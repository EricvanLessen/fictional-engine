from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from development_automation.ci_gate import CIGateError, validate_ci_for_exact_head
from development_automation.dispatcher_models import (
    DispatcherEvent,
    DispatcherPolicy,
    DispatchEventType,
    EventSource,
    is_supported_event_type,
    verify_webhook_signature,
)
from development_automation.dispatcher_store import (
    TERMINAL_INTENT_STATES,
    FileDispatcherStore,
)
from development_automation.errors import DevelopmentAutomationError
from development_automation.markdown import parse_control_document
from development_automation.reducer import ControlWorkflowProjection, reduce_run_events
from development_automation.schemas.v1 import (
    STOP_REASON_SECOND_TASK_CREATED,
    LifecycleState,
    RunEventDocument,
)


class DispatcherPolicyError(DevelopmentAutomationError):
    """Raised when an event fails allowlist or transport policy checks."""


class DispatcherAction(str):
    NOOP = "NOOP"
    DISPATCHED = "DISPATCHED"
    CI_READY = "CI_READY"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class DispatcherOutcome:
    action: str
    reason: str
    intent_id: str | None = None


class GitHubEventDispatcher:
    def __init__(
        self,
        *,
        control_root: Path,
        policy: DispatcherPolicy,
        webhook_secret: str,
        coding_agent: Any,
        store: FileDispatcherStore | None = None,
        post_provider_accept_hook: Callable[[str, str], None] | None = None,
    ) -> None:
        self._control_root = control_root
        self._policy = policy
        self._webhook_secret = webhook_secret
        self._coding_agent = coding_agent
        self._store = store or FileDispatcherStore(control_root)
        self._post_provider_accept_hook = post_provider_accept_hook

    def _load_run_events(self) -> list[RunEventDocument]:
        runs_dir = self._control_root / "runs"
        if not runs_dir.exists():
            return []
        events: list[RunEventDocument] = []
        for path in sorted(runs_dir.glob("*.md")):
            parsed = parse_control_document(path.read_text(encoding="utf-8"))
            if isinstance(parsed, RunEventDocument):
                events.append(parsed)
        return events

    def _stop_boundary_reached(self) -> bool:
        run_events = self._load_run_events()
        if not run_events:
            return False
        return reduce_run_events(run_events).stop_reason == STOP_REASON_SECOND_TASK_CREATED

    def _load_projection(self) -> ControlWorkflowProjection:
        run_events = self._load_run_events()
        return reduce_run_events(run_events)

    def _validate_transport(self, event: DispatcherEvent) -> None:
        if event.source == EventSource.WEBHOOK:
            if len(event.raw_body) > self._policy.max_webhook_body_bytes:
                raise DispatcherPolicyError("webhook body exceeds max size")
            if not verify_webhook_signature(
                self._webhook_secret,
                event.raw_body,
                event.signature_sha256,
            ):
                raise DispatcherPolicyError("webhook signature is invalid")
            return

        if event.source == EventSource.ACTIONS:
            if not event.actions_authenticated:
                raise DispatcherPolicyError("actions event is unauthenticated")
            if (
                self._policy.trusted_actions_refs
                and event.actions_workflow_ref not in self._policy.trusted_actions_refs
            ):
                raise DispatcherPolicyError("actions workflow ref is not trusted")
            return

        raise DispatcherPolicyError(f"unknown event source {event.source!r}")

    def _validate_policy(self, event: DispatcherEvent) -> None:
        if not is_supported_event_type(event.event_type):
            raise DispatcherPolicyError("event type is not supported")
        if event.repository not in self._policy.allowlisted_repositories:
            raise DispatcherPolicyError("repository is not allowlisted")
        if event.actor in self._policy.ignored_actors:
            raise DispatcherPolicyError("event actor is ignored")
        if event.actor not in self._policy.allowlisted_actors:
            raise DispatcherPolicyError("event actor is not allowlisted")

    def _validate_dispatch_action(self, event: DispatcherEvent) -> None:
        eligible_actions: dict[str, set[str | None]] = {
            DispatchEventType.PUSH: {None, "push"},
            DispatchEventType.PULL_REQUEST: {
                "opened",
                "reopened",
                "synchronize",
                "ready_for_review",
            },
            DispatchEventType.ISSUES: {"opened", "edited", "reopened"},
            DispatchEventType.ISSUE_COMMENT: {"created", "edited"},
        }
        allowed = eligible_actions.get(event.event_type)
        if allowed is None:
            raise DispatcherPolicyError("event type is not eligible for dispatch")
        if event.event_action not in allowed:
            raise DispatcherPolicyError(
                f"event action {event.event_action!r} is not eligible for {event.event_type}"
            )
        if event.event_type == DispatchEventType.ISSUE_COMMENT:
            comment_body = (event.comment_body or "").lower()
            if event.task_id is None or event.task_id.lower() not in comment_body:
                raise DispatcherPolicyError("issue comment is not bound to the task ID")

    def _validate_task_binding_for_dispatch(self, event: DispatcherEvent) -> None:
        projection = self._load_projection()
        if event.task_id is None:
            raise DispatcherPolicyError("missing task id")
        task = projection.tasks.get(event.task_id)
        if task is None:
            raise DispatcherPolicyError("unknown task id")
        if task.state != LifecycleState.READY_FOR_COPILOT:
            raise DispatcherPolicyError(f"task state {task.state} is not dispatch-eligible")
        if event.branch != task.branch:
            raise DispatcherPolicyError("event branch does not match active task branch")
        if event.attempt is None:
            raise DispatcherPolicyError("missing task attempt")
        if event.attempt != task.current_attempt:
            raise DispatcherPolicyError("stale task attempt")

    def _validate_task_binding_for_ci(self, event: DispatcherEvent) -> str:
        projection = self._load_projection()
        if event.task_id is None:
            raise DispatcherPolicyError("missing task id")
        task = projection.tasks.get(event.task_id)
        if task is None:
            raise DispatcherPolicyError("unknown task id")
        if task.state != LifecycleState.WAITING_FOR_CI:
            raise DispatcherPolicyError(f"task state {task.state} is not CI-eligible")
        if event.branch != task.branch:
            raise DispatcherPolicyError("event branch does not match active task branch")
        if event.attempt is None:
            raise DispatcherPolicyError("missing task attempt")
        if event.attempt != task.current_attempt:
            raise DispatcherPolicyError("stale task attempt")
        if task.expected_head_sha is None:
            raise DispatcherPolicyError("task has no persisted expected head SHA")
        if event.head_sha != task.expected_head_sha:
            raise DispatcherPolicyError("event head SHA does not match persisted expected head")
        if task.pull_request_number is None:
            raise DispatcherPolicyError("task has no persisted pull request identity")
        if event.pull_request_number is None:
            raise DispatcherPolicyError("missing pull request number")
        if event.pull_request_number != task.pull_request_number:
            raise DispatcherPolicyError("event pull request number does not match persisted task")
        return task.expected_head_sha

    def _handle_dispatch_intent(self, event: DispatcherEvent) -> DispatcherOutcome:
        self._validate_dispatch_action(event)
        self._validate_task_binding_for_dispatch(event)
        dedupe_key = (
            f"dispatch:{event.repository}:{event.task_id}:{event.attempt}:{event.branch}"
        )
        intent, claimed = self._store.claim_intent(
            dedupe_key=dedupe_key,
            operation="dispatch-task",
            task_id=event.task_id,
            head_sha=event.head_sha,
            branch=event.branch,
        )
        if not claimed:
            return DispatcherOutcome(
                action=DispatcherAction.NOOP,
                reason="duplicate dispatch intent",
                intent_id=intent.intent_id,
            )

        running_intent = self._store.transition_intent(
            intent.intent_id,
            "running",
            expected_status="claimed",
            expected_version=intent.version,
        )
        result = self._coding_agent.run(
            task_id=event.task_id,
            head_sha=event.head_sha,
            correlation_id=intent.correlation_id,
            branch=event.branch,
        )

        acknowledged_intent = self._store.transition_intent(
            running_intent.intent_id,
            "running",
            expected_status="running",
            expected_version=running_intent.version,
            provider_run_id=result.provider_run_id,
        )
        if self._post_provider_accept_hook is not None:
            self._post_provider_accept_hook(acknowledged_intent.intent_id, result.provider_run_id)

        if result.status == "completed":
            self._store.transition_intent(
                acknowledged_intent.intent_id,
                "completed",
                expected_status="running",
                expected_version=acknowledged_intent.version,
            )
            return DispatcherOutcome(
                action=DispatcherAction.DISPATCHED,
                reason="mock agent completed",
                intent_id=intent.intent_id,
            )
        if result.status == "failed":
            self._store.transition_intent(
                acknowledged_intent.intent_id,
                "failed",
                expected_status="running",
                expected_version=acknowledged_intent.version,
            )
            return DispatcherOutcome(
                action=DispatcherAction.BLOCKED,
                reason="mock agent failed",
                intent_id=intent.intent_id,
            )
        self._store.transition_intent(
            acknowledged_intent.intent_id,
            "blocked",
            expected_status="running",
            expected_version=acknowledged_intent.version,
        )
        return DispatcherOutcome(
            action=DispatcherAction.BLOCKED,
            reason=f"unknown mock agent status {result.status!r}",
            intent_id=intent.intent_id,
        )

    def _handle_ci_intent(self, event: DispatcherEvent) -> DispatcherOutcome:
        if event.head_sha is None:
            return DispatcherOutcome(action=DispatcherAction.NOOP, reason="missing head sha")
        expected_head_sha = self._validate_task_binding_for_ci(event)
        validate_ci_for_exact_head(
            expected_head_sha=expected_head_sha,
            checks=event.checks,
            required_check_names=self._policy.expected_check_names,
            trusted_workflow_refs=self._policy.trusted_workflow_refs,
        )
        dedupe_key = f"ci-ready:{event.semantic_key()}"
        intent, claimed = self._store.claim_intent(
            dedupe_key=dedupe_key,
            operation="ci-ready",
            task_id=event.task_id,
            head_sha=event.head_sha,
            branch=event.branch,
        )
        if not claimed:
            return DispatcherOutcome(
                action=DispatcherAction.NOOP,
                reason="duplicate CI-ready intent",
                intent_id=intent.intent_id,
            )
        self._store.transition_intent(
            intent.intent_id,
            "completed",
            expected_status="claimed",
            expected_version=intent.version,
        )
        return DispatcherOutcome(
            action=DispatcherAction.CI_READY,
            reason="required CI checks succeeded on expected head",
            intent_id=intent.intent_id,
        )

    def process_event(self, event: DispatcherEvent) -> DispatcherOutcome:
        if self._stop_boundary_reached():
            return DispatcherOutcome(
                action=DispatcherAction.NOOP,
                reason="SECOND_TASK_CREATED stop boundary is active",
            )

        try:
            self._validate_transport(event)
            self._validate_policy(event)
        except DispatcherPolicyError as exc:
            return DispatcherOutcome(action=DispatcherAction.NOOP, reason=str(exc))

        if not self._store.remember_delivery(event.delivery_id):
            return DispatcherOutcome(action=DispatcherAction.NOOP, reason="duplicate delivery id")

        if event.event_type in {
            DispatchEventType.PUSH,
            DispatchEventType.PULL_REQUEST,
            DispatchEventType.ISSUES,
            DispatchEventType.ISSUE_COMMENT,
        }:
            try:
                return self._handle_dispatch_intent(event)
            except DispatcherPolicyError as exc:
                return DispatcherOutcome(action=DispatcherAction.NOOP, reason=str(exc))

        if event.event_type in {DispatchEventType.WORKFLOW_RUN, DispatchEventType.CHECK_RUN}:
            try:
                return self._handle_ci_intent(event)
            except (CIGateError, DispatcherPolicyError) as exc:
                return DispatcherOutcome(action=DispatcherAction.NOOP, reason=str(exc))

        return DispatcherOutcome(action=DispatcherAction.NOOP, reason="unsupported event type")

    def resume_unfinished_intents(self) -> list[DispatcherOutcome]:
        if self._stop_boundary_reached():
            return [
                DispatcherOutcome(
                    action=DispatcherAction.NOOP,
                    reason="SECOND_TASK_CREATED stop boundary is active",
                )
            ]

        outcomes: list[DispatcherOutcome] = []
        worker_id = f"dispatcher-recovery-{uuid.uuid4()}"

        while True:
            intent = self._store.lease_unfinished_intent(worker_id=worker_id, lease_seconds=120)
            if intent is None:
                break
            if intent.operation != "dispatch-task":
                continue
            reconciled = self._coding_agent.reconcile(intent.correlation_id)
            if reconciled is not None and reconciled.status in {"completed", "failed"}:
                next_state = "completed" if reconciled.status == "completed" else "failed"
                self._store.transition_intent(
                    intent.intent_id,
                    next_state,
                    expected_status=intent.status,
                    expected_version=intent.version,
                    provider_run_id=reconciled.provider_run_id,
                )
                outcomes.append(
                    DispatcherOutcome(
                        action=DispatcherAction.DISPATCHED,
                        reason=f"reconciled as {next_state}",
                        intent_id=intent.intent_id,
                    )
                )
                continue

            if intent.status in TERMINAL_INTENT_STATES:
                continue

            if intent.status == "running":
                self._store.transition_intent(
                    intent.intent_id,
                    "unknown",
                    expected_status="running",
                    expected_version=intent.version,
                )
                outcomes.append(
                    DispatcherOutcome(
                        action=DispatcherAction.BLOCKED,
                        reason="provider side effect uncertain; marked UNKNOWN for human review",
                        intent_id=intent.intent_id,
                    )
                )
                continue

            running_intent = self._store.transition_intent(
                intent.intent_id,
                "running",
                expected_status=intent.status,
                expected_version=intent.version,
            )
            result = self._coding_agent.run(
                task_id=intent.task_id,
                head_sha=intent.head_sha,
                correlation_id=intent.correlation_id,
                branch=intent.branch,
            )
            acknowledged_intent = self._store.transition_intent(
                running_intent.intent_id,
                "running",
                expected_status="running",
                expected_version=running_intent.version,
                provider_run_id=result.provider_run_id,
            )
            if result.status == "completed":
                self._store.transition_intent(
                    acknowledged_intent.intent_id,
                    "completed",
                    expected_status="running",
                    expected_version=acknowledged_intent.version,
                )
                outcomes.append(
                    DispatcherOutcome(
                        action=DispatcherAction.DISPATCHED,
                        reason="resumed and completed",
                        intent_id=intent.intent_id,
                    )
                )
            else:
                self._store.transition_intent(
                    acknowledged_intent.intent_id,
                    "failed",
                    expected_status="running",
                    expected_version=acknowledged_intent.version,
                )
                outcomes.append(
                    DispatcherOutcome(
                        action=DispatcherAction.BLOCKED,
                        reason="resumed and failed",
                        intent_id=intent.intent_id,
                    )
                )
        return outcomes