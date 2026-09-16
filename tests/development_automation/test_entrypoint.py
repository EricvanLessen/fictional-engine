from __future__ import annotations

from pathlib import Path

from development_automation.dispatcher import DispatcherAction
from development_automation.dispatcher_models import DispatcherPolicy
from development_automation.dispatcher_store import FileDispatcherStore
from development_automation.entrypoint import PortableDispatcherEntrypoint
from development_automation.live_adapters import (
    OpenAIReviewOutcome,
    OpenAIReviewResult,
    ProviderRateLimitError,
    PullRequestMetadata,
)
from development_automation.schemas.v1 import (
    STOP_REASON_SECOND_TASK_CREATED,
    DocumentType,
    RunEventDocument,
)
from development_automation.storage import load_documents


class FakeCodingAgent:
    def __init__(self) -> None:
        self.dispatch_calls: list[tuple[str | None, str | None, str, str | None]] = []
        self.follow_up_calls: list[tuple[str, str, str, bool]] = []

    def run(
        self,
        *,
        task_id: str | None,
        head_sha: str | None,
        correlation_id: str,
        branch: str | None,
    ) -> object:
        self.dispatch_calls.append((task_id, head_sha, correlation_id, branch))
        return type(
            "DispatchResult",
            (),
            {
                "correlation_id": correlation_id,
                "status": "running",
                "provider_run_id": "issue:101",
            },
        )()

    def reconcile(self, correlation_id: str) -> None:
        return None

    def create_or_update_task(
        self,
        *,
        task_id: str,
        branch: str,
        correlation_id: str,
        title: str,
        body: str,
        dispatch: bool,
    ) -> object:
        self.follow_up_calls.append((task_id, branch, correlation_id, dispatch))
        return type(
            "TaskResult",
            (),
            {
                "correlation_id": correlation_id,
                "status": "completed",
                "provider_run_id": "issue:202",
            },
        )()

    def get_pull_request(self, pull_request_number: int) -> PullRequestMetadata:
        return PullRequestMetadata(
            number=pull_request_number,
            title="Implement Increment C",
            body="PR body",
            html_url=f"https://github.com/EricvanLessen/fictional-engine/pull/{pull_request_number}",
            head_sha="0123456789abcdef0123456789abcdef01234567",
            base_ref="main",
            head_ref="feat/development-automation-live-adapters",
        )

    def get_pull_request_diff(self, pull_request_number: int) -> str:
        return f"diff --git a/pr-{pull_request_number} b/pr-{pull_request_number}\n+change\n"


class FakeReviewer:
    def __init__(self) -> None:
        self.calls = 0
        self.raise_error = False

    def review(self, context: object) -> OpenAIReviewOutcome:
        self.calls += 1
        if self.raise_error:
            raise ProviderRateLimitError("OpenAI review request was rate limited")
        return OpenAIReviewOutcome(
            provider_run_id="resp_123",
            result=OpenAIReviewResult(
                decision="NEXT_TASK",
                summary="Follow-up task required.",
                rationale="The proof should stop after creating the second task.",
                next_task_title="Increment D notification path",
                next_task_body="Add notifications after the Increment C hard stop.",
            ),
        )


def _policy() -> DispatcherPolicy:
    return DispatcherPolicy(
        allowlisted_repositories=("EricvanLessen/fictional-engine",),
        allowlisted_actors=("EricvanLessen", "ci-bot", "Copilot", "copilot-swe-agent"),
        expected_check_names=("ruff", "mypy", "pytest"),
        trusted_workflow_refs=("trusted/workflow.yml@refs/heads/main",),
        trusted_actions_refs=("trusted/dispatcher.yml@refs/heads/main",),
    )


