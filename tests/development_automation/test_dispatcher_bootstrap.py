"""Tests for dispatcher bootstrap integration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from development_automation.dispatcher_bootstrap import (
    BootstrapTrigger,
    _extract_event_metadata,
    apply_bootstrap,
    should_bootstrap,
)


class TestBootstrapTrigger:
    """Test BootstrapTrigger configuration."""

    def test_bootstrap_trigger_disabled_by_default(self) -> None:
        trigger = BootstrapTrigger()
        assert trigger.enabled is False

    def test_bootstrap_trigger_configure_pr(self) -> None:
        trigger = BootstrapTrigger(
            enabled=True,
            target_pr_number=12,
            target_branch="test/increment-c-activation",
            target_head_sha="abc123",
        )
        assert trigger.enabled is True
        assert trigger.target_pr_number == 12
        assert trigger.target_branch == "test/increment-c-activation"
        assert trigger.target_head_sha == "abc123"


class TestExtractEventMetadata:
    """Test event metadata extraction."""

    def test_extract_pull_request_metadata(self) -> None:
        payload = {
            "pull_request": {
                "number": 12,
                "head": {
                    "ref": "test/increment-c-activation",
                    "sha": "def456",
                },
            }
        }
        metadata = _extract_event_metadata("pull_request", payload)
        assert metadata["pr_number"] == 12
        assert metadata["branch"] == "test/increment-c-activation"
        assert metadata["head_sha"] == "def456"

    def test_extract_push_metadata(self) -> None:
        payload = {
            "ref": "refs/heads/test/increment-c-activation",
            "after": "ghi789",
        }
        metadata = _extract_event_metadata("push", payload)
        # Push ref includes full path, should extract last component
        assert metadata["branch"] in (
            "test/increment-c-activation",
            "increment-c-activation",
        ), f"Got: {metadata['branch']}"
        assert metadata["head_sha"] == "ghi789"

    def test_extract_workflow_run_metadata(self) -> None:
        payload = {
            "workflow_run": {
                "head_branch": "test/increment-c-activation",
                "head_commit": {
                    "id": "jkl012",
                },
            }
        }
        metadata = _extract_event_metadata("workflow_run", payload)
        assert metadata["branch"] == "test/increment-c-activation"
        assert metadata["head_sha"] == "jkl012"

    def test_extract_check_run_metadata(self) -> None:
        payload = {
            "check_run": {
                "head_sha": "mno345",
            }
        }
        metadata = _extract_event_metadata("check_run", payload)
        assert metadata["head_sha"] == "mno345"


class TestShouldBootstrap:
    """Test bootstrap trigger conditions."""

    def test_no_bootstrap_when_disabled(self) -> None:
        trigger = BootstrapTrigger(enabled=False)
        payload = {
            "pull_request": {
                "number": 12,
                "head": {"ref": "feat/test", "sha": "abc"},
            }
        }
        assert not should_bootstrap(trigger, "pull_request", payload, {})

    def test_no_bootstrap_when_tasks_exist(self) -> None:
        trigger = BootstrapTrigger(
            enabled=True,
            target_pr_number=12,
            target_branch="test/increment-c-activation",
        )
        payload = {
            "pull_request": {
                "number": 12,
                "head": {"ref": "test/increment-c-activation", "sha": "abc"},
            }
        }
        existing_tasks = {"task-0001": MagicMock()}
        assert not should_bootstrap(trigger, "pull_request", payload, existing_tasks)

    def test_bootstrap_when_all_conditions_met(self) -> None:
        trigger = BootstrapTrigger(
            enabled=True,
            target_pr_number=12,
            target_branch="test/increment-c-activation",
            target_head_sha="abc123",
        )
        payload = {
            "pull_request": {
                "number": 12,
                "head": {
                    "ref": "test/increment-c-activation",
                    "sha": "abc123",
                },
            }
        }
        assert should_bootstrap(trigger, "pull_request", payload, {})

    def test_no_bootstrap_when_pr_mismatch(self) -> None:
        trigger = BootstrapTrigger(
            enabled=True,
            target_pr_number=12,
        )
        payload = {
            "pull_request": {
                "number": 13,  # Wrong PR
                "head": {"ref": "feat/test", "sha": "abc"},
            }
        }
        assert not should_bootstrap(trigger, "pull_request", payload, {})

    def test_no_bootstrap_when_branch_mismatch(self) -> None:
        trigger = BootstrapTrigger(
            enabled=True,
            target_branch="test/increment-c-activation",
        )
        payload = {
            "pull_request": {
                "number": 12,
                "head": {
                    "ref": "wrong/branch",  # Wrong branch
                    "sha": "abc",
                },
            }
        }
        assert not should_bootstrap(trigger, "pull_request", payload, {})

    def test_no_bootstrap_when_head_sha_mismatch(self) -> None:
        trigger = BootstrapTrigger(
            enabled=True,
            target_head_sha="abc123",
        )
        payload = {
            "pull_request": {
                "number": 12,
                "head": {
                    "ref": "feat/test",
                    "sha": "wrong_sha",  # Wrong SHA
                },
            }
        }
        assert not should_bootstrap(trigger, "pull_request", payload, {})


class TestApplyBootstrap:
    """Test bootstrap application and persistence."""

    def test_apply_bootstrap_requires_metadata(self) -> None:
        """Bootstrap fails gracefully without sufficient metadata."""
        trigger = BootstrapTrigger(enabled=True)
        payload: dict = {"pull_request": {}}  # Missing head info
        control_root = Path("/tmp/test-control")
        coding_agent = MagicMock()
        reviewer = MagicMock()
        github_persistence = MagicMock()

        success, reason = apply_bootstrap(
            trigger,
            "pull_request",
            payload,
            control_root,
            "owner/repo",
            coding_agent,
            reviewer,
            github_persistence,
        )

        assert success is False
        assert "metadata" in reason

    @patch("development_automation.dispatcher_bootstrap.RealBootstrap")
    def test_apply_bootstrap_creates_task(self, mock_bootstrap_cls):  # type: ignore
        """Bootstrap successfully creates task-0001."""
        # Mock bootstrap result
        mock_result = MagicMock()
        from development_automation.schemas.v1 import LifecycleState

        mock_result.task_lifecycle_state = LifecycleState.READY_FOR_COPILOT
        mock_result.persisted_paths = ("path/to/task", "path/to/event")
        mock_bootstrap_instance = MagicMock()
        mock_bootstrap_instance.bootstrap.return_value = mock_result
        mock_bootstrap_cls.return_value = mock_bootstrap_instance

        trigger = BootstrapTrigger(enabled=True)
        payload = {
            "pull_request": {
                "number": 12,
                "head": {
                    "ref": "test/increment-c-activation",
                    "sha": "abc123",
                },
            }
        }
        control_root = Path("/tmp/test-control")
        coding_agent = MagicMock()
        reviewer = MagicMock()
        github_persistence = MagicMock()
        github_persistence._token = "test_token"
        github_persistence.hydrate = MagicMock()
        github_persistence.sync_paths = MagicMock(return_value=[])

        success, reason = apply_bootstrap(
            trigger,
            "pull_request",
            payload,
            control_root,
            "owner/repo",
            coding_agent,
            reviewer,
            github_persistence,
        )

        assert success is True
        assert "successful" in reason
        github_persistence.hydrate.assert_called_once()
        github_persistence.sync_paths.assert_called_once()

    @patch("development_automation.dispatcher_bootstrap.RealBootstrap")
    def test_apply_bootstrap_handles_unexpected_state(self, mock_bootstrap_cls):  # type: ignore
        """Bootstrap fails if task ends in unexpected state."""
        mock_result = MagicMock()
        from development_automation.schemas.v1 import LifecycleState

        mock_result.task_lifecycle_state = LifecycleState.WAITING_FOR_CI  # Wrong state!
        mock_bootstrap_instance = MagicMock()
        mock_bootstrap_instance.bootstrap.return_value = mock_result
        mock_bootstrap_cls.return_value = mock_bootstrap_instance

        trigger = BootstrapTrigger(enabled=True)
        payload = {
            "pull_request": {
                "number": 12,
                "head": {
                    "ref": "test/increment-c-activation",
                    "sha": "abc123",
                },
            }
        }
        control_root = Path("/tmp/test-control")
        coding_agent = MagicMock()
        reviewer = MagicMock()
        github_persistence = MagicMock()
        github_persistence._token = "test_token"

        success, reason = apply_bootstrap(
            trigger,
            "pull_request",
            payload,
            control_root,
            "owner/repo",
            coding_agent,
            reviewer,
            github_persistence,
        )

        assert success is False
        assert "unexpected state" in reason
