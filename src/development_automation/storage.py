from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

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


def _document_matches_directory(relative_directory: str, document: Document) -> bool:
    if relative_directory == "messages":
        return isinstance(document, CorrespondenceDocument)
    if relative_directory == "runs":
        return isinstance(document, RunEventDocument)
    return False


def _all_control_documents(root: Path) -> Iterator[tuple[Path, Document]]:
    for name in ("messages", "runs"):
        directory = _safe_directory(root, name)
        for path in sorted(directory.glob("*.md")):
            yield (path, parse_control_document(path.read_text(encoding="utf-8")))


def _canonicalize_document(document: Document) -> Document:
    # Canonical identity must match the persisted representation.
    return parse_control_document(render_control_document(document))


def _acquire_lock(root: Path) -> BinaryIO:
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".append.lock"
    lock_handle = lock_path.open("a+b")
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
    return lock_handle


def _release_lock(lock_handle: BinaryIO) -> None:
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    lock_handle.close()


def load_documents(directory: Path) -> list[Document]:
    return [
        parse_control_document(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.md"))
    ]


def append_document(root: Path, relative_directory: str, document: Document) -> Path:
    directory = _safe_directory(root, relative_directory)
    canonical_document = _canonicalize_document(document)
    if not _document_matches_directory(relative_directory, canonical_document):
        raise PathTraversalError(
            f"document type does not match target directory {relative_directory!r}"
        )

    lock_handle = _acquire_lock(root)
    try:
        for existing_path, existing_document in _all_control_documents(root):
            if existing_document.message_id != canonical_document.message_id:
                continue
            if existing_document == canonical_document:
                return existing_path
            raise DuplicateMessageIdError(
                "message_id "
                f"{canonical_document.message_id!r} already exists with different content"
            )

        destination = directory / _filename_for_document(canonical_document)
        rendered_document = render_control_document(canonical_document)

        try:
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError as exc:
            raise DuplicateMessageIdError(
                f"control file {destination.name!r} already exists"
            ) from exc

        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered_document)
        return destination
    finally:
        _release_lock(lock_handle)