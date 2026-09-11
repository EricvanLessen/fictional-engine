from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from fictional_engine.adapters.persistence.models import RawTelegramMessageRecord
from fictional_engine.application.ingestion import IngestionResult
from fictional_engine.domain.raw_messages import RawTelegramMessage, StoredRawTelegramMessage


class SqlAlchemyRawMessageRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def ingest_message(
        self, raw_message: RawTelegramMessage, source_payload: dict[str, Any]
    ) -> IngestionResult:
        with self._session_factory() as session:
            existing = self._find_existing(session, raw_message)
            if existing is not None:
                return IngestionResult(stored_message=_to_domain(existing), inserted=False)

            latest = self._find_latest_version(
                session, raw_message.channel_id, raw_message.message_id
            )
            record = RawTelegramMessageRecord(
                id=str(uuid4()),
                channel_id=raw_message.channel_id,
                message_id=raw_message.message_id,
                version=1 if latest is None else latest.version + 1,
                previous_version_id=None if latest is None else latest.id,
                message_date=raw_message.message_date,
                edit_date=raw_message.edit_date,
                occurrence_timestamp=raw_message.occurrence_timestamp,
                sender_name=raw_message.sender_name,
                channel_title=raw_message.channel_title,
                text=raw_message.text,
                caption=raw_message.caption,
                media_metadata=raw_message.media_metadata,
                telegram_metadata=raw_message.telegram_metadata,
                source_payload=source_payload,
                content_hash=raw_message.content_hash,
                deduplication_key=raw_message.deduplication_key,
            )
            session.add(record)

            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = self._find_existing(session, raw_message)
                if existing is None:
                    raise
                return IngestionResult(stored_message=_to_domain(existing), inserted=False)

            return IngestionResult(stored_message=_to_domain(record), inserted=True)

    def list_messages(self) -> list[StoredRawTelegramMessage]:
        with self._session_factory() as session:
            statement = select(RawTelegramMessageRecord).order_by(
                RawTelegramMessageRecord.occurrence_timestamp,
                RawTelegramMessageRecord.channel_id,
                RawTelegramMessageRecord.message_id,
                RawTelegramMessageRecord.version,
            )
            return [_to_domain(record) for record in session.scalars(statement)]

    def list_message_versions(
        self, channel_id: int, message_id: int
    ) -> list[StoredRawTelegramMessage]:
        with self._session_factory() as session:
            statement = (
                select(RawTelegramMessageRecord)
                .where(
                    RawTelegramMessageRecord.channel_id == channel_id,
                    RawTelegramMessageRecord.message_id == message_id,
                )
                .order_by(RawTelegramMessageRecord.version)
            )
            return [_to_domain(record) for record in session.scalars(statement)]

    @staticmethod
    def _find_existing(
        session: Session, raw_message: RawTelegramMessage
    ) -> RawTelegramMessageRecord | None:
        statement = select(RawTelegramMessageRecord).where(
            RawTelegramMessageRecord.channel_id == raw_message.channel_id,
            RawTelegramMessageRecord.message_id == raw_message.message_id,
            RawTelegramMessageRecord.deduplication_key == raw_message.deduplication_key,
        )
        return session.scalar(statement)

    @staticmethod
    def _find_latest_version(
        session: Session, channel_id: int, message_id: int
    ) -> RawTelegramMessageRecord | None:
        statement = (
            select(RawTelegramMessageRecord)
            .where(
                RawTelegramMessageRecord.channel_id == channel_id,
                RawTelegramMessageRecord.message_id == message_id,
            )
            .order_by(RawTelegramMessageRecord.version.desc())
            .limit(1)
        )
        return session.scalar(statement)


def _to_domain(record: RawTelegramMessageRecord) -> StoredRawTelegramMessage:
    return StoredRawTelegramMessage(
        id=record.id,
        channel_id=record.channel_id,
        message_id=record.message_id,
        version=record.version,
        previous_version_id=record.previous_version_id,
        message_date=record.message_date,
        edit_date=record.edit_date,
        occurrence_timestamp=record.occurrence_timestamp,
        sender_name=record.sender_name,
        channel_title=record.channel_title,
        text=record.text,
        caption=record.caption,
        media_metadata=record.media_metadata,
        telegram_metadata=record.telegram_metadata,
        source_payload=record.source_payload,
        content_hash=record.content_hash,
        deduplication_key=record.deduplication_key,
    )
