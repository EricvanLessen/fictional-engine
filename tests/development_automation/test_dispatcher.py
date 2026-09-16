from __future__ import annotations

import hmac
import threading
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path

import pytest

from development_automation.dispatcher import DispatcherAction, GitHubEventDispatcher
from development_automation.dispatcher_models import (
    CheckRunEvidence,
    DispatcherEvent,
    DispatcherPolicy,
    DispatchEventType,
    EventSource,
)
from development_automation.markdown import parse_control_document
from development_automation.mock_agents import MockAgentResult, MockCodingAgent
from development_automation.schemas.v1 import RunEventDocument
from development_automation.storage import append_document


def _signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()


def _policy() -> DispatcherPolicy:
    return DispatcherPolicy(
        allowlisted_repositories=("EricvanLessen/fictional-engine",),
        allowlisted_actors=("EricvanLessen", "openai", "ci-bot"),
        expected_check_names=("ruff", "mypy", "pytest"),
        trusted_workflow_refs=("trusted/workflow.yml@refs/heads/main",),
        trusted_actions_refs=("trusted/dispatcher.yml@refs/heads/main",),
    )


def _dispatcher(
    tmp_path: Path,
    agent: MockCodingAgent,
    secret: str = "secret",
    post_provider_accept_hook: Callable[[str, str], None] | None = None,
) -> GitHubEventDispatcher:
    control_root = tmp_path / "control"
    (control_root / "runs").mkdir(parents=True, exist_ok=True)
    (control_root / "messages").mkdir(parents=True, exist_ok=True)
    return GitHubEventDispatcher(
        control_root=control_root,
        policy=_policy(),
        webhook_secret=secret,
        coding_agent=agent,
        post_provider_accept_hook=post_provider_accept_hook,
    )


def _load_fixture_run_event(name: str) -> RunEventDocument:
    fixture_path = Path("fixtures/development_automation") / name
    parsed = parse_control_document(fixture_path.read_text(encoding="utf-8"))
    assert isinstance(parsed, RunEventDocument)
    return parsed


def _append_run_events_for_state(control_root: Path, state: str) -> None:
    sequence = {
        "ready": ["run_task_created.md"],
        "waiting_for_ci": [
            "run_task_created.md",
            "run_dispatched.md",
            "run_result.md",
        ],
        "completed": [
            "run_task_created.md",
            "run_dispatched.md",
            "run_result.md",
            "run_ci.md",
            "run_review.md",
        ],
    }
    for fixture_name in sequence[state]:
        append_document(control_root, "runs", _load_fixture_run_event(fixture_name))


def _dispatch_event(
    *,
    delivery_id: str,
    event_type: str,
    source: str,
    body: bytes,
    signature: str,
    task_id: str = "task-0042",
    head_sha: str = "a" * 40,
    branch: str = "feat/development-automation-protocol",
    event_action: str | None = "opened",
    attempt: int = 1,
    comment_body: str | None = None,
) -> DispatcherEvent:
    return DispatcherEvent(
        delivery_id=delivery_id,
        event_type=event_type,
        source=source,
        repository="EricvanLessen/fictional-engine",
        actor="EricvanLessen",
        event_action=event_action,
        task_id=task_id,
        attempt=attempt,
        branch=branch,
        head_sha=head_sha,
        comment_body=comment_body,
        raw_body=body,
        signature_sha256=signature,
    )


def _ci_checks(head_sha: str) -> tuple[CheckRunEvidence, ...]:
    return (
        CheckRunEvidence(
            name="ruff",
            status="completed",
            conclusion="success",
            head_sha=head_sha,
            workflow_ref="trusted/workflow.yml@refs/heads/main",
        ),
        CheckRunEvidence(
            name="mypy",
            status="completed",
            conclusion="success",
            head_sha=head_sha,
            workflow_ref="trusted/workflow.yml@refs/heads/main",
        ),
        CheckRunEvidence(
            name="pytest",
            status="completed",
            conclusion="success",
            head_sha=head_sha,
            workflow_ref="trusted/workflow.yml@refs/heads/main",
        ),
    )


