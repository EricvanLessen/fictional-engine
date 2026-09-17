"""CLI entry point for Increment C bootstrap and task management."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from development_automation.bootstrap import (
    TaskBootstrapConfig,
    bootstrap_task,
    create_copilot_dispatch_record,
)


def main() -> int:
    """Bootstrap a new task or manage task lifecycle."""
    parser = argparse.ArgumentParser(
        description="Increment C task bootstrap and lifecycle management",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: bootstrap
    bootstrap_cmd = subparsers.add_parser(
        "bootstrap",
        help="Create a new task for a pull request",
    )
    bootstrap_cmd.add_argument("--control-root", type=Path, default=Path("control"))
    bootstrap_cmd.add_argument("--task-id", type=str, default=None, help="Explicit task ID (task-NNNN)")
    bootstrap_cmd.add_argument("--branch", type=str, default="feat/development-automation-dispatcher")
    bootstrap_cmd.add_argument("--pr-number", type=int, required=True, help="Pull request number")
    bootstrap_cmd.add_argument("--head-sha", type=str, required=True, help="Commit SHA (40 hex chars)")

    # Subcommand: simulate-dispatch
    dispatch_cmd = subparsers.add_parser(
        "simulate-dispatch",
        help="Simulate Copilot dispatch (for testing)",
    )
    dispatch_cmd.add_argument("--control-root", type=Path, default=Path("control"))
    dispatch_cmd.add_argument("--task-id", type=str, required=True)
    dispatch_cmd.add_argument("--head-sha", type=str, required=True)
    dispatch_cmd.add_argument("--branch", type=str, default="feat/development-automation-dispatcher")
    dispatch_cmd.add_argument("--provider-run-id", type=str, default="run-copilot-mock-001")

    args = parser.parse_args()

    try:
        if args.command == "bootstrap":
            return _cmd_bootstrap(args)
        elif args.command == "simulate-dispatch":
            return _cmd_simulate_dispatch(args)
        else:
            parser.print_help()
            return 1
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def _cmd_bootstrap(args) -> int:
    """Execute bootstrap command."""
    config = TaskBootstrapConfig(
        task_id=args.task_id,
        branch=args.branch,
        pull_request_number=args.pr_number,
        head_sha=args.head_sha,
        created_at=datetime.now(tz=timezone.utc),
    )

    result = bootstrap_task(args.control_root, config)

    print(f"✓ Bootstrap complete")
    print(f"  task_id: {result.task_id}")
    print(f"  message_id: {result.message_id}")
    print(f"  event_path: {result.event_path}")
    print(f"  expected_head_sha: {result.expected_head_sha}")
    print(f"\nNext step: send PR event to dispatcher for task_id={result.task_id}")

    return 0


def _cmd_simulate_dispatch(args) -> int:
    """Execute simulate-dispatch command (for testing)."""
    event = create_copilot_dispatch_record(
        control_root=args.control_root,
        task_id=args.task_id,
        head_sha=args.head_sha,
        branch=args.branch,
        provider_run_id=args.provider_run_id,
        created_at=datetime.now(tz=timezone.utc),
    )

    print(f"✓ Copilot dispatched")
    print(f"  task_id: {event.task_id}")
    print(f"  message_id: {event.message_id}")
    print(f"  lifecycle_state: {event.lifecycle_state}")
    print(f"  provider_run_id: {event.provider_run_id}")
    print(f"\nNext step: wait for CI checks or send CI evidence to dispatcher")

    return 0


if __name__ == "__main__":
    sys.exit(main())
