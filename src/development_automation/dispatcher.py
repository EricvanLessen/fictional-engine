from __future__ import annotations

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
from development_automation.reducer import reduce_run_events
from development_automation.schemas.v1 import STOP_REASON_SECOND_TASK_CREATED, RunEventDocument


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
    ) -> None:
        self._control_root = control_root
        self._policy = policy
        self._webhook_secret = webhook_secret
        self._coding_agent = coding_agent
        self._store = store or FileDispatcherStore(control_root)

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

    def _handle_dispatch_intent(self, event: DispatcherEvent) -> DispatcherOutcome:
        dedupe_key = f"dispatch:{event.semantic_key()}"
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

        self._store.transition_intent(intent.intent_id, "running")
        result = self._coding_agent.run(
            task_id=event.task_id,
            head_sha=event.head_sha,
            correlation_id=intent.correlation_id,
            branch=event.branch,
        )
        if result.status == "completed":
            self._store.transition_intent(intent.intent_id, "completed")
            return DispatcherOutcome(
                action=DispatcherAction.DISPATCHED,
                reason="mock agent completed",
                intent_id=intent.intent_id,
            )
        if result.status == "failed":
            self._store.transition_intent(intent.intent_id, "failed")
            return DispatcherOutcome(
                action=DispatcherAction.BLOCKED,
                reason="mock agent failed",
                intent_id=intent.intent_id,
            )
        self._store.transition_intent(intent.intent_id, "blocked")
        return DispatcherOutcome(
            action=DispatcherAction.BLOCKED,
            reason=f"unknown mock agent status {result.status!r}",
            intent_id=intent.intent_id,
        )

    def _handle_ci_intent(self, event: DispatcherEvent) -> DispatcherOutcome:
        if event.head_sha is None:
            return DispatcherOutcome(action=DispatcherAction.NOOP, reason="missing head sha")
        validate_ci_for_exact_head(
            expected_head_sha=event.head_sha,
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
        self._store.transition_intent(intent.intent_id, "completed")
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
            if event.task_id is None:
                return DispatcherOutcome(action=DispatcherAction.NOOP, reason="missing task id")
            return self._handle_dispatch_intent(event)

        if event.event_type in {DispatchEventType.WORKFLOW_RUN, DispatchEventType.CHECK_RUN}:
            try:
                return self._handle_ci_intent(event)
            except CIGateError as exc:
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
        for intent in self._store.list_unfinished_intents():
            if intent.operation != "dispatch-task":
                continue
            reconciled = self._coding_agent.reconcile(intent.correlation_id)
            if reconciled is not None and reconciled.status in {"completed", "failed"}:
                next_state = "completed" if reconciled.status == "completed" else "failed"
                self._store.transition_intent(intent.intent_id, next_state)
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

            self._store.transition_intent(intent.intent_id, "running")
            result = self._coding_agent.run(
                task_id=intent.task_id,
                head_sha=intent.head_sha,
                correlation_id=intent.correlation_id,
                branch=intent.branch,
            )
            if result.status == "completed":
                self._store.transition_intent(intent.intent_id, "completed")
                outcomes.append(
                    DispatcherOutcome(
                        action=DispatcherAction.DISPATCHED,
                        reason="resumed and completed",
                        intent_id=intent.intent_id,
                    )
                )
            else:
                self._store.transition_intent(intent.intent_id, "failed")
                outcomes.append(
                    DispatcherOutcome(
                        action=DispatcherAction.BLOCKED,
                        reason="resumed and failed",
                        intent_id=intent.intent_id,
                    )
                )
        return outcomes