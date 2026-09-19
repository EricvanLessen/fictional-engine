"""Demonstrate complete Increment C cycle with bootstrap integration."""

from pathlib import Path

from development_automation.bootstrap_real import (
    BootstrapConfig,
    BootstrapResult,
    RealBootstrap,
)


def demo_phase_1() -> BootstrapResult:
    """Phase 1: Bootstrap creates task-0001."""
    print("\n=== PHASE 1: Bootstrap ===")
    config = BootstrapConfig(
        control_root=(Path.cwd() / "control").resolve(),
        task_id="task-0001",
        pr_number=13,  # Real future PR for Increment C
        branch="feat/increment-c-phase-2",
        initial_head_sha="26bce9faaaabbbbccccddddeeeefffff1111222",
        task_title="Task 0001: Increment C Real Cycle - Phase 1 Bootstrap",
        task_body=(
            "Bootstrap task for real Increment C cycle execution.\n"
            "Branch: feat/increment-c-phase-2\n"
            "Phases 2-5 will execute via GitHub Actions dispatcher."
        ),
        repository="EricvanLessen/fictional-engine",
        use_mock_agents=True,  # Use mocks to avoid API costs in demo
        use_github_persistence=False,
    )

    bootstrap = RealBootstrap(config)
    result = bootstrap.bootstrap()

    print("✓ Phase 1 Bootstrap")
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
    print("Task is ready for coding-provider dispatcher processing")
    print("\nPhases 2-5 would execute via GitHub Actions:")
    print("  Phase 2: Cline generates a PR with code changes")
    print("  Phase 3: CI workflow runs tests/checks")
    print("  Phase 4: OpenRouter reviews PR changes")
    print("  Phase 5: Task state updated based on review decision")
    print("\nNo live API calls made in this demo (mocks only)")
