from __future__ import annotations

import random
from pathlib import Path

import pytest

from development_automation.errors import (
    InvalidTransitionError,
    StaleHeadError,
    StopBoundaryError,
    TaskLimitError,
)
from development_automation.markdown import parse_control_document
from development_automation.reducer import reduce_run_events
from development_automation.schemas.v1 import ReviewDecision, RunEventDocument


def _load_run_events() -> list[RunEventDocument]:
    fixture_dir = Path("fixtures/development_automation")
    run_events: list[RunEventDocument] = []
    for path in sorted(fixture_dir.glob("run_*.md")):
        document = parse_control_document(path.read_text(encoding="utf-8"))
        assert isinstance(document, RunEventDocument)
        run_events.append(document)
    return sorted(run_events, key=lambda event: (event.created_at.isoformat(), event.message_id))


def _updated_event(event: RunEventDocument, **changes: object) -> RunEventDocument:
    payload = event.model_dump(by_alias=True, mode="json")
    payload.update(changes)
    payload["body"] = event.body
    return RunEventDocument.model_validate(payload)


def test_duplicate_delivery_is_ignored_deterministically() -> None:
    run_events = _load_run_events()
    duplicate_input = [run_events[0], run_events[0], *run_events[1:]]

    projection = reduce_run_events(duplicate_input)

    assert projection.stop_reason == "SECOND_TASK_CREATED"
    assert projection.task_order == ("task-0042", "task-0043")


def test_invalid_transition_is_rejected() -> None:
    run_events = _load_run_events()

    with pytest.raises(InvalidTransitionError):
        reduce_run_events([run_events[1]])


def test_restart_reconstruction_is_order_independent() -> None:
    shuffled_events = _load_run_events()
    random.Random(7).shuffle(shuffled_events)

    projection = reduce_run_events(shuffled_events)

    assert projection.task_order == ("task-0042", "task-0043")
    assert projection.tasks["task-0042"].state.name == "COMPLETED"
    assert projection.tasks["task-0042"].head_sha == "0123456789abcdef0123456789abcdef01234567"


def test_stale_head_is_rejected() -> None:
    run_events = _load_run_events()
    stale_ci_event = _updated_event(run_events[3], expected_head_sha="abcdef0")

    with pytest.raises(StaleHeadError):
        reduce_run_events([run_events[0], run_events[1], run_events[2], stale_ci_event])


def test_repair_attempt_limit_is_enforced() -> None:
    created, dispatched, result, ci, review, *_ = _load_run_events()
    fix_review = _updated_event(review, review_decision=ReviewDecision.FIX_REQUIRED)
    dispatched_attempt_2 = _updated_event(
        dispatched,
        message_id="msg-20260915-github-copilot-2001",
        created_at="2026-09-15T13:06:00Z",
        attempt=2,
    )
    result_attempt_2 = _updated_event(
        result,
        message_id="msg-20260915-copilot-github-2002",
        created_at="2026-09-15T13:07:00Z",
        attempt=2,
        head_sha="1111111111111111111111111111111111111111",
        commit_sha="1111111111111111111111111111111111111111",
        accepted_criteria_delta=(),
        verified_diff_summary="Adjusted validation flow.",
        progress_verified=True,
    )
    ci_attempt_2 = _updated_event(
        ci,
        message_id="msg-20260915-ci-github-2003",
        created_at="2026-09-15T13:08:00Z",
        attempt=2,
        expected_head_sha="1111111111111111111111111111111111111111",
        commit_sha="1111111111111111111111111111111111111111",
    )
    dispatched_attempt_3 = _updated_event(
        dispatched,
        message_id="msg-20260915-github-copilot-3001",
        created_at="2026-09-15T13:09:00Z",
        attempt=3,
    )
    result_attempt_3 = _updated_event(
        result,
        message_id="msg-20260915-copilot-github-3002",
        created_at="2026-09-15T13:10:00Z",
        attempt=3,
        head_sha="2222222222222222222222222222222222222222",
        commit_sha="2222222222222222222222222222222222222222",
        accepted_criteria_delta=(),
        verified_diff_summary="Added one more repair.",
        progress_verified=True,
    )
    ci_attempt_3 = _updated_event(
        ci,
        message_id="msg-20260915-ci-github-3003",
        created_at="2026-09-15T13:11:00Z",
        attempt=3,
        expected_head_sha="2222222222222222222222222222222222222222",
        commit_sha="2222222222222222222222222222222222222222",
    )
    review_attempt_3 = _updated_event(
        review,
        message_id="msg-20260915-openai-github-3004",
        created_at="2026-09-15T13:12:00Z",
        attempt=3,
        expected_head_sha="2222222222222222222222222222222222222222",
        commit_sha="2222222222222222222222222222222222222222",
        review_decision=ReviewDecision.FIX_REQUIRED,
    )
    dispatched_attempt_4 = _updated_event(
        dispatched,
        message_id="msg-20260915-github-copilot-4001",
        created_at="2026-09-15T13:13:00Z",
        attempt=4,
    )
    result_attempt_4 = _updated_event(
        result,
        message_id="msg-20260915-copilot-github-4002",
        created_at="2026-09-15T13:14:00Z",
        attempt=4,
        head_sha="3333333333333333333333333333333333333333",
        commit_sha="3333333333333333333333333333333333333333",
        accepted_criteria_delta=(),
        verified_diff_summary="Third repair done.",
        progress_verified=True,
    )
    ci_attempt_4 = _updated_event(
        ci,
        message_id="msg-20260915-ci-github-4003",
        created_at="2026-09-15T13:15:00Z",
        attempt=4,
        expected_head_sha="3333333333333333333333333333333333333333",
        commit_sha="3333333333333333333333333333333333333333",
    )
    review_attempt_4 = _updated_event(
        review,
        message_id="msg-20260915-openai-github-4004",
        created_at="2026-09-15T13:16:00Z",
        attempt=4,
        expected_head_sha="3333333333333333333333333333333333333333",
        commit_sha="3333333333333333333333333333333333333333",
        review_decision=ReviewDecision.FIX_REQUIRED,
    )

    with pytest.raises(TaskLimitError):
        reduce_run_events(
            [
                created,
                dispatched,
                result,
                ci,
                fix_review,
                dispatched_attempt_2,
                result_attempt_2,
                ci_attempt_2,
                _updated_event(
                    fix_review,
                    message_id="msg-20260915-openai-github-2004",
                    created_at="2026-09-15T13:08:30Z",
                    attempt=2,
                    expected_head_sha="1111111111111111111111111111111111111111",
                    commit_sha="1111111111111111111111111111111111111111",
                ),
                dispatched_attempt_3,
                result_attempt_3,
                ci_attempt_3,
                review_attempt_3,
                dispatched_attempt_4,
                result_attempt_4,
                ci_attempt_4,
                review_attempt_4,
            ]
        )


def test_exact_stop_after_second_task_boundary() -> None:
    run_events = _load_run_events()
    task_two_dispatch = _updated_event(
        run_events[1],
        message_id="msg-20260915-github-copilot-5001",
        task_id="task-0043",
        created_at="2026-09-15T13:07:00Z",
    )

    with pytest.raises(StopBoundaryError):
        reduce_run_events([*run_events, task_two_dispatch])