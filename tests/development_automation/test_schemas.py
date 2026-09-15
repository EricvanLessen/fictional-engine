from __future__ import annotations

from datetime import UTC, datetime

import pytest

from development_automation.schemas.v1 import RunEventDocument


def _base_event_payload() -> dict[str, object]:
    return {
        "schema_version": "control-run.v1",
        "message_id": "msg-20260915-ci-github-9001",
        "task_id": "task-0042",
        "from": "ci",
        "to": "github",
        "type": "RUN_EVENT",
        "status": "RECORDED",
        "branch": "feat/development-automation-protocol",
        "created_at": datetime(2026, 9, 15, 14, 30, tzinfo=UTC),
        "attempt": 1,
        "pull_request_url": "https://github.com/EricvanLessen/fictional-engine/pull/7",
        "body": "# Run event\n\nSchema regression test.\n",
    }


def test_result_rejects_short_sha() -> None:
    payload = {
        **_base_event_payload(),
        "event_type": "COPILOT_RESULT_RECORDED",
        "head_sha": "abcdef0",
        "commit_sha": "abcdef0",
    }

    with pytest.raises(ValueError):
        RunEventDocument.model_validate(payload)


def test_review_rejects_contradictory_head_sha_field() -> None:
    full_a = "a" * 40
    payload = {
        **_base_event_payload(),
        "message_id": "msg-20260915-openai-github-9002",
        "from": "openai",
        "event_type": "OPENAI_REVIEW_RECORDED",
        "review_decision": "ACCEPT",
        "expected_head_sha": full_a,
        "commit_sha": full_a,
        "head_sha": "b" * 40,
    }

    with pytest.raises(ValueError):
        RunEventDocument.model_validate(payload)


def test_result_rejects_unchecked_expected_head_sha_field() -> None:
    full_a = "a" * 40
    payload = {
        **_base_event_payload(),
        "message_id": "msg-20260915-copilot-github-9003",
        "from": "copilot",
        "to": "github",
        "event_type": "COPILOT_RESULT_RECORDED",
        "head_sha": full_a,
        "commit_sha": full_a,
        "expected_head_sha": "b" * 40,
    }

    with pytest.raises(ValueError):
        RunEventDocument.model_validate(payload)
