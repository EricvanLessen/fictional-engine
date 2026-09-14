from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from fictional_engine.application.ingestion import RawMessageIngestionService
from fictional_engine.application.parsing import DeterministicMessageParser
from fictional_engine.domain.parsing import MessageSource, ParsedMessage, ParseStatus
from fictional_engine.domain.raw_messages import RawTelegramMessage
from fictional_engine.domain.state import MessageProcessingResult


@dataclass(frozen=True)
class TelegramChannelPosition:
    channel_id: int
    last_message_id: int
    last_occurrence_timestamp: datetime


@dataclass(frozen=True)
class TelegramIncomingMessage:
    channel_id: int
    message_id: int
    message_date: datetime
    edit_date: datetime | None
    sender_name: str | None
    channel_title: str | None
    text: str | None
    caption: str | None
    media_metadata: dict[str, Any]
    telegram_metadata: dict[str, Any]
    source_payload: dict[str, Any]

    @property
    def occurrence_timestamp(self) -> datetime:
        return self.edit_date or self.message_date

    @property
    def is_edit(self) -> bool:
        return self.edit_date is not None

    def to_raw_message(self) -> RawTelegramMessage:
        return RawTelegramMessage.model_validate(
            {
                "channel_id": self.channel_id,
                "message_id": self.message_id,
                "message_date": self.message_date,
                "edit_date": self.edit_date,
                "sender_name": self.sender_name,
                "channel_title": self.channel_title,
                "text": self.text,
                "caption": self.caption,
                "media_metadata": self.media_metadata,
                "telegram_metadata": self.telegram_metadata,
            }
        )


class TelegramPositionRepository(Protocol):
    def get_position(self, channel_id: int) -> TelegramChannelPosition | None: ...

    def advance_position(
        self,
        *,
        channel_id: int,
        message_id: int,
        occurrence_timestamp: datetime,
    ) -> TelegramChannelPosition: ...


class TelegramUpdateSource(Protocol):
    def iter_messages(
        self,
        *,
        channel_id: int,
        after_position: TelegramChannelPosition | None,
    ) -> AsyncIterator[TelegramIncomingMessage]: ...


class TradingStateRepositoryPort(Protocol):
    def apply_parsed_message(
        self,
        parsed_message: ParsedMessage,
        source: MessageSource,
    ) -> MessageProcessingResult: ...


@dataclass(frozen=True)
class TelegramProcessingSummary:
    received: int
    inserted: int
    duplicates: int


class TelegramRealtimeProcessingService:
    def __init__(
        self,
        ingestion_service: RawMessageIngestionService,
        parser: DeterministicMessageParser,
        trading_state_repository: TradingStateRepositoryPort,
        position_repository: TelegramPositionRepository,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._ingestion_service = ingestion_service
        self._parser = parser
        self._trading_state_repository = trading_state_repository
        self._position_repository = position_repository
        self._logger = logger or logging.getLogger("fictional_engine.telegram")

    async def process_source(
        self,
        source: TelegramUpdateSource,
        *,
        channel_id: int,
    ) -> TelegramProcessingSummary:
        position = self._position_repository.get_position(channel_id)
        self._logger.info(
            "telegram.catchup_started",
            extra={
                "channel_id": channel_id,
                "has_position": position is not None,
                "last_message_id": None if position is None else position.last_message_id,
            },
        )

        received = 0
        inserted = 0
        duplicates = 0

        async for incoming in source.iter_messages(channel_id=channel_id, after_position=position):
            if incoming.channel_id != channel_id:
                continue

            received += 1
            self._logger.info(
                "telegram.message_edited" if incoming.is_edit else "telegram.message_received",
                extra={
                    "channel_id": incoming.channel_id,
                    "message_id": incoming.message_id,
                },
            )

            raw_message = incoming.to_raw_message()
            ingestion = self._ingestion_service.ingest(raw_message, incoming.source_payload)

            if not ingestion.inserted:
                duplicates += 1
                self._logger.info(
                    "ingestion.duplicate",
                    extra={
                        "channel_id": incoming.channel_id,
                        "message_id": incoming.message_id,
                        "version": ingestion.stored_message.version,
                    },
                )
                continue

            inserted += 1
            ingest_event = (
                "ingestion.versioned"
                if ingestion.stored_message.version > 1
                else "ingestion.inserted"
            )
            self._logger.info(
                ingest_event,
                extra={
                    "channel_id": incoming.channel_id,
                    "message_id": incoming.message_id,
                    "version": ingestion.stored_message.version,
                },
            )

            source_message = MessageSource.from_stored_message(ingestion.stored_message)
            parsed_message = self._parser.parse(source_message)
            self._logger.info(
                "parser.manual_review"
                if parsed_message.status == ParseStatus.MANUAL_REVIEW
                else "parser.completed",
                extra={
                    "channel_id": incoming.channel_id,
                    "message_id": incoming.message_id,
                    "version": ingestion.stored_message.version,
                    "classification": parsed_message.classification.value,
                    "event_count": len(parsed_message.events),
                },
            )

            processing = self._trading_state_repository.apply_parsed_message(
                parsed_message,
                source_message,
            )
            self._logger.info(
                "state.applied",
                extra={
                    "channel_id": incoming.channel_id,
                    "message_id": incoming.message_id,
                    "version": ingestion.stored_message.version,
                    "applied_event_count": len(processing.applied_events),
                },
            )

            outbox_count = sum(len(item.outbox_command_ids) for item in processing.applied_events)
            if outbox_count > 0:
                self._logger.info(
                    "outbox.created",
                    extra={
                        "channel_id": incoming.channel_id,
                        "message_id": incoming.message_id,
                        "version": ingestion.stored_message.version,
                        "count": outbox_count,
                    },
                )

            self._position_repository.advance_position(
                channel_id=incoming.channel_id,
                message_id=incoming.message_id,
                occurrence_timestamp=_ensure_utc(ingestion.stored_message.occurrence_timestamp),
            )

        self._logger.info(
            "telegram.catchup_completed",
            extra={
                "channel_id": channel_id,
                "received": received,
                "inserted": inserted,
                "duplicates": duplicates,
            },
        )
        return TelegramProcessingSummary(
            received=received,
            inserted=inserted,
            duplicates=duplicates,
        )


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
