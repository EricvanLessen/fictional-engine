"""First-run bootstrap integration for Increment C dispatcher.

Detects when a GitHub event should trigger first-task creation via bootstrap,
then seamlessly processes the event with the newly created task state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from development_automation.bootstrap_real import BootstrapConfig, RealBootstrap
from development_automation.github_persistence import GitHubControlBranchPersistence
from development_automation.schemas.v1 import LifecycleState

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class BootstrapTrigger:
    """Configuration for first-run bootstrap trigger."""

    enabled: bool = False
    target_pr_number: int | None = None
    target_branch: str | None = None
    target_head_sha: str | None = None


def _extract_event_metadata(event_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Extract PR number, branch, head SHA from GitHub event payload."""
    metadata: dict[str, Any] = {}

    if event_name == "pull_request":
        pr = payload.get("pull_request", {})
        metadata["pr_number"] = pr.get("number")
        metadata["branch"] = pr.get("head", {}).get("ref")
        metadata["head_sha"] = pr.get("head", {}).get("sha")
    elif event_name == "push":
        metadata["branch"] = payload.get("ref", "").split("/")[-1]
        metadata["head_sha"] = payload.get("after")
    elif event_name == "workflow_run":
        wr = payload.get("workflow_run", {})
        metadata["branch"] = wr.get("head_branch")
        metadata["head_sha"] = wr.get("head_commit", {}).get("id") or wr.get("head_sha")
    elif event_name == "check_run":
        cr = payload.get("check_run", {})
        metadata["head_sha"] = cr.get("head_sha")

    return metadata


def should_bootstrap(
    trigger: BootstrapTrigger,
    event_name: str,
    payload: dict[str, Any],
    existing_tasks: dict[str, Any],
) -> bool:
    """Determine if bootstrap should run.

    Args:
        trigger: Bootstrap configuration
        event_name: GitHub event type
        payload: Event payload
        existing_tasks: Current task projections

    Returns:
        True if bootstrap should execute
    """
    if not trigger.enabled:
        return False

    # Only bootstrap if no tasks exist (first run)
    if existing_tasks:
        return False

    # Extract metadata from event
    metadata = _extract_event_metadata(event_name, payload)

    # Check trigger conditions
    if (
        trigger.target_pr_number is not None
        and metadata.get("pr_number") != trigger.target_pr_number
    ):
        return False

    if (
        trigger.target_branch is not None
        and metadata.get("branch") != trigger.target_branch
    ):
        return False

    if (
        trigger.target_head_sha is not None
        and metadata.get("head_sha") != trigger.target_head_sha
    ):
        return False

    return True


def apply_bootstrap(
    trigger: BootstrapTrigger,
    event_name: str,
    payload: dict[str, Any],
    control_root: Path,
    repository: str,
    coding_agent: Any,
    reviewer: Any,
    github_persistence: GitHubControlBranchPersistence,
) -> tuple[bool, str]:
    """Execute first-run bootstrap and persist task state.

    Args:
        trigger: Bootstrap configuration
        event_name: GitHub event type
        payload: Event payload
        control_root: Path to control directory
        repository: Repository identifier (owner/repo)
        coding_agent: Copilot coding agent
        reviewer: OpenAI review adapter
        github_persistence: GitHub control state persistence

    Returns:
        Tuple (success: bool, reason: str)
    """
    try:
        metadata = _extract_event_metadata(event_name, payload)
        pr_number = metadata.get("pr_number")
        branch_val = metadata.get("branch")
        head_sha_val = metadata.get("head_sha")

        if not isinstance(branch_val, str) or not isinstance(head_sha_val, str):
            return False, "insufficient metadata for bootstrap"

        config = BootstrapConfig(
            control_root=control_root,
            task_id="task-0001",
            pr_number=pr_number or 0,
            branch=branch_val,
            initial_head_sha=head_sha_val,
            task_title=f"Task 0001: Increment C Cycle (PR #{pr_number})",
            task_body=(
                f"Automated bootstrap task for PR #{pr_number}.\n"
                f"Branch: {branch_val}\n"
                f"Head SHA: {head_sha_val}\n"
            ),
            repository=repository,
            use_mock_agents=False,  # Use real agents
            use_github_persistence=True,
            github_token=github_persistence._token,
            openai_api_key=None,  # Will use env var
        )

        bootstrap = RealBootstrap(config)
        result = bootstrap.bootstrap()

        if result.task_lifecycle_state != LifecycleState.READY_FOR_COPILOT:
            return False, f"bootstrap produced unexpected state: {result.task_lifecycle_state}"

        # Sync to GitHub control branch
        github_persistence.hydrate()  # Refresh before sync
        # Convert string paths to Path objects for sync_paths
        path_objs = tuple(Path(p) for p in result.persisted_paths)
        github_persistence.sync_paths(path_objs)
        LOG.info(
            f"Bootstrap synced {len(result.persisted_paths)} paths to GitHub control branch"
        )

        return True, "bootstrap successful"

    except Exception as e:
        LOG.exception(f"Bootstrap failed: {e}")
        return False, f"bootstrap exception: {type(e).__name__}"
