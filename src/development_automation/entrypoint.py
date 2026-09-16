from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from development_automation.dispatcher import (
    DispatcherAction,
    DispatcherOutcome,
    GitHubEventDispatcher,
)
from development_automation.dispatcher_models import (
    CheckRunEvidence,
    DispatcherEvent,
    DispatcherPolicy,
    DispatchEventType,
    EventSource,
)
from development_automation.dispatcher_store import FileDispatcherStore
from development_automation.live_adapters import (
    GitHubCopilotCodingAgent,
    OpenAIReviewAdapter,
    OpenAIReviewOutcome,
    ProviderError,
    ReviewContext,
)
from development_automation.reducer import (
    ControlWorkflowProjection,
    TaskProjection,
    reduce_run_events,
)
from development_automation.schemas.v1 import (
    STOP_REASON_SECOND_TASK_CREATED,
    Actor,
    CorrespondenceDocument,
    DocumentStatus,
    DocumentType,
    LifecycleState,
    ReviewDecision,
    RunEventDocument,
    RunEventType,
)
from development_automation.storage import append_document, load_documents

BRANCH_INSTRUCTION_PATTERN = re.compile(
    r"(?:use|branch)\s+`?(feat/[A-Za-z0-9._/-]+)`?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EntrypointResult:
    action: str
    reason: str
    task_id: str | None = None
    persisted_paths: tuple[str, ...] = ()


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_timestamp(value: str | None) -> datetime:
    if value is None:
        return _now()
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(UTC)


def _event_created_at(payload: dict[str, Any]) -> datetime:
    for key in ("comment", "pull_request", "issue", "workflow_run"):
        value = payload.get(key)
        if not isinstance(value, dict):
            continue
        timestamp = value.get("updated_at") or value.get("created_at")
        if isinstance(timestamp, str):
            return _parse_timestamp(timestamp)
    return _now()


def _slugify_branch(title: str, issue_number: int) -> str:
    branch = BRANCH_INSTRUCTION_PATTERN.search(title)
    if branch is not None:
        return branch.group(1)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", title.lower()).strip("-")
    slug = slug[:48] or f"task-{issue_number:04d}"
    return f"feat/{slug}"


def _message_id(prefix: str, *parts: object) -> str:
    digest = sha256(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:16]
    return f"msg-{prefix}-{digest}"


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


class PortableDispatcherEntrypoint:
    def __init__(
        self,
        *,
        control_root: Path,
        policy: DispatcherPolicy,
        webhook_secret: str,
        coding_agent: Any,
        reviewer: Any,
        store: FileDispatcherStore | None = None,
    ) -> None:
        self._control_root = control_root
        self._policy = policy
        self._store = store or FileDispatcherStore(control_root)
        self._coding_agent = coding_agent
        self._reviewer = reviewer
        self._dispatcher = GitHubEventDispatcher(
            control_root=control_root,
            policy=policy,
            webhook_secret=webhook_secret,
            coding_agent=coding_agent,
            store=self._store,
        )

    def _projection(self) -> ControlWorkflowProjection:
        runs_dir = self._control_root / "runs"
        if not runs_dir.exists():
            return reduce_run_events([])
        events = [
            document
            for document in load_documents(runs_dir)
            if isinstance(document, RunEventDocument)
        ]
        return reduce_run_events(events)

    def _task_by_branch(self, branch: str, state: LifecycleState) -> TaskProjection | None:
        projection = self._projection()
        matches = [
            task
            for task in projection.tasks.values()
            if task.branch == branch and task.state == state
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def _latest_message_body(self, task_id: str, document_type: DocumentType) -> str:
        messages_dir = self._control_root / "messages"
        if not messages_dir.exists():
            return ""
        latest: CorrespondenceDocument | None = None
        for document in load_documents(messages_dir):
            if not isinstance(document, CorrespondenceDocument):
                continue
            if document.task_id != task_id or document.type != document_type:
                continue
            if latest is None or document.created_at > latest.created_at:
                latest = document
        return latest.body if latest is not None else ""

    def _message_exists(self, message_id: str) -> bool:
        messages_dir = self._control_root / "messages"
        if not messages_dir.exists():
            return False
        for document in load_documents(messages_dir):
            if isinstance(document, CorrespondenceDocument) and document.message_id == message_id:
                return True
        return False

    def _append(
        self,
        relative_directory: str,
        document: CorrespondenceDocument | RunEventDocument,
    ) -> str:
        path = append_document(self._control_root, relative_directory, document)
        return str(path.relative_to(self._control_root.parent))

    def _create_task_from_issue(self, payload: dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
        issue = payload.get("issue")
        if not isinstance(issue, dict):
            raise ValueError("issues event payload is missing issue")
        issue_number = int(issue["number"])
        title = str(issue["title"])
        body = str(issue.get("body") or "")
        created_at = _parse_timestamp(issue.get("created_at"))
        task_id = f"task-{issue_number:04d}"
        branch = _slugify_branch(body or title, issue_number)

        projection = self._projection()
        if task_id in projection.tasks:
            return task_id, projection.tasks[task_id].branch, ()

        instruction_body = f"# {title}\n\n{body.strip()}\n"
        message = CorrespondenceDocument(
            message_id=_message_id("issue", issue_number, "instruction"),
            task_id=task_id,
            from_actor=Actor.ERIC,
            to_actor=Actor.COPILOT,
            type=DocumentType.TASK_INSTRUCTION,
            status=DocumentStatus.PENDING,
            branch=branch,
            created_at=created_at,
            body=instruction_body,
        )
        run_event = RunEventDocument(
            message_id=_message_id("issue", issue_number, "created"),
            task_id=task_id,
            from_actor=Actor.SYSTEM,
            to_actor=Actor.GITHUB,
            branch=branch,
            created_at=created_at,
            body=f"# Run event\n\nTask created from GitHub issue #{issue_number}.\n",
            event_type=RunEventType.TASK_CREATED,
            lifecycle_state=LifecycleState.READY_FOR_COPILOT,
        )
        return task_id, branch, (self._append("messages", message), self._append("runs", run_event))

    def _build_dispatch_event(
        self,
        *,
        event_name: str,
        payload: dict[str, Any],
        delivery_id: str,
        source: str,
        signature_sha256: str | None,
        actions_authenticated: bool,
        actions_workflow_ref: str | None,
        task_id: str,
        branch: str,
        attempt: int,
    ) -> DispatcherEvent:
        repository = payload["repository"]["full_name"]
        actor = payload["sender"]["login"]
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        pull_request = payload.get("pull_request")
        issue_comment = payload.get("comment")
        return DispatcherEvent(
            delivery_id=delivery_id,
            event_type=event_name,
            source=source,
            repository=repository,
            actor=actor,
            event_action=payload.get("action"),
            task_id=task_id,
            attempt=attempt,
            branch=branch,
            pull_request_number=(
                int(pull_request["number"])
                if isinstance(pull_request, dict) and "number" in pull_request
                else None
            ),
            head_sha=(
                str(pull_request["head"]["sha"])
                if isinstance(pull_request, dict)
                else str(payload.get("after")) if payload.get("after") else None
            ),
            comment_body=(
                str(issue_comment.get("body")) if isinstance(issue_comment, dict) else None
            ),
            raw_body=body,
            signature_sha256=signature_sha256,
            actions_authenticated=actions_authenticated,
            actions_workflow_ref=actions_workflow_ref,
            payload=payload,
        )

    def _persist_dispatch(
        self,
        task_id: str,
        branch: str,
        attempt: int,
        outcome: DispatcherOutcome,
        created_at: datetime,
    ) -> tuple[str, ...]:
        if outcome.intent_id is None:
            return ()
        intent = self._store.get_intent(outcome.intent_id)
        provider_run_id = outcome.provider_run_id or (intent.provider_run_id if intent else None)
        if provider_run_id is None:
            return ()
        run_event = RunEventDocument(
            message_id=_message_id("dispatch", task_id, attempt, provider_run_id),
            task_id=task_id,
            from_actor=Actor.GITHUB,
            to_actor=Actor.COPILOT,
            branch=branch,
            created_at=created_at + timedelta(seconds=1),
            attempt=attempt,
            body="# Run event\n\nGitHub dispatched the coding task to Copilot.\n",
            event_type=RunEventType.COPILOT_DISPATCHED,
            lifecycle_state=LifecycleState.COPILOT_RUNNING,
            provider_run_id=provider_run_id,
        )
        return (self._append("runs", run_event),)

    def _persist_copilot_result(self, payload: dict[str, Any]) -> tuple[str, ...]:
        pull_request = payload.get("pull_request")
        if not isinstance(pull_request, dict):
            return ()
        sender = payload.get("sender")
        if not isinstance(sender, dict) or sender.get("login") not in {
            "Copilot",
            "copilot-swe-agent",
        }:
            return ()
        head = pull_request.get("head")
        if not isinstance(head, dict):
            return ()
        branch = str(head["ref"])
        task = self._task_by_branch(branch, LifecycleState.COPILOT_RUNNING)
        if task is None:
            return ()
        head_sha = str(head["sha"])
        pr_number = int(pull_request["number"])
        pr_url = str(pull_request["html_url"])
        created_at = _parse_timestamp(
            pull_request.get("updated_at") or pull_request.get("created_at")
        )
        message = CorrespondenceDocument(
            message_id=_message_id("pr", pr_number, head_sha, "result"),
            task_id=task.task_id,
            from_actor=Actor.COPILOT,
            to_actor=Actor.GITHUB,
            type=DocumentType.TASK_RESULT,
            status=DocumentStatus.FINAL,
            branch=branch,
            created_at=created_at,
            attempt=task.current_attempt,
            pull_request_url=pr_url,
            commit_sha=head_sha,
            body=(
                f"# Pull request\n\n"
                f"- PR: {pr_url}\n"
                f"- Title: {pull_request['title']}\n"
                f"- Head SHA: `{head_sha}`\n"
            ),
        )
        run_event = RunEventDocument(
            message_id=_message_id("pr", pr_number, head_sha, "run"),
            task_id=task.task_id,
            from_actor=Actor.COPILOT,
            to_actor=Actor.GITHUB,
            branch=branch,
            created_at=created_at,
            attempt=task.current_attempt,
            pull_request_url=pr_url,
            commit_sha=head_sha,
            body="# Run event\n\nCopilot result recorded from pull request evidence.\n",
            event_type=RunEventType.COPILOT_RESULT_RECORDED,
            lifecycle_state=LifecycleState.WAITING_FOR_CI,
            head_sha=head_sha,
            provider_run_id=f"pr:{pr_number}",
            verified_diff_summary=str(pull_request["title"]),
            progress_verified=True,
        )
        return (
            self._append("messages", message),
            self._append("runs", run_event),
        )

    @staticmethod
    def _checks_from_payload(
        event_name: str,
        payload: dict[str, Any],
    ) -> tuple[CheckRunEvidence, ...]:
        if event_name == DispatchEventType.CHECK_RUN:
            check_run = payload.get("check_run")
            if not isinstance(check_run, dict):
                return ()
            head_sha = str(check_run["head_sha"])
            workflow_ref = None
            app = check_run.get("app")
            if isinstance(app, dict):
                workflow_ref = app.get("slug")
            return (
                CheckRunEvidence(
                    name=str(check_run["name"]),
                    status=str(check_run["status"]),
                    conclusion=(
                        str(check_run["conclusion"])
                        if check_run.get("conclusion") is not None
                        else None
                    ),
                    head_sha=head_sha,
                    workflow_ref=(
                        str(payload.get("workflow_ref"))
                        if payload.get("workflow_ref")
                        else workflow_ref
                    ),
                ),
            )
        check_runs = payload.get("checks")
        if not isinstance(check_runs, list):
            return ()
        evidence: list[CheckRunEvidence] = []
        for check in check_runs:
            if not isinstance(check, dict):
                continue
            evidence.append(
                CheckRunEvidence(
                    name=str(check["name"]),
                    status=str(check["status"]),
                    conclusion=(
                        str(check["conclusion"]) if check.get("conclusion") is not None else None
                    ),
                    head_sha=str(check["head_sha"]),
                    workflow_ref=(
                        str(check["workflow_ref"])
                        if check.get("workflow_ref") is not None
                        else None
                    ),
                )
            )
        return tuple(evidence)

    def _ci_dispatch_event(
        self,
        *,
        event_name: str,
        payload: dict[str, Any],
        delivery_id: str,
        source: str,
        signature_sha256: str | None,
        actions_authenticated: bool,
        actions_workflow_ref: str | None,
    ) -> DispatcherEvent | None:
        projection = self._projection()
        checks = self._checks_from_payload(event_name, payload)
        task = self._resolve_ci_task(projection, payload, checks)
        if task is None:
            return None
        issue = payload.get("pull_request")
        pull_request_number = task.pull_request_number
        if isinstance(issue, dict) and "number" in issue:
            pull_request_number = int(issue["number"])
        head_sha = None
        if checks:
            head_sha = checks[0].head_sha
        elif event_name == DispatchEventType.WORKFLOW_RUN:
            workflow_run = payload.get("workflow_run")
            if isinstance(workflow_run, dict) and workflow_run.get("head_sha"):
                head_sha = str(workflow_run["head_sha"])
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        return DispatcherEvent(
            delivery_id=delivery_id,
            event_type=event_name,
            source=source,
            repository=payload["repository"]["full_name"],
            actor=payload["sender"]["login"],
            event_action=payload.get("action"),
            task_id=task.task_id,
            attempt=task.current_attempt,
            branch=task.branch,
            pull_request_number=pull_request_number,
            head_sha=head_sha,
            checks=checks,
            raw_body=body,
            signature_sha256=signature_sha256,
            actions_authenticated=actions_authenticated,
            actions_workflow_ref=actions_workflow_ref,
            payload=payload,
        )

    def _resolve_ci_task(
        self,
        projection: ControlWorkflowProjection,
        payload: dict[str, Any],
        checks: tuple[CheckRunEvidence, ...],
    ) -> TaskProjection | None:
        pull_request_number: int | None = None
        branch: str | None = None
        head_sha: str | None = checks[0].head_sha if checks else None

        pull_request = payload.get("pull_request")
        if isinstance(pull_request, dict):
            number = pull_request.get("number")
            head = pull_request.get("head")
            if isinstance(number, int):
                pull_request_number = number
            if isinstance(head, dict) and isinstance(head.get("ref"), str):
                branch = str(head["ref"])

        workflow_run = payload.get("workflow_run")
        if isinstance(workflow_run, dict):
            number = workflow_run.get("pull_requests")
            if isinstance(number, list) and number:
                first_pr = number[0]
                if isinstance(first_pr, dict) and isinstance(first_pr.get("number"), int):
                    pull_request_number = int(first_pr["number"])
            if isinstance(workflow_run.get("head_branch"), str):
                branch = str(workflow_run["head_branch"])
            if isinstance(workflow_run.get("head_sha"), str):
                head_sha = str(workflow_run["head_sha"])

        candidates = [
            task
            for task in projection.tasks.values()
            if task.state
            in {
                LifecycleState.WAITING_FOR_CI,
                LifecycleState.WAITING_FOR_OPENAI_REVIEW,
            }
        ]
        if pull_request_number is not None:
            candidates = [
                task for task in candidates if task.pull_request_number == pull_request_number
            ]
        if branch is not None:
            candidates = [task for task in candidates if task.branch == branch]
        if head_sha is not None and len(candidates) > 1:
            candidates = [
                task
                for task in candidates
                if task.expected_head_sha == head_sha or task.head_sha == head_sha
            ]
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _persist_ci_and_review(
        self,
        *,
        event: DispatcherEvent,
        outcome: DispatcherOutcome,
    ) -> tuple[str, ...]:
        if outcome.action != DispatcherAction.CI_READY:
            return ()
        if event.task_id is None:
            return ()
        projection = self._projection()
        task = projection.tasks[event.task_id]
        if event.head_sha is None:
            return ()
        event_created_at = _event_created_at(event.payload)
        persisted: list[str] = []
        if task.state == LifecycleState.WAITING_FOR_CI:
            ci_event = RunEventDocument(
                message_id=_message_id("ci", task.task_id, task.current_attempt, event.head_sha),
                task_id=task.task_id,
                from_actor=Actor.CI,
                to_actor=Actor.GITHUB,
                branch=task.branch,
                created_at=event_created_at,
                attempt=task.current_attempt,
                expected_head_sha=event.head_sha,
                pull_request_url=(
                    f"https://github.com/{event.repository}/pull/{task.pull_request_number}"
                    if task.pull_request_number is not None
                    else None
                ),
                commit_sha=event.head_sha,
                body="# Run event\n\nExact-head CI evidence recorded.\n",
                event_type=RunEventType.CI_EVIDENCE_RECORDED,
                lifecycle_state=LifecycleState.WAITING_FOR_OPENAI_REVIEW,
                provider_run_id=outcome.provider_run_id,
                ci_conclusion="success",
                ci_check_names=tuple(check.name for check in event.checks),
            )
            persisted.append(self._append("runs", ci_event))

        review_intent, claimed = self._store.claim_intent(
            dedupe_key=(
                f"review:{event.repository}:{task.task_id}:{task.current_attempt}:{event.head_sha}"
            ),
            operation="openai-review",
            task_id=task.task_id,
            head_sha=event.head_sha,
            branch=task.branch,
        )
        if not claimed and review_intent.status == "completed":
            return tuple(persisted)
        if review_intent.status == "running":
            return tuple(persisted)
        running_review = self._store.transition_intent(
            review_intent.intent_id,
            "running",
            expected_status=review_intent.status,
            expected_version=review_intent.version,
        )

        try:
            pull_request = self._coding_agent.get_pull_request(task.pull_request_number or 0)
            control_documents = {
                name: (self._control_root / name).read_text(encoding="utf-8")
                for name in (
                    "GOAL.md",
                    "ARCHITECTURE.md",
                    "CURRENT_STATE.md",
                    "DECISIONS.md",
                )
            }
            review_context = ReviewContext(
                repository=event.repository,
                task_id=task.task_id,
                attempt=task.current_attempt,
                branch=task.branch,
                task_message=self._latest_message_body(task.task_id, DocumentType.TASK_INSTRUCTION),
                result_message=self._latest_message_body(task.task_id, DocumentType.TASK_RESULT),
                pull_request=pull_request,
                pull_request_diff=self._coding_agent.get_pull_request_diff(
                    task.pull_request_number or 0
                ),
                ci_summary=tuple(
                    f"{check.name}:{check.status}:{check.conclusion}:{check.head_sha}"
                    for check in event.checks
                ),
                control_documents=control_documents,
            )
            review_outcome = self._reviewer.review(review_context)
        except ProviderError:
            self._store.transition_intent(
                running_review.intent_id,
                "claimed",
                expected_status="running",
                expected_version=running_review.version,
            )
            raise

        self._store.transition_intent(
            running_review.intent_id,
            "completed",
            expected_status="running",
            expected_version=running_review.version,
            provider_run_id=review_outcome.provider_run_id,
        )
        persisted.extend(
            self._persist_review(
                task,
                event.repository,
                event.head_sha,
                review_outcome,
                event_created_at,
            )
        )
        return tuple(persisted)

    def _persist_review(
        self,
        task: TaskProjection,
        repository: str,
        head_sha: str,
        review_outcome: OpenAIReviewOutcome,
        created_at: datetime,
    ) -> list[str]:
        persisted: list[str] = []
        result = review_outcome.result
        review_message = CorrespondenceDocument(
            message_id=_message_id("review", task.task_id, task.current_attempt, head_sha),
            task_id=task.task_id,
            from_actor=Actor.OPENAI,
            to_actor=Actor.GITHUB,
            type=DocumentType.REVIEW_DECISION,
            status=DocumentStatus.FINAL,
            branch=task.branch,
            created_at=created_at + timedelta(seconds=1),
            attempt=task.current_attempt,
            expected_head_sha=head_sha,
            pull_request_url=(
                f"https://github.com/{repository}/pull/{task.pull_request_number}"
                if task.pull_request_number is not None
                else None
            ),
            commit_sha=head_sha,
            body=(
                f"# Review decision\n\n"
                f"- Decision: `{result.decision}`\n"
                f"- Summary: {result.summary}\n\n"
                f"## Rationale\n\n{result.rationale}\n"
            ),
        )
        persisted.append(self._append("messages", review_message))
        review_event = RunEventDocument(
            message_id=_message_id("review", task.task_id, task.current_attempt, head_sha, "run"),
            task_id=task.task_id,
            from_actor=Actor.OPENAI,
            to_actor=Actor.GITHUB,
            branch=task.branch,
            created_at=created_at + timedelta(seconds=2),
            attempt=task.current_attempt,
            expected_head_sha=head_sha,
            pull_request_url=(
                f"https://github.com/{repository}/pull/{task.pull_request_number}"
                if task.pull_request_number is not None
                else None
            ),
            commit_sha=head_sha,
            body="# Run event\n\nOpenAI review decision recorded.\n",
            event_type=RunEventType.OPENAI_REVIEW_RECORDED,
            lifecycle_state=(
                LifecycleState.READY_FOR_COPILOT
                if result.decision == ReviewDecision.FIX_REQUIRED
                else LifecycleState.PAUSED
                if result.decision
                in {ReviewDecision.BLOCKED, ReviewDecision.HUMAN_DECISION_REQUIRED}
                else LifecycleState.COMPLETED
            ),
            provider_run_id=review_outcome.provider_run_id,
            review_decision=result.decision,
        )
        persisted.append(self._append("runs", review_event))
        if result.decision != ReviewDecision.NEXT_TASK:
            return persisted

        next_task_number = int(task.task_id.split("-")[1]) + 1
        next_task_id = f"task-{next_task_number:04d}"
        next_branch = _slugify_branch(result.next_task_title or next_task_id, next_task_number)
        next_intent, claimed = self._store.claim_intent(
            dedupe_key=f"next-task:{repository}:{task.task_id}:{task.current_attempt}:{next_task_id}",
            operation="create-next-task",
            task_id=next_task_id,
            head_sha=head_sha,
            branch=next_branch,
        )
        next_message = CorrespondenceDocument(
            message_id=_message_id("follow-up", task.task_id, next_task_id),
            task_id=next_task_id,
            from_actor=Actor.GITHUB,
            to_actor=Actor.COPILOT,
            type=DocumentType.TASK_INSTRUCTION,
            status=DocumentStatus.PENDING,
            branch=next_branch,
            created_at=created_at + timedelta(seconds=3),
            body=f"# {result.next_task_title}\n\n{result.next_task_body}\n",
        )
        if claimed or not self._message_exists(next_message.message_id):
            persisted.append(self._append("messages", next_message))
        if claimed or next_intent.status == "claimed":
            running_next = self._store.transition_intent(
                next_intent.intent_id,
                "running",
                expected_status=next_intent.status,
                expected_version=next_intent.version,
            )
            try:
                created = self._coding_agent.create_or_update_task(
                    task_id=next_task_id,
                    branch=next_branch,
                    correlation_id=next_intent.correlation_id,
                    title=result.next_task_title or next_task_id,
                    body=result.next_task_body or "",
                    dispatch=False,
                )
            except ProviderError:
                self._store.transition_intent(
                    running_next.intent_id,
                    "claimed",
                    expected_status="running",
                    expected_version=running_next.version,
                )
                raise
            self._store.transition_intent(
                running_next.intent_id,
                "completed",
                expected_status="running",
                expected_version=running_next.version,
                provider_run_id=created.provider_run_id,
            )

        next_event = RunEventDocument(
            message_id=_message_id("follow-up", task.task_id, next_task_id, "run"),
            task_id=task.task_id,
            from_actor=Actor.GITHUB,
            to_actor=Actor.SYSTEM,
            branch=task.branch,
            created_at=created_at + timedelta(seconds=4),
            attempt=task.current_attempt,
            expected_head_sha=head_sha,
            pull_request_url=(
                f"https://github.com/{repository}/pull/{task.pull_request_number}"
                if task.pull_request_number is not None
                else None
            ),
            commit_sha=head_sha,
            body="# Run event\n\nSecond task created and hard stop reached.\n",
            event_type=RunEventType.NEXT_TASK_CREATED,
            lifecycle_state=LifecycleState.COMPLETED,
            next_task_id=next_task_id,
            stop_reason=STOP_REASON_SECOND_TASK_CREATED,
        )
        persisted.append(self._append("runs", next_event))
        return persisted

    def handle_event(
        self,
        *,
        event_name: str,
        payload: dict[str, Any],
        delivery_id: str,
        source: str = EventSource.ACTIONS,
        signature_sha256: str | None = None,
        actions_authenticated: bool = True,
        actions_workflow_ref: str | None = None,
    ) -> EntrypointResult:
        sender = payload.get("sender")
        actor = sender.get("login") if isinstance(sender, dict) else None
        if isinstance(actor, str) and actor in self._policy.ignored_actors:
            return EntrypointResult(action=DispatcherAction.NOOP, reason="ignored self event")

        persisted: list[str] = []
        if event_name == DispatchEventType.ISSUES:
            task_id, branch, created_paths = self._create_task_from_issue(payload)
            persisted.extend(created_paths)
            attempt = self._projection().tasks[task_id].current_attempt
            created_at = _event_created_at(payload)
            dispatch_event = self._build_dispatch_event(
                event_name=event_name,
                payload=payload,
                delivery_id=delivery_id,
                source=source,
                signature_sha256=signature_sha256,
                actions_authenticated=actions_authenticated,
                actions_workflow_ref=actions_workflow_ref,
                task_id=task_id,
                branch=branch,
                attempt=attempt,
            )
            outcome = self._dispatcher.process_event(dispatch_event)
            if outcome.action == DispatcherAction.DISPATCHED:
                persisted.extend(
                    self._persist_dispatch(
                        task_id,
                        branch,
                        attempt,
                        outcome,
                        created_at,
                    )
                )
            return EntrypointResult(
                action=outcome.action,
                reason=outcome.reason,
                task_id=task_id,
                persisted_paths=tuple(persisted),
            )

        if event_name == DispatchEventType.ISSUE_COMMENT:
            issue = payload.get("issue")
            if not isinstance(issue, dict):
                return EntrypointResult(
                    action=DispatcherAction.NOOP,
                    reason="missing issue context",
                )
            task_id = f"task-{int(issue['number']):04d}"
            projection = self._projection()
            task = projection.tasks.get(task_id)
            if task is None:
                return EntrypointResult(action=DispatcherAction.NOOP, reason="unknown task id")
            created_at = _event_created_at(payload)
            dispatch_event = self._build_dispatch_event(
                event_name=event_name,
                payload=payload,
                delivery_id=delivery_id,
                source=source,
                signature_sha256=signature_sha256,
                actions_authenticated=actions_authenticated,
                actions_workflow_ref=actions_workflow_ref,
                task_id=task_id,
                branch=task.branch,
                attempt=task.current_attempt,
            )
            outcome = self._dispatcher.process_event(dispatch_event)
            if outcome.action == DispatcherAction.DISPATCHED:
                persisted.extend(
                    self._persist_dispatch(
                        task_id,
                        task.branch,
                        task.current_attempt,
                        outcome,
                        created_at,
                    )
                )
            return EntrypointResult(
                action=outcome.action,
                reason=outcome.reason,
                task_id=task_id,
                persisted_paths=tuple(persisted),
            )

        if event_name == DispatchEventType.PULL_REQUEST:
            persisted.extend(self._persist_copilot_result(payload))
            return EntrypointResult(
                action=DispatcherAction.DISPATCHED if persisted else DispatcherAction.NOOP,
                reason="pull request result recorded" if persisted else "no matching running task",
                task_id=None,
                persisted_paths=tuple(persisted),
            )

        if event_name in {DispatchEventType.CHECK_RUN, DispatchEventType.WORKFLOW_RUN}:
            ci_event = self._ci_dispatch_event(
                event_name=event_name,
                payload=payload,
                delivery_id=delivery_id,
                source=source,
                signature_sha256=signature_sha256,
                actions_authenticated=actions_authenticated,
                actions_workflow_ref=actions_workflow_ref,
            )
            if ci_event is None:
                return EntrypointResult(action=DispatcherAction.NOOP, reason="no active task")
            try:
                outcome = self._dispatcher.process_event(ci_event)
                if outcome.action == DispatcherAction.NOOP:
                    retry_outcome = self._review_retry_outcome(ci_event)
                    if retry_outcome is not None:
                        outcome = retry_outcome
                persisted.extend(self._persist_ci_and_review(event=ci_event, outcome=outcome))
            except ProviderError as exc:
                return EntrypointResult(
                    action=DispatcherAction.NOOP,
                    reason=str(exc),
                    task_id=ci_event.task_id,
                    persisted_paths=tuple(persisted),
                )
            return EntrypointResult(
                action=outcome.action,
                reason=outcome.reason,
                task_id=ci_event.task_id,
                persisted_paths=tuple(persisted),
            )

        if event_name == DispatchEventType.PUSH:
            return EntrypointResult(
                action=DispatcherAction.NOOP,
                reason="push events are audit-only",
            )

        return EntrypointResult(action=DispatcherAction.NOOP, reason="unsupported event type")

    def _review_retry_outcome(self, event: DispatcherEvent) -> DispatcherOutcome | None:
        if event.task_id is None or event.head_sha is None:
            return None
        projection = self._projection()
        task = projection.tasks.get(event.task_id)
        if task is None or task.state != LifecycleState.WAITING_FOR_OPENAI_REVIEW:
            return None
        if task.expected_head_sha != event.head_sha:
            return None
        if task.pull_request_number != event.pull_request_number:
            return None
        return DispatcherOutcome(
            action=DispatcherAction.CI_READY,
            reason="retrying pending OpenAI review",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Development automation GitHub dispatcher entrypoint",
    )
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--event-path", required=True)
    parser.add_argument("--control-root", default="control")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source", default=EventSource.ACTIONS)
    parser.add_argument("--delivery-id", required=True)
    parser.add_argument("--webhook-secret", default="")
    parser.add_argument("--actions-workflow-ref", default=None)
    parser.add_argument("--openai-model", default="gpt-5-mini")
    parser.add_argument(
        "--allowlisted-actors",
        nargs="+",
        default=None,
        help=(
            "Explicit GitHub actor allowlist. Falls back to "
            "DEVELOPMENT_AUTOMATION_ALLOWLISTED_ACTORS."
        ),
    )
    arguments = parser.parse_args(argv)

    payload = json.loads(Path(arguments.event_path).read_text(encoding="utf-8"))
    env_allowlisted_actors = os.environ.get("DEVELOPMENT_AUTOMATION_ALLOWLISTED_ACTORS")
    allowlisted_actors = (
        tuple(arguments.allowlisted_actors)
        if arguments.allowlisted_actors
        else _split_csv(env_allowlisted_actors) if env_allowlisted_actors else ()
    )
    if not allowlisted_actors:
        parser.error(
            "provide --allowlisted-actors or DEVELOPMENT_AUTOMATION_ALLOWLISTED_ACTORS"
        )
    policy = DispatcherPolicy(
        allowlisted_repositories=(arguments.repository,),
        allowlisted_actors=allowlisted_actors,
        expected_check_names=("ruff", "mypy", "pytest"),
        trusted_workflow_refs=("trusted/workflow.yml@refs/heads/main",),
        trusted_actions_refs=(
            (arguments.actions_workflow_ref,) if arguments.actions_workflow_ref else ()
        ),
    )
    coding_agent = GitHubCopilotCodingAgent(
        repository=arguments.repository,
        token=os.environ["COPILOT_AGENT_TOKEN"],
    )
    reviewer = OpenAIReviewAdapter(
        api_key=os.environ["OPENAI_API_KEY"],
        model=arguments.openai_model,
    )
    entrypoint = PortableDispatcherEntrypoint(
        control_root=Path(arguments.control_root),
        policy=policy,
        webhook_secret=arguments.webhook_secret,
        coding_agent=coding_agent,
        reviewer=reviewer,
    )
    outcome = entrypoint.handle_event(
        event_name=arguments.event_name,
        payload=payload,
        delivery_id=arguments.delivery_id,
        source=arguments.source,
        signature_sha256=None,
        actions_authenticated=arguments.source == EventSource.ACTIONS,
        actions_workflow_ref=arguments.actions_workflow_ref,
    )
    print(
        json.dumps(
            {
                "action": outcome.action,
                "reason": outcome.reason,
                "task_id": outcome.task_id,
                "persisted_paths": list(outcome.persisted_paths),
            },
            sort_keys=True,
        )
    )
    return 0
