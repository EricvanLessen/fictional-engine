from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from development_automation.errors import DuplicateMessageIdError, PathTraversalError
from development_automation.schemas.v1 import CorrespondenceDocument
from development_automation.storage import append_document, load_documents


def _document(message_id: str) -> CorrespondenceDocument:
    return CorrespondenceDocument(
        message_id=message_id,
        task_id="task-0042",
        from_actor="eric",
        to_actor="copilot",
        type="TASK_INSTRUCTION",
        status="PENDING",
        branch="feat/development-automation-protocol",
        created_at=datetime(2026, 9, 15, 12, 45, tzinfo=UTC),
        body="# Objective\n\nImplement Increment A.\n",
    )


def test_append_only_storage_rejects_duplicate_message_ids(tmp_path: Path) -> None:
    control_root = tmp_path / "control"

    first_path = append_document(
        control_root,
        "messages",
        _document("msg-20260915-eric-copilot-1111"),
    )

    with pytest.raises(DuplicateMessageIdError):
        append_document(
            control_root,
            "messages",
            _document("msg-20260915-eric-copilot-1111"),
        )

    assert first_path.name == (
        "20260915T124500Z_eric_copilot_task-0042_msg-20260915-eric-copilot-1111.md"
    )


def test_append_storage_creates_collision_safe_distinct_filenames(tmp_path: Path) -> None:
    control_root = tmp_path / "control"

    first_path = append_document(
        control_root,
        "messages",
        _document("msg-20260915-eric-copilot-1111"),
    )
    second_path = append_document(
        control_root,
        "messages",
        _document("msg-20260915-eric-copilot-2222"),
    )

    assert first_path != second_path
    assert [document.message_id for document in load_documents(control_root / "messages")] == [
        "msg-20260915-eric-copilot-1111",
        "msg-20260915-eric-copilot-2222",
    ]


def test_append_storage_rejects_path_traversal_directory(tmp_path: Path) -> None:
    with pytest.raises(PathTraversalError):
        append_document(
            tmp_path / "control",
            "../outside",
            _document("msg-20260915-eric-copilot-3333"),
        )