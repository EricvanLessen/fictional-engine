from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fictional_engine.adapters.telegram.telethon_client import _message_sort_key


@dataclass
class _StubMessage:
    id: int
    date: datetime
    edit_date: datetime | None


def test_message_sort_key_prefers_edit_date_then_message_id() -> None:
    old = _StubMessage(
        id=100,
        date=datetime(2026, 9, 14, 12, 0, tzinfo=UTC),
        edit_date=None,
    )
    edited = _StubMessage(
        id=100,
        date=datetime(2026, 9, 14, 12, 0, tzinfo=UTC),
        edit_date=datetime(2026, 9, 14, 12, 5, tzinfo=UTC),
    )
    later = _StubMessage(
        id=101,
        date=datetime(2026, 9, 14, 12, 4, tzinfo=UTC),
        edit_date=None,
    )

    ordered = sorted([later, old, edited], key=_message_sort_key)

    assert [item.id for item in ordered] == [100, 101, 100]
    assert ordered[2].edit_date is not None
