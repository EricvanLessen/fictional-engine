"""Real bootstrap integration for Increment C.

Creates the first task with the portable entrypoint, GitHub persistence, and
exchangeable coding/review providers. Test-safe: supports injected mock providers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from development_automation.dispatcher_models import (
    DispatcherPolicy,
    DispatchEventType,
    EventSource,
)
from development_automation.github_persistence import GitHubControlBranchPersistence
from development_automation.live_adapters import (
    GitHubCopilotCodingAgent,
    OpenAIReviewAdapter,
)
from development_automation.mock_agents import MockCodingAgent
from development_automation.schemas.v1 import LifecycleState


@dataclass(frozen=True)
class BootstrapConfig:
    """Configuration for bootstrapping first task with real components."""

    control_root: Path
    task_id: str
    pr_number: int
    branch: str
    initial_head_sha: str
    task_title: str
    task_body: str
    repository: str = "EricvanLessen/fictional-engine"
    delivery_id: str = "bootstrap-delivery-1"
    use_github_persistence: bool = True
    use_mock_agents: bool = True  # For testing without real API keys
    openai_api_key: str | None = None
    github_token: str | None = None
    github_api_url: str = "https://api.github.com"


@dataclass(frozen=True)
class BootstrapResult:
    """Result of successful bootstrap."""

    task_id: str
    initial_action: str
    initial_reason: str
    task_lifecycle_state: LifecycleState
    control_root: Path
    persisted_paths: tuple[str, ...] = ()


class RealBootstrap:
    """Bootstrap first task using real Increment C components."""

    def __init__(
        self,
        config: BootstrapConfig,
        *,
        coding_agent: Any | None = None,
        reviewer: Any | None = None,
        github_persistence: GitHubControlBranchPersistence | None = None,
    ) -> None:
        self._config = config
        self._control_root = config.control_root.resolve()
        self._coding_agent_override = coding_agent
        self._reviewer_override = reviewer
        self._github_persistence_override = github_persistence
        self._control_root.mkdir(parents=True, exist_ok=True)

    def _build_coding_agent(self) -> Any:
        """Build the configured real, injected, or mock coding agent."""
        if self._coding_agent_override is not None:
            return self._coding_agent_override
        if self._config.use_mock_agents:
            return MockCodingAgent()
        # Real GitHubCopilotCodingAgent requires repository and token
        token = self._config.github_token or os.getenv("GITHUB_TOKEN", "")
        if not token:
            raise ValueError("Real coding agent requires GITHUB_TOKEN or github_token config")
        return GitHubCopilotCodingAgent(
            repository=self._config.repository,
            token=token,
            base_url=self._config.github_api_url,
        )

    def _build_reviewer(self) -> Any:
        """Build the configured real, injected, or mock reviewer."""
        if self._reviewer_override is not None:
            return self._reviewer_override
        if self._config.use_mock_agents:
            # For bootstrap, use a simple mock; MockReviewer might not exist
            # Return a callable object that implements reviewer interface
            class _MockReviewer:
                async def review(self, context: Any) -> Any:
                    return None
            return _MockReviewer()
        # Real OpenAIReviewAdapter requires api_key
        api_key = self._config.openai_api_key or os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("Real reviewer requires OPENAI_API_KEY or openai_api_key config")
        return OpenAIReviewAdapter(
            api_key=api_key,
            model="gpt-4-mini",
        )

    def _build_github_persistence(self) -> GitHubControlBranchPersistence | None:
        """Build GitHub persistence if enabled."""
        if self._github_persistence_override is not None:
            return self._github_persistence_override
        if not self._config.use_github_persistence:
            return None
        # Real GitHub persistence requires token
        token = self._config.github_token or os.getenv("GITHUB_TOKEN", "")
        if not token:
            raise ValueError("GitHub persistence requires GITHUB_TOKEN or github_token config")
        return GitHubControlBranchPersistence(
            repository=self._config.repository,
            token=token,
            repository_root=self._control_root.parent,
            control_root=self._control_root,
            base_url=self._config.github_api_url,
        )

    def bootstrap(self) -> BootstrapResult:
        """Create first task and record initial dispatcher action."""
        # Late import to avoid circular dependency
        from development_automation.entrypoint import (
            EntrypointResult,
            PortableDispatcherEntrypoint,
        )

        coding_agent = self._build_coding_agent()
        reviewer = self._build_reviewer()
        github_persistence = self._build_github_persistence()

        policy = DispatcherPolicy(
            allowlisted_repositories=("EricvanLessen/fictional-engine",),
            allowlisted_actors=("EricvanLessen",),
            expected_check_names=("checks", "docker", "gitleaks"),
            trusted_workflow_refs=(
                "EricvanLessen/fictional-engine/.github/workflows/ci.yml@refs/heads/main",
                "EricvanLessen/fictional-engine/.github/workflows/secret-scan.yml@refs/heads/main",
            ),
        )

        entrypoint = PortableDispatcherEntrypoint(
            control_root=self._control_root,
            policy=policy,
            webhook_secret="",
            coding_agent=coding_agent,
            reviewer=reviewer,
            github_persistence=github_persistence,
        )

        # Simulate GitHub issues.opened event that creates the task
        # Extract issue number from task_id (format: task-NNNN or task-NAME)
        task_id_parts = self._config.task_id.split("-", 1)
        is_numeric = task_id_parts[-1].isdigit()
        task_issue_number = (
            int(task_id_parts[-1]) if is_numeric else len(task_id_parts)
        )

        event_payload = {
            "action": "opened",
            "issue": {
                "number": task_issue_number,
                "title": self._config.task_title,
                "body": self._config.task_body,
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
            },
            "repository": {
                "name": self._config.repository.split("/")[-1],
                "full_name": self._config.repository,
            },
            "sender": {
                "login": "EricvanLessen",
            },
        }

        # Process event through real entrypoint using public API
        result = entrypoint.handle_event(
            event_name=DispatchEventType.ISSUES,
            payload=event_payload,
            delivery_id=self._config.delivery_id,
            source=EventSource.ACTIONS,
        )

        if isinstance(result, EntrypointResult):
            return BootstrapResult(
                task_id=result.task_id or self._config.task_id,
                initial_action=result.action,
                initial_reason=result.reason,
                task_lifecycle_state=LifecycleState.READY_FOR_COPILOT,
                control_root=self._control_root,
                persisted_paths=result.persisted_paths,
            )

        raise RuntimeError(f"Unexpected entrypoint result: {result}")


def bootstrap_first_task(config: BootstrapConfig) -> BootstrapResult:
    """Create first task with real Increment C components.

    Args:
        config: Bootstrap configuration with PR, branch, head SHA, and component options

    Returns:
        BootstrapResult with task_id, initial action, and persisted paths
    """
    bootstrap = RealBootstrap(config)
    return bootstrap.bootstrap()
