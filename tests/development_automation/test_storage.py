from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from development_automation.errors import DuplicateMessageIdError, PathTraversalError
from development_automation.schemas.v1 import CorrespondenceDocument, RunEventDocument
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


def _run_document(message_id: str) -> RunEventDocument:
    return RunEventDocument(
        message_id=message_id,
        task_id="task-0042",
        from_actor="system",
        to_actor="github",
        type="RUN_EVENT",
        status="RECORDED",
        branch="feat/development-automation-protocol",
        created_at=datetime(2026, 9, 15, 12, 45, tzinfo=UTC),
        expected_head_sha=None,
        commit_sha=None,
        body="# Run event\n\nTask created.\n",
        event_type="TASK_CREATED",
        lifecycle_state="READY_FOR_COPILOT",
    )


def test_append_only_storage_rejects_conflicting_duplicate_message_ids(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    message_id = "msg-20260915-eric-copilot-1111"

    first_path = append_document(
        control_root,
        "messages",
        _document(message_id),
    )

    conflicting_document = _document(message_id).model_copy(
        update={"body": "# Objective\n\nChanged payload.\n"}
    )

    with pytest.raises(DuplicateMessageIdError):
        append_document(
            control_root,
            "messages",
            conflicting_document,
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


def test_cross_directory_message_id_collision_is_rejected(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    message_id = "msg-20260915-cross-dir-4444"

    append_document(control_root, "messages", _document(message_id))

    with pytest.raises(DuplicateMessageIdError):
        append_document(control_root, "runs", _run_document(message_id))


def test_exact_redelivery_is_idempotent_no_op(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    message_id = "msg-20260915-idempotent-5555"
    document = _document(message_id)

    first_path = append_document(control_root, "messages", document)
    second_path = append_document(control_root, "messages", document)

    assert first_path == second_path
    assert len(list((control_root / "messages").glob("*.md"))) == 1


def test_foreign_document_type_rejected_per_directory(tmp_path: Path) -> None:
    control_root = tmp_path / "control"

    with pytest.raises(PathTraversalError):
        append_document(control_root, "messages", _run_document("msg-20260915-foreign-6666"))

    with pytest.raises(PathTraversalError):
        append_document(control_root, "runs", _document("msg-20260915-foreign-7777"))


def test_concurrent_append_is_race_safe_without_overwrite(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    document = _document("msg-20260915-concurrent-8888")
    results: list[Path] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            results.append(append_document(control_root, "messages", document))
        except Exception as exc:  # pragma: no cover - assertion below checks no failures.
            errors.append(exc)

    thread_one = threading.Thread(target=worker)
    thread_two = threading.Thread(target=worker)
    thread_one.start()
    thread_two.start()
    thread_one.join()
    thread_two.join()

    assert not errors
    assert len(results) == 2
    assert results[0] == results[1]
    assert len(list((control_root / "messages").glob("*.md"))) == 1