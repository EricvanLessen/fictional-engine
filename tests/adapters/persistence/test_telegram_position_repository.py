from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from fictional_engine.adapters.persistence.telegram_position_repository import (
    SqlAlchemyTelegramPositionRepository,
)


def test_position_repository_persists_monotonic_checkpoint(
    migrated_session_factory: sessionmaker[Session],
) -> None:
    repository = SqlAlchemyTelegramPositionRepository(migrated_session_factory)

    assert repository.get_position(777000) is None

    first = repository.advance_position(
        channel_id=777000,
        message_id=100,
        occurrence_timestamp=datetime(2026, 9, 14, 10, 0, tzinfo=UTC),
    )
    assert first.last_message_id == 100

    repository.advance_position(
        channel_id=777000,
        message_id=99,
        occurrence_timestamp=datetime(2026, 9, 14, 9, 59, tzinfo=UTC),
    )
    assert repository.get_position(777000) is not None
    assert repository.get_position(777000).last_message_id == 100  # type: ignore[union-attr]

    repository.advance_position(
        channel_id=777000,
        message_id=101,
        occurrence_timestamp=datetime(2026, 9, 14, 10, 1, tzinfo=UTC),
    )
    assert repository.get_position(777000).last_message_id == 101  # type: ignore[union-attr]
