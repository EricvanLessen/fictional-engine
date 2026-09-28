from __future__ import annotations

import fcntl
import json
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from development_automation.errors import DevelopmentAutomationError


class DispatcherStoreError(DevelopmentAutomationError):
    """Raised when the dispatcher persistent store has invalid or inconsistent data."""


TERMINAL_INTENT_STATES = {"completed", "failed", "blocked"}
NON_TERMINAL_INTENT_STATES = {"claimed", "running"}

ALLOWED_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "claimed": {"running", "completed", "failed", "blocked", "unknown"},
    "running": {"claimed", "running", "completed", "failed", "blocked", "unknown"},
    "completed": set(),
    "failed": set(),
    "blocked": set(),
    "unknown": set(),
}


@dataclass(frozen=True)
class DispatchIntent:
    intent_id: str
    dedupe_key: str
    operation: str
    task_id: str | None
    head_sha: str | None
    branch: str | None
    correlation_id: str
    status: str
    version: int
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    provider_run_id: str | None = None


TERMINAL_INTENT_STATES = TERMINAL_INTENT_STATES.union({"unknown"})


class FileDispatcherStore:
    def __init__(
        self,
        control_root: Path,
        *,
        after_write: Callable[[tuple[Path, ...]], None] | None = None,
    ) -> None:
        self._control_root = control_root
        self._runs_dir = control_root / "runs"
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        self._events_path = self._runs_dir / "dispatcher-events.jsonl"
        self._lock_path = self._runs_dir / ".dispatcher-events.lock"
        self._after_write = after_write

    def _append_record_locked(self, record: dict[str, object]) -> None:
        with self._events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        if self._after_write is not None:
            self._after_write((self._events_path,))

    def _iter_records(self) -> Iterable[dict[str, object]]:
        if not self._events_path.exists():
            return ()
        records: list[dict[str, object]] = []
        for line in self._events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise DispatcherStoreError("dispatcher event line is not an object")
            records.append(payload)
        return records

    def _load_state_locked(self) -> tuple[set[str], dict[str, DispatchIntent]]:
        deliveries: set[str] = set()
        intents_by_id: dict[str, DispatchIntent] = {}
        for record in self._iter_records():
            kind = record.get("kind")
            if kind == "delivery_seen":
                delivery_id = record.get("delivery_id")
                if isinstance(delivery_id, str):
                    deliveries.add(delivery_id)
                continue

            if kind not in {
                "intent_claimed",
                "intent_leased",
                "intent_running",
                "intent_completed",
                "intent_failed",
                "intent_blocked",
                "intent_unknown",
            }:
                continue

            intent_id = record.get("intent_id")
            dedupe_key = record.get("dedupe_key")
            operation = record.get("operation")
            task_id = record.get("task_id")
            head_sha = record.get("head_sha")
            branch = record.get("branch")
            correlation_id = record.get("correlation_id")
            status = record.get("status")
            version = record.get("version")
            lease_owner = record.get("lease_owner")
            lease_expires_at = record.get("lease_expires_at")
            provider_run_id = record.get("provider_run_id")
            if not isinstance(intent_id, str):
                raise DispatcherStoreError("dispatcher intent record missing intent_id")
            if not isinstance(dedupe_key, str):
                raise DispatcherStoreError("dispatcher intent record missing dedupe_key")
            if not isinstance(operation, str):
                raise DispatcherStoreError("dispatcher intent record missing operation")
            if not isinstance(correlation_id, str):
                raise DispatcherStoreError("dispatcher intent record missing correlation_id")
            if not isinstance(status, str):
                raise DispatcherStoreError(
                    "dispatcher intent record is missing required string fields"
                )
            if not isinstance(version, int):
                raise DispatcherStoreError("dispatcher intent record missing version")

            task_value = task_id if isinstance(task_id, str) else None
            head_value = head_sha if isinstance(head_sha, str) else None
            branch_value = branch if isinstance(branch, str) else None
            lease_owner_value = lease_owner if isinstance(lease_owner, str) else None
            lease_expires_at_value = lease_expires_at if isinstance(lease_expires_at, str) else None
            provider_run_id_value = provider_run_id if isinstance(provider_run_id, str) else None

            intents_by_id[intent_id] = DispatchIntent(
                intent_id=intent_id,
                dedupe_key=dedupe_key,
                operation=operation,
                task_id=task_value,
                head_sha=head_value,
                branch=branch_value,
                correlation_id=correlation_id,
                status=status,
                version=version,
                lease_owner=lease_owner_value,
                lease_expires_at=lease_expires_at_value,
                provider_run_id=provider_run_id_value,
            )
        return deliveries, intents_by_id

    def _is_lease_expired(self, lease_expires_at: str | None, now: datetime) -> bool:
        if lease_expires_at is None:
            return True
        try:
            lease_deadline = datetime.fromisoformat(lease_expires_at)
        except ValueError as exc:
            raise DispatcherStoreError("lease_expires_at is not a valid ISO timestamp") from exc
        if lease_deadline.tzinfo is None or lease_deadline.utcoffset() is None:
            raise DispatcherStoreError("lease_expires_at must be timezone-aware")
        return lease_deadline <= now

    def _lock(self) -> BinaryIO:
        lock_handle = self._lock_path.open("a+b")
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        return lock_handle

    def _unlock(self, lock_handle: BinaryIO) -> None:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    def remember_delivery(self, delivery_id: str) -> bool:
        lock_handle = self._lock()
        try:
            deliveries, _ = self._load_state_locked()
            if delivery_id in deliveries:
                return False
            self._append_record_locked(
                {
                    "kind": "delivery_seen",
                    "delivery_id": delivery_id,
                    "recorded_at": datetime.now(UTC).isoformat(),
                }
            )
            return True
        finally:
            self._unlock(lock_handle)

    def claim_intent(
        self,
        dedupe_key: str,
        operation: str,
        task_id: str | None,
        head_sha: str | None,
        branch: str | None,
    ) -> tuple[DispatchIntent, bool]:
        lock_handle = self._lock()
        try:
            _, intents_by_id = self._load_state_locked()
            for intent in intents_by_id.values():
                if intent.dedupe_key == dedupe_key:
                    return intent, False

            intent_id = str(uuid.uuid4())
            correlation_id = str(uuid.uuid4())
            intent = DispatchIntent(
                intent_id=intent_id,
                dedupe_key=dedupe_key,
                operation=operation,
                task_id=task_id,
                head_sha=head_sha,
                branch=branch,
                correlation_id=correlation_id,
                status="claimed",
                version=1,
            )
            self._append_record_locked(
                {
                    "kind": "intent_claimed",
                    "recorded_at": datetime.now(UTC).isoformat(),
                    "intent_id": intent.intent_id,
                    "dedupe_key": intent.dedupe_key,
                    "operation": intent.operation,
                    "task_id": intent.task_id,
                    "head_sha": intent.head_sha,
                    "branch": intent.branch,
                    "correlation_id": intent.correlation_id,
                    "status": intent.status,
                    "version": intent.version,
                    "lease_owner": intent.lease_owner,
                    "lease_expires_at": intent.lease_expires_at,
                    "provider_run_id": intent.provider_run_id,
                }
            )
            return intent, True
        finally:
            self._unlock(lock_handle)

    def transition_intent(
        self,
        intent_id: str,
        status: str,
        *,
        expected_status: str | None = None,
        expected_version: int | None = None,
        provider_run_id: str | None = None,
    ) -> DispatchIntent:
        if status not in NON_TERMINAL_INTENT_STATES.union(TERMINAL_INTENT_STATES):
            raise DispatcherStoreError(f"unsupported dispatcher intent status: {status}")

        lock_handle = self._lock()
        try:
            _, intents_by_id = self._load_state_locked()
            intent = intents_by_id.get(intent_id)
            if intent is None:
                raise DispatcherStoreError(f"unknown intent_id {intent_id!r}")
            if expected_status is not None and intent.status != expected_status:
                raise DispatcherStoreError(
                    f"expected status {expected_status!r} but found {intent.status!r}"
                )
            if expected_version is not None and intent.version != expected_version:
                raise DispatcherStoreError(
                    f"expected version {expected_version} but found {intent.version}"
                )

            allowed_targets = ALLOWED_STATUS_TRANSITIONS.get(intent.status)
            if allowed_targets is None or status not in allowed_targets:
                raise DispatcherStoreError(
                    f"illegal transition from {intent.status!r} to {status!r}"
                )

            next_provider_run_id = provider_run_id or intent.provider_run_id

            updated_intent = DispatchIntent(
                intent_id=intent.intent_id,
                dedupe_key=intent.dedupe_key,
                operation=intent.operation,
                task_id=intent.task_id,
                head_sha=intent.head_sha,
                branch=intent.branch,
                correlation_id=intent.correlation_id,
                status=status,
                version=intent.version + 1,
                lease_owner=None if status in TERMINAL_INTENT_STATES else intent.lease_owner,
                lease_expires_at=(
                    None if status in TERMINAL_INTENT_STATES else intent.lease_expires_at
                ),
                provider_run_id=next_provider_run_id,
            )
            self._append_record_locked(
                {
                    "kind": f"intent_{status}",
                    "recorded_at": datetime.now(UTC).isoformat(),
                    "intent_id": updated_intent.intent_id,
                    "dedupe_key": updated_intent.dedupe_key,
                    "operation": updated_intent.operation,
                    "task_id": updated_intent.task_id,
                    "head_sha": updated_intent.head_sha,
                    "branch": updated_intent.branch,
                    "correlation_id": updated_intent.correlation_id,
                    "status": updated_intent.status,
                    "version": updated_intent.version,
                    "lease_owner": updated_intent.lease_owner,
                    "lease_expires_at": updated_intent.lease_expires_at,
                    "provider_run_id": updated_intent.provider_run_id,
                }
            )
            return updated_intent
        finally:
            self._unlock(lock_handle)

    def lease_unfinished_intent(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 120,
        now: datetime | None = None,
    ) -> DispatchIntent | None:
        if lease_seconds <= 0:
            raise DispatcherStoreError("lease_seconds must be > 0")

        lock_handle = self._lock()
        try:
            _, intents_by_id = self._load_state_locked()
            lease_now = now or datetime.now(UTC)
            for intent in sorted(intents_by_id.values(), key=lambda item: item.intent_id):
                if intent.status not in NON_TERMINAL_INTENT_STATES:
                    continue
                if intent.lease_owner and not self._is_lease_expired(
                    intent.lease_expires_at,
                    lease_now,
                ):
                    continue

                lease_expires_at = (lease_now.timestamp() + lease_seconds)
                lease_deadline = datetime.fromtimestamp(lease_expires_at, tz=UTC).isoformat()
                leased_intent = DispatchIntent(
                    intent_id=intent.intent_id,
                    dedupe_key=intent.dedupe_key,
                    operation=intent.operation,
                    task_id=intent.task_id,
                    head_sha=intent.head_sha,
                    branch=intent.branch,
                    correlation_id=intent.correlation_id,
                    status=intent.status,
                    version=intent.version + 1,
                    lease_owner=worker_id,
                    lease_expires_at=lease_deadline,
                    provider_run_id=intent.provider_run_id,
                )
                self._append_record_locked(
                    {
                        "kind": "intent_leased",
                        "recorded_at": lease_now.isoformat(),
                        "intent_id": leased_intent.intent_id,
                        "dedupe_key": leased_intent.dedupe_key,
                        "operation": leased_intent.operation,
                        "task_id": leased_intent.task_id,
                        "head_sha": leased_intent.head_sha,
                        "branch": leased_intent.branch,
                        "correlation_id": leased_intent.correlation_id,
                        "status": leased_intent.status,
                        "version": leased_intent.version,
                        "lease_owner": leased_intent.lease_owner,
                        "lease_expires_at": leased_intent.lease_expires_at,
                        "provider_run_id": leased_intent.provider_run_id,
                    }
                )
                return leased_intent
            return None
        finally:
            self._unlock(lock_handle)

    def list_unfinished_intents(self) -> list[DispatchIntent]:
        lock_handle = self._lock()
        try:
            _, intents_by_id = self._load_state_locked()
            return [
                intent
                for intent in intents_by_id.values()
                if intent.status in NON_TERMINAL_INTENT_STATES
            ]
        finally:
            self._unlock(lock_handle)

    def get_intent(self, intent_id: str) -> DispatchIntent | None:
        lock_handle = self._lock()
        try:
            _, intents_by_id = self._load_state_locked()
            return intents_by_id.get(intent_id)
        finally:
            self._unlock(lock_handle)