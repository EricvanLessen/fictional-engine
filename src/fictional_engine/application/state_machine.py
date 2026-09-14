from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fictional_engine.adapters.telegram.fixtures import load_fixture_envelopes
from fictional_engine.application.ingestion import IngestionResult, RawMessageIngestionService
from fictional_engine.application.parsing import DeterministicMessageParser
from fictional_engine.domain.parsing import MessageSource, ParsedMessage
from fictional_engine.domain.raw_messages import RawTelegramMessage
from fictional_engine.domain.state import MessageProcessingResult


class TradingStateRepository(Protocol):
    def apply_parsed_message(
        self, parsed_message: ParsedMessage, source: MessageSource
    ) -> MessageProcessingResult: ...


@dataclass(frozen=True)
class ProcessedMessage:
    ingestion: IngestionResult
    parsing: ParsedMessage
    processing: MessageProcessingResult


@dataclass(frozen=True)
class StatefulReplaySummary:
    processed_inputs: int
    inserted_versions: int
    duplicate_inputs: int
    processed_messages: tuple[ProcessedMessage, ...]


class StatefulMessageProcessingService:
    def __init__(
        self,
        ingestion_service: RawMessageIngestionService,
        parser: DeterministicMessageParser,
        trading_state_repository: TradingStateRepository,
    ) -> None:
        self._ingestion_service = ingestion_service
        self._parser = parser
        self._trading_state_repository = trading_state_repository

    def process_raw_message(
        self, raw_message: RawTelegramMessage, source_payload: dict[str, object]
    ) -> ProcessedMessage:
        ingestion = self._ingestion_service.ingest(raw_message, source_payload)
        source = MessageSource.from_stored_message(ingestion.stored_message)
        parsed_message = self._parser.parse(source)
        processing = self._trading_state_repository.apply_parsed_message(parsed_message, source)
        return ProcessedMessage(ingestion=ingestion, parsing=parsed_message, processing=processing)


class StatefulReplayService:
    def __init__(self, processor: StatefulMessageProcessingService) -> None:
        self._processor = processor

    def replay_fixtures(self, fixture_path: Path) -> StatefulReplaySummary:
        envelopes = load_fixture_envelopes(fixture_path)
        processed_messages = tuple(
            self._processor.process_raw_message(envelope.raw_message, envelope.source_payload)
            for envelope in envelopes
        )
        inserted_versions = sum(int(message.ingestion.inserted) for message in processed_messages)
        return StatefulReplaySummary(
            processed_inputs=len(envelopes),
            inserted_versions=inserted_versions,
            duplicate_inputs=len(envelopes) - inserted_versions,
            processed_messages=processed_messages,
        )