def test_webhook_hmac_and_delivery_replay_dedup(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    _append_run_events_for_state(tmp_path / "control", "ready")

    body = b'{"action":"opened"}'
    event = _dispatch_event(
        delivery_id="delivery-1",
        event_type=DispatchEventType.PULL_REQUEST,
        source=EventSource.WEBHOOK,
        body=body,
        signature=_signature("secret", body),
        event_action="opened",
    )

    first = dispatcher.process_event(event)
    second = dispatcher.process_event(event)

    assert first.action == DispatcherAction.DISPATCHED
    assert second.action == DispatcherAction.NOOP
    assert len(agent.dispatch_calls) == 1


def test_dispatch_rejects_unknown_task(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    _append_run_events_for_state(tmp_path / "control", "ready")

    body = b'{"action":"opened"}'
    event = _dispatch_event(
        delivery_id="delivery-unknown-task",
        event_type=DispatchEventType.PULL_REQUEST,
        source=EventSource.WEBHOOK,
        body=body,
        signature=_signature("secret", body),
        task_id="task-9999",
        event_action="opened",
    )

    outcome = dispatcher.process_event(event)

    assert outcome.action == DispatcherAction.NOOP
    assert "unknown task" in outcome.reason
    assert len(agent.dispatch_calls) == 0


def test_dispatch_rejects_completed_task_state(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    _append_run_events_for_state(tmp_path / "control", "completed")

    body = b'{"action":"reopened"}'
    event = _dispatch_event(
        delivery_id="delivery-completed",
        event_type=DispatchEventType.PULL_REQUEST,
        source=EventSource.WEBHOOK,
        body=body,
        signature=_signature("secret", body),
        event_action="reopened",
    )

    outcome = dispatcher.process_event(event)

    assert outcome.action == DispatcherAction.NOOP
    assert "not dispatch-eligible" in outcome.reason
    assert len(agent.dispatch_calls) == 0


def test_dispatch_rejects_waiting_for_ci_task_state(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    _append_run_events_for_state(tmp_path / "control", "waiting_for_ci")

    body = b'{"action":"synchronize"}'
    event = _dispatch_event(
        delivery_id="delivery-waiting",
        event_type=DispatchEventType.PULL_REQUEST,
        source=EventSource.WEBHOOK,
        body=body,
        signature=_signature("secret", body),
        event_action="synchronize",
    )

    outcome = dispatcher.process_event(event)

    assert outcome.action == DispatcherAction.NOOP
    assert "not dispatch-eligible" in outcome.reason
    assert len(agent.dispatch_calls) == 0


def test_dispatch_rejects_branch_mismatch(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    _append_run_events_for_state(tmp_path / "control", "ready")

    body = b'{"action":"opened"}'
    event = _dispatch_event(
        delivery_id="delivery-branch-mismatch",
        event_type=DispatchEventType.PULL_REQUEST,
        source=EventSource.WEBHOOK,
        body=body,
        signature=_signature("secret", body),
        branch="feat/wrong-branch",
        event_action="opened",
    )

    outcome = dispatcher.process_event(event)

    assert outcome.action == DispatcherAction.NOOP
    assert "branch" in outcome.reason
    assert len(agent.dispatch_calls) == 0


def test_dispatch_rejects_stale_attempt(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    _append_run_events_for_state(tmp_path / "control", "ready")

    body = b'{"action":"opened"}'
    event = _dispatch_event(
        delivery_id="delivery-stale-attempt",
        event_type=DispatchEventType.PULL_REQUEST,
        source=EventSource.WEBHOOK,
        body=body,
        signature=_signature("secret", body),
        attempt=2,
        event_action="opened",
    )

    outcome = dispatcher.process_event(event)

    assert outcome.action == DispatcherAction.NOOP
    assert "stale task attempt" in outcome.reason
    assert len(agent.dispatch_calls) == 0


def test_dispatch_rejects_unrelated_issue_comment(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    _append_run_events_for_state(tmp_path / "control", "ready")

    body = b'{"action":"created"}'
    event = _dispatch_event(
        delivery_id="delivery-unrelated-comment",
        event_type=DispatchEventType.ISSUE_COMMENT,
        source=EventSource.WEBHOOK,
        body=body,
        signature=_signature("secret", body),
        event_action="created",
        comment_body="Looks good to me",
    )

    outcome = dispatcher.process_event(event)

    assert outcome.action == DispatcherAction.NOOP
    assert "not bound" in outcome.reason
    assert len(agent.dispatch_calls) == 0


def test_semantically_different_comments_are_not_deduped(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    _append_run_events_for_state(tmp_path / "control", "ready")

    body = b'{"action":"created"}'
    first = _dispatch_event(
        delivery_id="delivery-comment-1",
        event_type=DispatchEventType.ISSUE_COMMENT,
        source=EventSource.WEBHOOK,
        body=body,
        signature=_signature("secret", body),
        event_action="created",
        comment_body="task-0042 please apply blocker fix one",
    )
    second = _dispatch_event(
        delivery_id="delivery-comment-2",
        event_type=DispatchEventType.ISSUE_COMMENT,
        source=EventSource.WEBHOOK,
        body=body,
        signature=_signature("secret", body),
        event_action="created",
        comment_body="task-0042 please apply blocker fix two",
    )

    first_outcome = dispatcher.process_event(first)
    second_outcome = dispatcher.process_event(second)

    assert first_outcome.action == DispatcherAction.DISPATCHED
    assert second_outcome.action == DispatcherAction.DISPATCHED
    assert len(agent.dispatch_calls) == 2


def test_ci_gate_requires_projection_expected_head(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    _append_run_events_for_state(tmp_path / "control", "waiting_for_ci")

    body = b'{"action":"completed"}'
    expected_head = "0123456789abcdef0123456789abcdef01234567"
    checks = _ci_checks(expected_head)
    event = DispatcherEvent(
        delivery_id="delivery-3",
        event_type=DispatchEventType.CHECK_RUN,
        source=EventSource.WEBHOOK,
        repository="EricvanLessen/fictional-engine",
        actor="ci-bot",
        event_action="completed",
        task_id="task-0042",
        attempt=1,
        head_sha=expected_head,
        branch="feat/development-automation-protocol",
        checks=checks,
        raw_body=body,
        signature_sha256=_signature("secret", body),
    )

    outcome = dispatcher.process_event(event)

    assert outcome.action == DispatcherAction.CI_READY


def test_ci_rejects_unknown_task(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    _append_run_events_for_state(tmp_path / "control", "waiting_for_ci")

    body = b'{"action":"completed"}'
    expected_head = "0123456789abcdef0123456789abcdef01234567"
    event = DispatcherEvent(
        delivery_id="delivery-ci-unknown",
        event_type=DispatchEventType.CHECK_RUN,
        source=EventSource.WEBHOOK,
        repository="EricvanLessen/fictional-engine",
        actor="ci-bot",
        event_action="completed",
        task_id="task-9999",
        attempt=1,
        head_sha=expected_head,
        branch="feat/development-automation-protocol",
        checks=_ci_checks(expected_head),
        raw_body=body,
        signature_sha256=_signature("secret", body),
    )

    outcome = dispatcher.process_event(event)

    assert outcome.action == DispatcherAction.NOOP
    assert "unknown task" in outcome.reason


def test_ci_rejects_event_head_mismatch_vs_persisted_expected_head(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    _append_run_events_for_state(tmp_path / "control", "waiting_for_ci")

    body = b'{"action":"completed"}'
    persisted_head = "0123456789abcdef0123456789abcdef01234567"
    event_head = "a" * 40
    event = DispatcherEvent(
        delivery_id="delivery-ci-head-mismatch",
        event_type=DispatchEventType.CHECK_RUN,
        source=EventSource.WEBHOOK,
        repository="EricvanLessen/fictional-engine",
        actor="ci-bot",
        event_action="completed",
        task_id="task-0042",
        attempt=1,
        head_sha=event_head,
        branch="feat/development-automation-protocol",
        checks=_ci_checks(event_head),
        raw_body=body,
        signature_sha256=_signature("secret", body),
    )

    outcome = dispatcher.process_event(event)

    assert outcome.action == DispatcherAction.NOOP
    assert "persisted expected head" in outcome.reason
    assert persisted_head != event_head


def test_ci_rejects_stale_attempt(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    _append_run_events_for_state(tmp_path / "control", "waiting_for_ci")

    body = b'{"action":"completed"}'
    expected_head = "0123456789abcdef0123456789abcdef01234567"
    event = DispatcherEvent(
        delivery_id="delivery-ci-attempt",
        event_type=DispatchEventType.CHECK_RUN,
        source=EventSource.WEBHOOK,
        repository="EricvanLessen/fictional-engine",
        actor="ci-bot",
        event_action="completed",
        task_id="task-0042",
        attempt=2,
        head_sha=expected_head,
        branch="feat/development-automation-protocol",
        checks=_ci_checks(expected_head),
        raw_body=body,
        signature_sha256=_signature("secret", body),
    )

    outcome = dispatcher.process_event(event)

    assert outcome.action == DispatcherAction.NOOP
    assert "stale task attempt" in outcome.reason


def test_ci_rejects_correct_checks_for_wrong_branch(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    _append_run_events_for_state(tmp_path / "control", "waiting_for_ci")

    body = b'{"action":"completed"}'
    expected_head = "0123456789abcdef0123456789abcdef01234567"
    event = DispatcherEvent(
        delivery_id="delivery-ci-branch",
        event_type=DispatchEventType.CHECK_RUN,
        source=EventSource.WEBHOOK,
        repository="EricvanLessen/fictional-engine",
        actor="ci-bot",
        event_action="completed",
        task_id="task-0042",
        attempt=1,
        head_sha=expected_head,
        branch="feat/other-branch",
        checks=_ci_checks(expected_head),
        raw_body=body,
        signature_sha256=_signature("secret", body),
    )

    outcome = dispatcher.process_event(event)

    assert outcome.action == DispatcherAction.NOOP
    assert "branch" in outcome.reason


def test_untrusted_actions_and_fork_repo_are_ignored(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    _append_run_events_for_state(tmp_path / "control", "ready")

    actions_event = DispatcherEvent(
        delivery_id="delivery-4",
        event_type=DispatchEventType.ISSUES,
        source=EventSource.ACTIONS,
        repository="EricvanLessen/fictional-engine",
        actor="EricvanLessen",
        event_action="opened",
        task_id="task-0042",
        attempt=1,
        branch="feat/development-automation-protocol",
        actions_authenticated=True,
        actions_workflow_ref="untrusted/ref.yml@refs/heads/main",
    )
    fork_event = DispatcherEvent(
        delivery_id="delivery-5",
        event_type=DispatchEventType.ISSUE_COMMENT,
        source=EventSource.WEBHOOK,
        repository="someone/fork",
        actor="EricvanLessen",
        event_action="created",
        task_id="task-0042",
        attempt=1,
        branch="feat/development-automation-protocol",
        comment_body="task-0042 run it",
        raw_body=b"{}",
        signature_sha256=_signature("secret", b"{}"),
    )

    actions_outcome = dispatcher.process_event(actions_event)
    fork_outcome = dispatcher.process_event(fork_event)

    assert actions_outcome.action == DispatcherAction.NOOP
    assert "not trusted" in actions_outcome.reason
    assert fork_outcome.action == DispatcherAction.NOOP
    assert "allowlisted" in fork_outcome.reason
    assert len(agent.dispatch_calls) == 0


def test_second_task_stop_boundary_blocks_dispatch(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    fixtures_dir = Path("fixtures/development_automation")

    for path in sorted(fixtures_dir.glob("run_*.md")):
        parsed = parse_control_document(path.read_text(encoding="utf-8"))
        assert isinstance(parsed, RunEventDocument)
        append_document(tmp_path / "control", "runs", parsed)

    body = b'{"action":"opened"}'
    event = _dispatch_event(
        delivery_id="delivery-6",
        event_type=DispatchEventType.PULL_REQUEST,
        source=EventSource.WEBHOOK,
        body=body,
        signature=_signature("secret", body),
        event_action="opened",
    )
    outcome = dispatcher.process_event(event)

    assert outcome.action == DispatcherAction.NOOP
    assert "SECOND_TASK_CREATED" in outcome.reason
    assert len(agent.dispatch_calls) == 0


def test_housekeeping_actor_ignored(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    _append_run_events_for_state(tmp_path / "control", "ready")

    body = b'{"action":"opened"}'
    event = DispatcherEvent(
        delivery_id="delivery-7",
        event_type=DispatchEventType.PULL_REQUEST,
        source=EventSource.WEBHOOK,
        repository="EricvanLessen/fictional-engine",
        actor="github-actions[bot]",
        event_action="opened",
        task_id="task-0042",
        attempt=1,
        branch="feat/development-automation-protocol",
        head_sha="a" * 40,
        raw_body=body,
        signature_sha256=_signature("secret", body),
    )

    outcome = dispatcher.process_event(event)

    assert outcome.action == DispatcherAction.NOOP
    assert "ignored" in outcome.reason
    assert len(agent.dispatch_calls) == 0


def test_recovery_leases_allow_one_worker_to_resume(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher_a = _dispatcher(tmp_path, agent)
    dispatcher_b = _dispatcher(tmp_path, agent)

    store = dispatcher_a._store
    intent, _ = store.claim_intent(
        dedupe_key="dispatch:recover-1",
        operation="dispatch-task",
        task_id="task-0042",
        head_sha="a" * 40,
        branch="feat/development-automation-protocol",
    )

    outcomes: list[tuple[str, str | None]] = []

    def resume(dispatcher: GitHubEventDispatcher) -> None:
        for outcome in dispatcher.resume_unfinished_intents():
            outcomes.append((outcome.action, outcome.intent_id))

    thread_a = threading.Thread(target=resume, args=(dispatcher_a,))
    thread_b = threading.Thread(target=resume, args=(dispatcher_b,))
    thread_a.start()
    thread_b.start()
    thread_a.join()
    thread_b.join()

    assert len(agent.dispatch_calls) == 1
    assert any(action == DispatcherAction.DISPATCHED for action, _ in outcomes)
    assert all(intent_id == intent.intent_id for _, intent_id in outcomes)


def test_recovery_marks_uncertain_running_intent_unknown(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    store = dispatcher._store
    intent, _ = store.claim_intent(
        dedupe_key="dispatch:recover-2",
        operation="dispatch-task",
        task_id="task-0042",
        head_sha="a" * 40,
        branch="feat/development-automation-protocol",
    )
    store.transition_intent(
        intent.intent_id,
        "running",
        expected_status="claimed",
        expected_version=intent.version,
    )

    outcomes = dispatcher.resume_unfinished_intents()

    assert outcomes
    assert outcomes[0].action == DispatcherAction.BLOCKED
    assert "UNKNOWN" in outcomes[0].reason
    assert len(agent.dispatch_calls) == 0


def test_crash_after_provider_acceptance_does_not_redispatch_on_resume(tmp_path: Path) -> None:
    agent = MockCodingAgent()

    def crash_after_accept(_: str, __: str) -> None:
        raise RuntimeError("simulated crash after provider acceptance")

    dispatcher = _dispatcher(
        tmp_path,
        agent,
        post_provider_accept_hook=crash_after_accept,
    )
    _append_run_events_for_state(tmp_path / "control", "ready")

    body = b'{"action":"opened"}'
    event = _dispatch_event(
        delivery_id="delivery-crash-window",
        event_type=DispatchEventType.PULL_REQUEST,
        source=EventSource.WEBHOOK,
        body=body,
        signature=_signature("secret", body),
        event_action="opened",
    )

    with pytest.raises(RuntimeError):
        dispatcher.process_event(event)

    resumed = _dispatcher(tmp_path, agent)
    outcomes = resumed.resume_unfinished_intents()

    assert len(agent.dispatch_calls) == 1
    assert outcomes
    assert outcomes[0].action == DispatcherAction.BLOCKED
    assert "UNKNOWN" in outcomes[0].reason


def test_recovery_reconciles_running_intent_without_redispatch(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    store = dispatcher._store
    intent, _ = store.claim_intent(
        dedupe_key="dispatch:recover-3",
        operation="dispatch-task",
        task_id="task-0042",
        head_sha="a" * 40,
        branch="feat/development-automation-protocol",
    )
    running = store.transition_intent(
        intent.intent_id,
        "running",
        expected_status="claimed",
        expected_version=intent.version,
    )
    agent.reconcile_results[intent.correlation_id] = MockAgentResult(
        correlation_id=intent.correlation_id,
        status="completed",
        provider_run_id="mock-existing-1",
    )

    outcomes = dispatcher.resume_unfinished_intents()

    assert outcomes
    assert outcomes[0].reason == "reconciled as completed"
    assert len(agent.dispatch_calls) == 0
    assert running.intent_id == outcomes[0].intent_id
