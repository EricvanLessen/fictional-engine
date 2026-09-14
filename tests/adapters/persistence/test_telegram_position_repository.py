from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from fictional_engine.adapters.persistence.telegram_position_repository import (
    SqlAlchemyTelegramPositionRepository,
)


def test_position_repository_persists_and_advances_monotonically(
    migrated_session_factory: sessionmaker[Session],
) -> None:
    repository = SqlAlchemyTelegramPositionRepository(migrated_session_factory)

    assert repository.get_position(777000) is None

    first = repository.advance_position(
        channel_id=777000,
        message_id=101,
        occurrence_timestamp=datetime(2026, 9, 14, 10, 0, tzinfo=UTC),
    )
    assert first.last_message_id == 101

    # Older position updates must not move the checkpoint backwards.
    second = repository.advance_position(
        channel_id=777000,
        message_id=99,
        occurrence_timestamp=datetime(2026, 9, 14, 9, 0, tzinfo=UTC),
    )
    assert second.last_message_id == 101

    third = repository.advance_position(
        channel_id=777000,
        message_id=102,
        occurrence_timestamp=datetime(2026, 9, 14, 11, 0, tzinfo=UTC),
    )
    assert third.last_message_id == 102

    reloaded = SqlAlchemyTelegramPositionRepository(migrated_session_factory).get_position(777000)
    assert reloaded is not None
    assert reloaded.last_message_id == 102
