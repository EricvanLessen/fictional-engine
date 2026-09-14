from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from fictional_engine.application.ingestion import RawMessageIngestionService
from fictional_engine.application.parsing import DeterministicMessageParser
from fictional_engine.domain.parsing import MessageSource, ParsedMessage
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
            "telegram_catchup_start",
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
                self._logger.info(
                    "telegram_message_ignored_unconfigured_channel",
                    extra={
                        "configured_channel_id": channel_id,
                        "incoming_channel_id": incoming.channel_id,
                        "message_id": incoming.message_id,
                    },
                )
                continue

            received += 1
            raw_message = incoming.to_raw_message()
            ingestion = self._ingestion_service.ingest(raw_message, incoming.source_payload)

            self._logger.info(
                "raw_message_ingested",
                extra={
                    "channel_id": raw_message.channel_id,
                    "message_id": raw_message.message_id,
                    "version": ingestion.stored_message.version,
                    "inserted": ingestion.inserted,
                    "is_edit": raw_message.edit_date is not None,
                },
            )

            if not ingestion.inserted:
                duplicates += 1
                self._logger.info(
                    "raw_message_duplicate_detected",
                    extra={
                        "channel_id": raw_message.channel_id,
                        "message_id": raw_message.message_id,
                        "version": ingestion.stored_message.version,
                    },
                )
                continue

            inserted += 1
            source_message = MessageSource.from_stored_message(ingestion.stored_message)
            parsed = self._parser.parse(source_message)
            self._logger.info(
                "message_parsed",
                extra={
                    "channel_id": raw_message.channel_id,
                    "message_id": raw_message.message_id,
                    "version": ingestion.stored_message.version,
                    "classification": parsed.classification.value,
                    "status": parsed.status.value,
                    "event_count": len(parsed.events),
                },
            )

            processing = self._trading_state_repository.apply_parsed_message(parsed, source_message)
            outbox_count = sum(len(item.outbox_command_ids) for item in processing.applied_events)
            manual_review_count = sum(
                int(item.manual_review_id is not None) for item in processing.applied_events
            )
            self._logger.info(
                "state_machine_applied",
                extra={
                    "channel_id": raw_message.channel_id,
                    "message_id": raw_message.message_id,
                    "version": ingestion.stored_message.version,
                    "applied_event_count": len(processing.applied_events),
                    "manual_review_count": manual_review_count,
                    "outbox_command_count": outbox_count,
                },
            )
            if manual_review_count > 0:
                self._logger.info(
                    "manual_review_recorded",
                    extra={
                        "channel_id": raw_message.channel_id,
                        "message_id": raw_message.message_id,
                        "version": ingestion.stored_message.version,
                        "count": manual_review_count,
                    },
                )
            if outbox_count > 0:
                self._logger.info(
                    "outbox_commands_created",
                    extra={
                        "channel_id": raw_message.channel_id,
                        "message_id": raw_message.message_id,
                        "version": ingestion.stored_message.version,
                        "count": outbox_count,
                    },
                )

            new_position = self._position_repository.advance_position(
                channel_id=raw_message.channel_id,
                message_id=raw_message.message_id,
                occurrence_timestamp=ingestion.stored_message.occurrence_timestamp,
            )
            self._logger.info(
                "telegram_position_advanced",
                extra={
                    "channel_id": new_position.channel_id,
                    "last_message_id": new_position.last_message_id,
                },
            )

        return TelegramProcessingSummary(
            received=received,
            inserted=inserted,
            duplicates=duplicates,
        )
