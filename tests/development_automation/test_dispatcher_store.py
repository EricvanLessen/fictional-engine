from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from development_automation.dispatcher_store import DispatcherStoreError, FileDispatcherStore


def _store(tmp_path: Path) -> FileDispatcherStore:
    control_root = tmp_path / "control"
    (control_root / "runs").mkdir(parents=True, exist_ok=True)
    return FileDispatcherStore(control_root)


def test_completed_to_running_transition_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    intent, _ = store.claim_intent(
        dedupe_key="dispatch:task-0042",
        operation="dispatch-task",
        task_id="task-0042",
        head_sha="a" * 40,
        branch="feat/development-automation-protocol",
    )
    running = store.transition_intent(
        intent.intent_id,
        "running",
        expected_status="claimed",
        expected_version=intent.version,
    )
    completed = store.transition_intent(
        running.intent_id,
        "completed",
        expected_status="running",
        expected_version=running.version,
    )

    with pytest.raises(DispatcherStoreError):
        store.transition_intent(
            completed.intent_id,
            "running",
            expected_status="completed",
            expected_version=completed.version,
        )


def test_lease_expiry_allows_safe_reclaim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    intent, _ = store.claim_intent(
        dedupe_key="dispatch:task-0042",
        operation="dispatch-task",
        task_id="task-0042",
        head_sha="a" * 40,
        branch="feat/development-automation-protocol",
    )

    t0 = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    leased = store.lease_unfinished_intent(worker_id="worker-a", lease_seconds=30, now=t0)
    assert leased is not None
    assert leased.intent_id == intent.intent_id
    assert leased.lease_owner == "worker-a"

    still_leased = store.lease_unfinished_intent(
        worker_id="worker-b",
        lease_seconds=30,
        now=t0 + timedelta(seconds=10),
    )
    assert still_leased is None

    reclaimed = store.lease_unfinished_intent(
        worker_id="worker-b",
        lease_seconds=30,
        now=t0 + timedelta(seconds=31),
    )
    assert reclaimed is not None
    assert reclaimed.intent_id == intent.intent_id
    assert reclaimed.lease_owner == "worker-b"


def test_cas_version_mismatch_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    intent, _ = store.claim_intent(
        dedupe_key="dispatch:task-0042",
        operation="dispatch-task",
        task_id="task-0042",
        head_sha="a" * 40,
        branch="feat/development-automation-protocol",
    )

    with pytest.raises(DispatcherStoreError):
        store.transition_intent(
            intent.intent_id,
            "running",
            expected_status="claimed",
            expected_version=intent.version + 1,
        )
