from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fictional_engine.adapters.persistence.models import TelegramChannelPositionRecord
from fictional_engine.application.telegram_runtime import TelegramChannelPosition


class SqlAlchemyTelegramPositionRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_position(self, channel_id: int) -> TelegramChannelPosition | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(TelegramChannelPositionRecord).where(
                    TelegramChannelPositionRecord.channel_id == channel_id
                )
            )
            return None if record is None else _to_domain(record)

    def advance_position(
        self,
        *,
        channel_id: int,
        message_id: int,
        occurrence_timestamp: datetime,
    ) -> TelegramChannelPosition:
        with self._session_factory() as session:
            record = session.scalar(
                select(TelegramChannelPositionRecord).where(
                    TelegramChannelPositionRecord.channel_id == channel_id
                )
            )

            normalized_timestamp = _ensure_utc(occurrence_timestamp)
            if record is None:
                record = TelegramChannelPositionRecord(
                    id=str(uuid4()),
                    channel_id=channel_id,
                    last_message_id=message_id,
                    last_occurrence_timestamp=normalized_timestamp,
                    updated_at=datetime.now(UTC),
                )
                session.add(record)
            else:
                if _position_key(normalized_timestamp, message_id) >= _position_key(
                    _ensure_utc(record.last_occurrence_timestamp),
                    record.last_message_id,
                ):
                    record.last_message_id = message_id
                    record.last_occurrence_timestamp = normalized_timestamp
                record.updated_at = datetime.now(UTC)

            session.commit()
            return _to_domain(record)


def _position_key(occurrence_timestamp: datetime, message_id: int) -> tuple[datetime, int]:
    return occurrence_timestamp, message_id


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _to_domain(record: TelegramChannelPositionRecord) -> TelegramChannelPosition:
    return TelegramChannelPosition(
        channel_id=record.channel_id,
        last_message_id=record.last_message_id,
        last_occurrence_timestamp=_ensure_utc(record.last_occurrence_timestamp),
    )