def _seed_control_documents(control_root: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    control_root.mkdir(parents=True, exist_ok=True)
    for name in ("GOAL.md", "ARCHITECTURE.md", "CURRENT_STATE.md", "DECISIONS.md"):
        (control_root / name).write_text((repo_root / "control" / name).read_text(encoding="utf-8"))


def _entrypoint(
    tmp_path: Path,
    reviewer: FakeReviewer | None = None,
) -> tuple[PortableDispatcherEntrypoint, FakeCodingAgent, FakeReviewer]:
    control_root = tmp_path / "control"
    (control_root / "messages").mkdir(parents=True, exist_ok=True)
    (control_root / "runs").mkdir(parents=True, exist_ok=True)
    _seed_control_documents(control_root)
    agent = FakeCodingAgent()
    fake_reviewer = reviewer or FakeReviewer()
    return (
        PortableDispatcherEntrypoint(
            control_root=control_root,
            policy=_policy(),
            webhook_secret="secret",
            coding_agent=agent,
            reviewer=fake_reviewer,
            store=FileDispatcherStore(control_root),
        ),
        agent,
        fake_reviewer,
    )


def _issue_payload(actor: str = "EricvanLessen") -> dict[str, object]:
    return {
        "action": "opened",
        "repository": {"full_name": "EricvanLessen/fictional-engine"},
        "sender": {"login": actor},
        "issue": {
            "number": 9,
            "title": "Increment C live adapters",
            "body": "Please use branch `feat/development-automation-live-adapters`.",
            "created_at": "2026-09-16T15:00:00Z",
        },
    }


def _pull_request_payload(
    head_sha: str = "0123456789abcdef0123456789abcdef01234567",
) -> dict[str, object]:
    return {
        "action": "opened",
        "repository": {"full_name": "EricvanLessen/fictional-engine"},
        "sender": {"login": "Copilot"},
        "pull_request": {
            "number": 10,
            "title": "Implement Increment C",
            "html_url": "https://github.com/EricvanLessen/fictional-engine/pull/10",
            "head": {
                "ref": "feat/development-automation-live-adapters",
                "sha": head_sha,
            },
            "created_at": "2026-09-16T15:10:00Z",
            "updated_at": "2026-09-16T15:11:00Z",
        },
    }


def _check_payload(head_sha: str = "0123456789abcdef0123456789abcdef01234567") -> dict[str, object]:
    return {
        "action": "completed",
        "repository": {"full_name": "EricvanLessen/fictional-engine"},
        "sender": {"login": "ci-bot"},
        "pull_request": {"number": 10},
        "workflow_run": {
            "head_sha": head_sha,
            "created_at": "2026-09-16T15:20:00Z",
            "updated_at": "2026-09-16T15:21:00Z",
        },
        "checks": [
            {
                "name": "ruff",
                "status": "completed",
                "conclusion": "success",
                "head_sha": head_sha,
                "workflow_ref": "trusted/workflow.yml@refs/heads/main",
            },
            {
                "name": "mypy",
                "status": "completed",
                "conclusion": "success",
                "head_sha": head_sha,
                "workflow_ref": "trusted/workflow.yml@refs/heads/main",
            },
            {
                "name": "pytest",
                "status": "completed",
                "conclusion": "success",
                "head_sha": head_sha,
                "workflow_ref": "trusted/workflow.yml@refs/heads/main",
            },
        ],
    }


def test_issue_replay_does_not_double_dispatch(tmp_path: Path) -> None:
    entrypoint, agent, _ = _entrypoint(tmp_path)

    first = entrypoint.handle_event(
        event_name="issues",
        payload=_issue_payload(),
        delivery_id="delivery-1",
        actions_workflow_ref="trusted/dispatcher.yml@refs/heads/main",
    )
    second = entrypoint.handle_event(
        event_name="issues",
        payload=_issue_payload(),
        delivery_id="delivery-1",
        actions_workflow_ref="trusted/dispatcher.yml@refs/heads/main",
    )

    assert first.action == DispatcherAction.DISPATCHED
    assert second.action == DispatcherAction.NOOP
    assert len(agent.dispatch_calls) == 1


def test_self_event_is_suppressed(tmp_path: Path) -> None:
    entrypoint, agent, _ = _entrypoint(tmp_path)

    result = entrypoint.handle_event(
        event_name="issues",
        payload=_issue_payload(actor="github-actions[bot]"),
        delivery_id="delivery-self",
        actions_workflow_ref="trusted/dispatcher.yml@refs/heads/main",
    )

    assert result.action == DispatcherAction.NOOP
    assert "ignored" in result.reason
    assert agent.dispatch_calls == []


def test_single_cycle_stops_after_second_task_created(tmp_path: Path) -> None:
    entrypoint, agent, reviewer = _entrypoint(tmp_path)

    issue_result = entrypoint.handle_event(
        event_name="issues",
        payload=_issue_payload(),
        delivery_id="delivery-issue",
        actions_workflow_ref="trusted/dispatcher.yml@refs/heads/main",
    )
    pr_result = entrypoint.handle_event(
        event_name="pull_request",
        payload=_pull_request_payload(),
        delivery_id="delivery-pr",
        actions_workflow_ref="trusted/dispatcher.yml@refs/heads/main",
    )
    ci_result = entrypoint.handle_event(
        event_name="workflow_run",
        payload=_check_payload(),
        delivery_id="delivery-ci",
        actions_workflow_ref="trusted/dispatcher.yml@refs/heads/main",
    )

    assert issue_result.action == DispatcherAction.DISPATCHED
    assert pr_result.action == DispatcherAction.DISPATCHED
    assert ci_result.action == DispatcherAction.CI_READY
    assert reviewer.calls == 1
    assert len(agent.dispatch_calls) == 1
    assert len(agent.follow_up_calls) == 1
    assert agent.follow_up_calls[0][0] == "task-0010"
    assert agent.follow_up_calls[0][3] is False

    run_documents = [
        document
        for document in load_documents(tmp_path / "control" / "runs")
        if isinstance(document, RunEventDocument)
    ]
    assert any(document.event_type == "NEXT_TASK_CREATED" for document in run_documents)
    projection = entrypoint._projection()
    assert projection.stop_reason == STOP_REASON_SECOND_TASK_CREATED
    assert projection.task_order == ("task-0009", "task-0010")

    second_task_comment = entrypoint.handle_event(
        event_name="issue_comment",
        payload={
            "action": "created",
            "repository": {"full_name": "EricvanLessen/fictional-engine"},
            "sender": {"login": "EricvanLessen"},
            "issue": {"number": 10},
            "comment": {"body": "task-0010 please continue"},
        },
        delivery_id="delivery-second-task",
        actions_workflow_ref="trusted/dispatcher.yml@refs/heads/main",
    )
    assert second_task_comment.action == DispatcherAction.NOOP
    assert "SECOND_TASK_CREATED" in second_task_comment.reason
    assert len(agent.dispatch_calls) == 1


def test_ci_head_binding_prevents_review(tmp_path: Path) -> None:
    entrypoint, _, reviewer = _entrypoint(tmp_path)
    entrypoint.handle_event(
        event_name="issues",
        payload=_issue_payload(),
        delivery_id="delivery-issue",
        actions_workflow_ref="trusted/dispatcher.yml@refs/heads/main",
    )
    entrypoint.handle_event(
        event_name="pull_request",
        payload=_pull_request_payload(),
        delivery_id="delivery-pr",
        actions_workflow_ref="trusted/dispatcher.yml@refs/heads/main",
    )

    result = entrypoint.handle_event(
        event_name="workflow_run",
        payload=_check_payload(head_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        delivery_id="delivery-ci",
        actions_workflow_ref="trusted/dispatcher.yml@refs/heads/main",
    )

    assert result.action == DispatcherAction.NOOP
    assert "persisted expected head" in result.reason
    assert reviewer.calls == 0


def test_review_rate_limit_is_retryable_without_persisting_review(tmp_path: Path) -> None:
    reviewer = FakeReviewer()
    reviewer.raise_error = True
    entrypoint, _, _ = _entrypoint(tmp_path, reviewer=reviewer)
    entrypoint.handle_event(
        event_name="issues",
        payload=_issue_payload(),
        delivery_id="delivery-issue",
        actions_workflow_ref="trusted/dispatcher.yml@refs/heads/main",
    )
    entrypoint.handle_event(
        event_name="pull_request",
        payload=_pull_request_payload(),
        delivery_id="delivery-pr",
        actions_workflow_ref="trusted/dispatcher.yml@refs/heads/main",
    )

    limited = entrypoint.handle_event(
        event_name="workflow_run",
        payload=_check_payload(),
        delivery_id="delivery-ci-1",
        actions_workflow_ref="trusted/dispatcher.yml@refs/heads/main",
    )

    assert limited.action == DispatcherAction.NOOP
    assert "rate limited" in limited.reason
    message_documents = load_documents(tmp_path / "control" / "messages")
    assert all(
        not (getattr(document, "type", None) == DocumentType.REVIEW_DECISION)
        for document in message_documents
    )

    reviewer.raise_error = False
    retried = entrypoint.handle_event(
        event_name="workflow_run",
        payload=_check_payload(),
        delivery_id="delivery-ci-2",
        actions_workflow_ref="trusted/dispatcher.yml@refs/heads/main",
    )

    assert retried.action == DispatcherAction.CI_READY
    message_documents = load_documents(tmp_path / "control" / "messages")
    assert any(
        getattr(document, "type", None) == DocumentType.REVIEW_DECISION
        for document in message_documents
    )
