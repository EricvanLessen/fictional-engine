"""Demonstrate complete Increment C cycle with bootstrap integration."""
import os
from pathlib import Path
from development_automation.bootstrap_real import RealBootstrap, BootstrapConfig


def demo_phase_1():
    """Phase 1: Bootstrap creates task-0001."""
    print("\n=== PHASE 1: Bootstrap ===")
    config = BootstrapConfig(
        control_root=Path(
            "/Users/ericvanlessen/Development/fictional-engine/control"
        ).resolve(),
        task_id="task-0001",
        pr_number=13,  # Real future PR for Increment C
        branch="feat/increment-c-phase-2",
        initial_head_sha="26bce9faaaabbbbccccddddeeeefffff1111222",
        task_title="Task 0001: Increment C Real Cycle - Phase 1 Bootstrap",
        task_body="Bootstrap task for real Increment C cycle execution.\nBranch: feat/increment-c-phase-2\nPhases 2-5 will execute via GitHub Actions dispatcher.",
        repository="EricvanLessen/fictional-engine",
        use_mock_agents=True,  # Use mocks to avoid API costs in demo
        use_github_persistence=False,
    )

    bootstrap = RealBootstrap(config)
    result = bootstrap.bootstrap()

    print(f"✓ Phase 1 Bootstrap")
    print(f"  Task ID: {result.task_id}")
    print(f"  State: {result.task_lifecycle_state}")
    print(f"  Action: {result.initial_action}")
    print(f"  Persisted paths: {len(result.persisted_paths)}")
    for i, path in enumerate(result.persisted_paths[:3]):
        print(f"    [{i+1}] {path}")

    return result


if __name__ == "__main__":
    result = demo_phase_1()

    print("\n=== Phase 1 Execution Summary ===")
    print(
        f"Bootstrap successfully created task-0001 in {result.task_lifecycle_state} state"
    )
    print(f"Task is ready for Copilot dispatcher processing")
    print(f"\nPhases 2-5 would execute via GitHub Actions:")
    print(f"  Phase 2: Copilot generates PR with code changes")
    print(f"  Phase 3: CI workflow runs tests/checks")
    print(f"  Phase 4: OpenAI reviews PR changes")
    print(f"  Phase 5: Task state updated based on review decision")
    print(f"\nNo live API calls made in this demo (mocks only)")
