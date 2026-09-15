from __future__ import annotations

import hmac
import threading
from hashlib import sha256
from pathlib import Path

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
) -> GitHubEventDispatcher:
    control_root = tmp_path / "control"
    (control_root / "runs").mkdir(parents=True, exist_ok=True)
    (control_root / "messages").mkdir(parents=True, exist_ok=True)
    return GitHubEventDispatcher(
        control_root=control_root,
        policy=_policy(),
        webhook_secret=secret,
        coding_agent=agent,
    )


def _dispatch_event(
    *,
    delivery_id: str,
    event_type: str,
    source: str,
    body: bytes,
    signature: str,
    task_id: str = "task-0042",
    head_sha: str = "a" * 40,
) -> DispatcherEvent:
    return DispatcherEvent(
        delivery_id=delivery_id,
        event_type=event_type,
        source=source,
        repository="EricvanLessen/fictional-engine",
        actor="EricvanLessen",
        task_id=task_id,
        branch="feat/development-automation-dispatcher",
        head_sha=head_sha,
        raw_body=body,
        signature_sha256=signature,
    )


def test_webhook_hmac_and_delivery_replay_dedup(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    body = b'{"action":"opened"}'
    event = _dispatch_event(
        delivery_id="delivery-1",
        event_type=DispatchEventType.PULL_REQUEST,
        source=EventSource.WEBHOOK,
        body=body,
        signature=_signature("secret", body),
    )

    first = dispatcher.process_event(event)
    second = dispatcher.process_event(event)

    assert first.action == DispatcherAction.DISPATCHED
    assert second.action == DispatcherAction.NOOP
    assert len(agent.dispatch_calls) == 1


def test_concurrent_duplicate_events_claim_one_intent(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    body = b'{"action":"synchronize"}'

    outcomes: list[str] = []

    def run(delivery_id: str) -> None:
        event = _dispatch_event(
            delivery_id=delivery_id,
            event_type=DispatchEventType.PULL_REQUEST,
            source=EventSource.WEBHOOK,
            body=body,
            signature=_signature("secret", body),
        )
        outcomes.append(dispatcher.process_event(event).action)

    thread_a = threading.Thread(target=run, args=("delivery-2a",))
    thread_b = threading.Thread(target=run, args=("delivery-2b",))
    thread_a.start()
    thread_b.start()
    thread_a.join()
    thread_b.join()

    assert sorted(outcomes) == [DispatcherAction.DISPATCHED, DispatcherAction.NOOP]
    assert len(agent.dispatch_calls) == 1


def test_recovery_resume_from_claimed_intent(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    store = dispatcher._store  # validated through public recovery behavior below.
    intent, _ = store.claim_intent(
        dedupe_key="dispatch:recover-1",
        operation="dispatch-task",
        task_id="task-0042",
        head_sha="a" * 40,
        branch="feat/development-automation-dispatcher",
    )

    outcomes = dispatcher.resume_unfinished_intents()

    assert outcomes
    assert outcomes[0].action == DispatcherAction.DISPATCHED
    assert outcomes[0].intent_id == intent.intent_id
    assert len(agent.dispatch_calls) == 1


def test_recovery_reconciles_running_intent_without_redispatch(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    store = dispatcher._store
    intent, _ = store.claim_intent(
        dedupe_key="dispatch:recover-2",
        operation="dispatch-task",
        task_id="task-0042",
        head_sha="a" * 40,
        branch="feat/development-automation-dispatcher",
    )
    store.transition_intent(intent.intent_id, "running")
    agent.reconcile_results[intent.correlation_id] = MockAgentResult(
        correlation_id=intent.correlation_id,
        status="completed",
        provider_run_id="mock-existing-1",
    )

    outcomes = dispatcher.resume_unfinished_intents()

    assert outcomes
    assert outcomes[0].reason == "reconciled as completed"
    assert len(agent.dispatch_calls) == 0


def test_ci_gate_requires_exact_head_and_trusted_workflow(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    body = b'{"action":"completed"}'
    checks = (
        CheckRunEvidence(
            name="ruff",
            status="completed",
            conclusion="success",
            head_sha="a" * 40,
            workflow_ref="trusted/workflow.yml@refs/heads/main",
        ),
        CheckRunEvidence(
            name="mypy",
            status="completed",
            conclusion="success",
            head_sha="a" * 40,
            workflow_ref="trusted/workflow.yml@refs/heads/main",
        ),
        CheckRunEvidence(
            name="pytest",
            status="completed",
            conclusion="success",
            head_sha="a" * 40,
            workflow_ref="trusted/workflow.yml@refs/heads/main",
        ),
    )
    event = DispatcherEvent(
        delivery_id="delivery-3",
        event_type=DispatchEventType.CHECK_RUN,
        source=EventSource.WEBHOOK,
        repository="EricvanLessen/fictional-engine",
        actor="ci-bot",
        task_id="task-0042",
        head_sha="a" * 40,
        branch="feat/development-automation-dispatcher",
        checks=checks,
        raw_body=body,
        signature_sha256=_signature("secret", body),
    )

    outcome = dispatcher.process_event(event)

    assert outcome.action == DispatcherAction.CI_READY


def test_untrusted_actions_and_fork_repo_are_ignored(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    actions_event = DispatcherEvent(
        delivery_id="delivery-4",
        event_type=DispatchEventType.ISSUES,
        source=EventSource.ACTIONS,
        repository="EricvanLessen/fictional-engine",
        actor="EricvanLessen",
        task_id="task-0042",
        branch="feat/development-automation-dispatcher",
        actions_authenticated=True,
        actions_workflow_ref="untrusted/ref.yml@refs/heads/main",
    )
    fork_event = DispatcherEvent(
        delivery_id="delivery-5",
        event_type=DispatchEventType.ISSUE_COMMENT,
        source=EventSource.WEBHOOK,
        repository="someone/fork",
        actor="EricvanLessen",
        task_id="task-0042",
        branch="feat/development-automation-dispatcher",
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
    )
    outcome = dispatcher.process_event(event)

    assert outcome.action == DispatcherAction.NOOP
    assert "SECOND_TASK_CREATED" in outcome.reason
    assert len(agent.dispatch_calls) == 0


def test_housekeeping_actor_ignored(tmp_path: Path) -> None:
    agent = MockCodingAgent()
    dispatcher = _dispatcher(tmp_path, agent)
    body = b'{"action":"opened"}'
    event = DispatcherEvent(
        delivery_id="delivery-7",
        event_type=DispatchEventType.PULL_REQUEST,
        source=EventSource.WEBHOOK,
        repository="EricvanLessen/fictional-engine",
        actor="github-actions[bot]",
        task_id="task-0042",
        branch="feat/development-automation-dispatcher",
        head_sha="a" * 40,
        raw_body=body,
        signature_sha256=_signature("secret", body),
    )

    outcome = dispatcher.process_event(event)

    assert outcome.action == DispatcherAction.NOOP
    assert "ignored" in outcome.reason
    assert len(agent.dispatch_calls) == 0