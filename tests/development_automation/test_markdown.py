from __future__ import annotations

from pathlib import Path

import pytest

from development_automation.errors import MalformedControlDocumentError
from development_automation.markdown import parse_control_document, render_control_document
from development_automation.schemas.v1 import CorrespondenceDocument


def test_schema_round_trip_fixture() -> None:
    fixture_path = Path("fixtures/development_automation/task_instruction_v1.md")
    original_text = fixture_path.read_text(encoding="utf-8")

    document = parse_control_document(original_text)
    rendered_text = render_control_document(document)
    reloaded_document = parse_control_document(rendered_text)

    assert reloaded_document == document


def test_render_redacts_credentials_with_explicit_marker() -> None:
    document = CorrespondenceDocument(
        message_id="msg-20260915-eric-copilot-9999",
        task_id="task-0042",
        from_actor="eric",
        to_actor="copilot",
        type="TASK_INSTRUCTION",
        status="PENDING",
        branch="feat/development-automation-protocol",
        created_at="2026-09-15T12:45:00Z",
        body="OPENAI_API_KEY=sk-secretvalue123456\nKeep the rest of the note.\n",
    )

    rendered_text = render_control_document(document)

    assert "[REDACTED_CREDENTIAL]" in rendered_text
    assert "sk-secretvalue123456" not in rendered_text


def test_parse_rejects_malformed_yaml() -> None:
    invalid_text = "---\nschema_version: control-message.v1\nmessage_id: [oops\n---\n\nBroken\n"

    with pytest.raises(MalformedControlDocumentError):
        parse_control_document(invalid_text)