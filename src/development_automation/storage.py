from __future__ import annotations

from pathlib import Path

from development_automation.errors import DuplicateMessageIdError, PathTraversalError
from development_automation.markdown import parse_control_document, render_control_document
from development_automation.schemas.v1 import CorrespondenceDocument, RunEventDocument

Document = CorrespondenceDocument | RunEventDocument


def _safe_directory(root: Path, relative_directory: str) -> Path:
    if relative_directory not in {"messages", "runs"}:
        raise PathTraversalError("control documents may only be stored under messages or runs")
    target = (root / relative_directory).resolve()
    root_resolved = root.resolve()
    if root_resolved not in target.parents and target != root_resolved:
        raise PathTraversalError("resolved control path escapes the control root")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _filename_for_document(document: Document) -> str:
    timestamp = document.created_at.strftime("%Y%m%dT%H%M%SZ")
    return (
        f"{timestamp}_{document.from_actor.value}_{document.to_actor.value}_"
        f"{document.task_id}_{document.message_id}.md"
    )


def load_documents(directory: Path) -> list[Document]:
    return [
        parse_control_document(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.md"))
    ]


def append_document(root: Path, relative_directory: str, document: Document) -> Path:
    directory = _safe_directory(root, relative_directory)
    for existing in load_documents(directory):
        if existing.message_id == document.message_id:
            raise DuplicateMessageIdError(
                f"message_id {document.message_id!r} already exists in append-only storage"
            )

    destination = directory / _filename_for_document(document)
    if destination.exists():
        raise DuplicateMessageIdError(f"control file {destination.name!r} already exists")

    destination.write_text(render_control_document(document), encoding="utf-8")
    return destination