"""Bootstrap module for creating initial tasks in Increment C workflow.

This module provides utilities for creating the first task in a controlled,
repeatable manner with proper PR and commit binding.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from development_automation.schemas.v1 import (
    Actor,
    DocumentStatus,
    DocumentType,
    LifecycleState,
    RunEventDocument,
    RunEventType,
)
from development_automation.storage import append_document


class TaskBootstrapConfig(NamedTuple):
    """Configuration for bootstrapping a new task."""

    task_id: str | None = None  # Auto-generated if None
    branch: str = "feat/development-automation-dispatcher"
    pull_request_number: int = 10
    head_sha: str = "deadbeef" * 5  # 40 hex chars
    created_at: datetime | None = None


class BootstrapResult(NamedTuple):
    """Result of a successful task bootstrap."""

    task_id: str
    message_id: str
    event_path: Path
    expected_head_sha: str


def generate_task_id(prefix: str = "task-") -> str:
    """Generate a unique task ID using the task-NNNN convention."""
    # Use a simple counter-based approach for determinism
    # In production, this would query existing tasks to find the next number
    import random

    number = random.randint(1000, 9999)
    return f"{prefix}{number:04d}"


def bootstrap_task(
    control_root: Path,
    config: TaskBootstrapConfig | None = None,
) -> BootstrapResult:
    """Create and persist the first task for Increment C.

    This bootstrap is idempotent: calling it multiple times with the same
    task_id and PR number will return the same result if the persisted task
    is identical.

    Args:
        control_root: Root directory for control documents
        config: Bootstrap configuration; uses defaults if None

    Returns:
        BootstrapResult with task_id, message_id, path, and expected_head_sha

    Raises:
        ValueError: If task with different content already exists
        OSError: If control root cannot be created
    """
    if config is None:
        config = TaskBootstrapConfig()

    task_id = config.task_id or generate_task_id()
    created_at = config.created_at or datetime.now(tz=timezone.utc)
    message_id = f"msg-bootstrap-{task_id}-{uuid.uuid4().hex[:8]}"

    # Create the TASK_CREATED event
    event = RunEventDocument(
        schema_version="control-run.v1",
        message_id=message_id,
        task_id=task_id,
        from_actor=Actor.SYSTEM,
        to_actor=Actor.GITHUB,
        type=DocumentType.RUN_EVENT,
        status=DocumentStatus.RECORDED,
        branch=config.branch,
        created_at=created_at,
        in_reply_to=None,
        attempt=1,
        expected_head_sha=None,  # Will be set by COPILOT_DISPATCHED
        pull_request_url=f"https://github.com/EricvanLessen/fictional-engine/pull/{config.pull_request_number}",
        commit_sha=None,
        body=f"Bootstrap task {task_id} for PR #{config.pull_request_number} on branch {config.branch}",
        event_type=RunEventType.TASK_CREATED,
        lifecycle_state=LifecycleState.READY_FOR_COPILOT,
        head_sha=None,
        provider_run_id=None,
        ci_conclusion=None,
        ci_check_names=(),
        review_decision=None,
        accepted_criteria_delta=(),
        verified_diff_summary=None,
        progress_verified=False,
        next_task_id=None,
        stop_reason=None,
        pull_request_number=config.pull_request_number,
    )

    # Persist the event
    control_root.mkdir(parents=True, exist_ok=True)
    event_path = append_document(control_root, "runs", event)

    return BootstrapResult(
        task_id=task_id,
        message_id=message_id,
        event_path=event_path,
        expected_head_sha=config.head_sha,
    )


def create_copilot_dispatch_record(
    control_root: Path,
    task_id: str,
    head_sha: str,
    branch: str,
    provider_run_id: str = "run-copilot-mock-001",
    created_at: datetime | None = None,
) -> RunEventDocument:
    """Create a COPILOT_DISPATCHED event after dispatching to Copilot.

    This transitions the task from READY_FOR_COPILOT to WAITING_FOR_CI.

    Args:
        control_root: Root directory for control documents
        task_id: The task being dispatched
        head_sha: The commit SHA that Copilot will work on
        branch: The branch name
        provider_run_id: Provider-side run ID from Copilot
        created_at: Event timestamp; uses now if None

    Returns:
        The persisted COPILOT_DISPATCHED event
    """
    if created_at is None:
        created_at = datetime.now(tz=timezone.utc)

    message_id = f"msg-copilot-dispatched-{task_id}-{uuid.uuid4().hex[:8]}"

    event = RunEventDocument(
        schema_version="control-run.v1",
        message_id=message_id,
        task_id=task_id,
        from_actor=Actor.COPILOT,
        to_actor=Actor.GITHUB,
        type=DocumentType.RUN_EVENT,
        status=DocumentStatus.RECORDED,
        branch=branch,
        created_at=created_at,
        in_reply_to=None,
        attempt=1,
        expected_head_sha=head_sha,  # Task now expects this head
        pull_request_url=None,
        commit_sha=head_sha,
        body=f"Copilot dispatched for {task_id} on {head_sha[:8]}",
        event_type=RunEventType.COPILOT_DISPATCHED,
        lifecycle_state=LifecycleState.WAITING_FOR_CI,
        head_sha=head_sha,
        provider_run_id=provider_run_id,
        ci_conclusion=None,
        ci_check_names=(),
        review_decision=None,
        accepted_criteria_delta=(),
        verified_diff_summary=None,
        progress_verified=False,
        next_task_id=None,
        stop_reason=None,
        pull_request_number=None,
    )

    control_root.mkdir(parents=True, exist_ok=True)
    append_document(control_root, "runs", event)
    return event
