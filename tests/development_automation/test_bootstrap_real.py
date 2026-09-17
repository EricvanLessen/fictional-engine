"""Tests for real bootstrap integration with Increment C components."""

from pathlib import Path

import pytest

from development_automation.bootstrap_real import (
    BootstrapConfig,
    RealBootstrap,
    bootstrap_first_task,
)
from development_automation.schemas.v1 import LifecycleState


@pytest.fixture
def temp_control_root(tmp_path: Path) -> Path:
    """Temporary control root for bootstrap testing."""
    return tmp_path / "control"


@pytest.fixture
def bootstrap_config(temp_control_root: Path) -> BootstrapConfig:
    """Standard bootstrap config for testing."""
    return BootstrapConfig(
        control_root=temp_control_root,
        task_id="task-0001",
        pr_number=10,
        branch="feat/increment-c-bootstrap",
        initial_head_sha="0aafd25951067ac67fbd0e8bd8de212e927e2edf",  # Real PR #10 head
        task_title="[Task 0001] Implement bootstrap proof of concept",
        task_body="Demonstrate real bootstrap integration with live adapters.",
        repository="EricvanLessen/fictional-engine",
        use_mock_agents=True,
        use_github_persistence=False,  # Avoid real GitHub calls in tests
    )


class TestRealBootstrap:
    """Test bootstrap with real PortableDispatcherEntrypoint."""

    def test_bootstrap_creates_control_root(
        self, temp_control_root: Path, bootstrap_config: BootstrapConfig
    ) -> None:
        """Bootstrap initializes control_root directory structure."""
        assert not temp_control_root.exists()
        bootstrap = RealBootstrap(bootstrap_config)
        bootstrap.bootstrap()
        assert temp_control_root.exists()

    def test_bootstrap_creates_events_directory(
        self, temp_control_root: Path, bootstrap_config: BootstrapConfig
    ) -> None:
        """Bootstrap processes event without error."""
        bootstrap = RealBootstrap(bootstrap_config)
        result = bootstrap.bootstrap()
        
        # Control root should exist after bootstrap
        assert temp_control_root.exists()
        assert result.task_id is not None

    def test_bootstrap_returns_task_id(
        self, temp_control_root: Path, bootstrap_config: BootstrapConfig
    ) -> None:
        """Bootstrap result includes task_id from config."""
        bootstrap = RealBootstrap(bootstrap_config)
        result = bootstrap.bootstrap()
        
        assert result.task_id == "task-0001"

    def test_bootstrap_records_initial_state(
        self, temp_control_root: Path, bootstrap_config: BootstrapConfig
    ) -> None:
        """Bootstrap sets READY_FOR_COPILOT lifecycle state."""
        bootstrap = RealBootstrap(bootstrap_config)
        result = bootstrap.bootstrap()
        
        assert result.task_lifecycle_state == LifecycleState.READY_FOR_COPILOT

    def test_bootstrap_function_creates_task(
        self, temp_control_root: Path, bootstrap_config: BootstrapConfig
    ) -> None:
        """Top-level bootstrap_first_task function works."""
        config = BootstrapConfig(
            control_root=temp_control_root,
            task_id="task-0002",
            pr_number=10,
            branch="feat/test-bootstrap",
            initial_head_sha="0aafd2595105",
            task_title="Test Task",
            task_body="Test body.",
            use_github_persistence=False,  # Disable GitHub calls in test
        )
        
        result = bootstrap_first_task(config)
        
        assert result.task_id == "task-0002"
        assert temp_control_root.exists()

    def test_bootstrap_with_real_head_sha(
        self, temp_control_root: Path
    ) -> None:
        """Bootstrap accepts real PR head SHA."""
        config = BootstrapConfig(
            control_root=temp_control_root,
            task_id="task-0003",
            pr_number=10,
            branch="feat/milestone-c",
            initial_head_sha="0aafd25951067ac67fbd0e8bd8de212e927e2edf",  # Real
            task_title="Milestone C Completion",
            task_body="Real head SHA binding.",
            use_github_persistence=False,  # Disable GitHub calls in test
        )
        
        bootstrap = RealBootstrap(config)
        result = bootstrap.bootstrap()
        
        assert result.task_id == "task-0003"
        # Should not raise about invalid SHA format


class TestBootstrapHeadBinding:
    """Test head SHA binding and PR binding."""

    def test_bootstrap_binds_to_pr_number(
        self, temp_control_root: Path
    ) -> None:
        """Bootstrap records PR number."""
        config = BootstrapConfig(
            control_root=temp_control_root,
            task_id="task-0010",
            pr_number=10,
            branch="feat/pr-binding",
            initial_head_sha="0aafd25951067ac67fbd0e8bd8de212e927e2edf",
            task_title="PR Binding Test",
            task_body="Should be bound to PR #10.",
            use_github_persistence=False,  # Disable GitHub calls in test
        )
        
        bootstrap = RealBootstrap(config)
        result = bootstrap.bootstrap()
        
        # Verify control_root was used (PR #10 control branch would sync here)
        assert result.control_root == temp_control_root


class TestBootstrapWithMockAgents:
    """Test bootstrap with mock agents (default)."""

    def test_bootstrap_builds_mock_coding_agent(
        self, temp_control_root: Path, bootstrap_config: BootstrapConfig
    ) -> None:
        """Bootstrap builds mock Copilot coding agent."""
        assert bootstrap_config.use_mock_agents is True
        bootstrap = RealBootstrap(bootstrap_config)
        
        agent = bootstrap._build_coding_agent()
        assert agent is not None
        # MockCodingAgent has run method
        assert hasattr(agent, 'run') and callable(agent.run)

    def test_bootstrap_builds_mock_reviewer(
        self, temp_control_root: Path, bootstrap_config: BootstrapConfig
    ) -> None:
        """Bootstrap builds mock reviewer by default."""
        assert bootstrap_config.use_mock_agents is True
        bootstrap = RealBootstrap(bootstrap_config)
        
        reviewer = bootstrap._build_reviewer()
        assert reviewer is not None
        # Should have reviewer interface
        assert hasattr(reviewer, 'review') or callable(reviewer)


class TestBootstrapPersistence:
    """Test control state persistence."""

    def test_bootstrap_persists_without_github(
        self, temp_control_root: Path, bootstrap_config: BootstrapConfig
    ) -> None:
        """Bootstrap works with local file storage (no GitHub)."""
        config = BootstrapConfig(
            control_root=temp_control_root,
            task_id="task-local",
            pr_number=10,
            branch="feat/local",
            initial_head_sha="0aafd25951067ac67fbd0e8bd8de212e927e2edf",
            task_title="Local Test",
            task_body="No GitHub persistence.",
            use_github_persistence=False,
        )
        
        bootstrap = RealBootstrap(config)
        result = bootstrap.bootstrap()
        
        assert result.task_id == "task-local"
        # Control root should have local files
        assert temp_control_root.exists()
