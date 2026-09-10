from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fictional_engine.adapters.telegram.fixtures import FixtureEnvelope, load_fixture_envelopes
from fictional_engine.application.ingestion import IngestionResult, RawMessageIngestionService


@dataclass(frozen=True)
class ReplayEvent:
    fixture_id: str | None
    channel_id: int
    message_id: int
    version: int
    inserted: bool
    source_index: int
    occurrence_timestamp: str


@dataclass(frozen=True)
class ReplaySummary:
    processed_inputs: int
    inserted_versions: int
    duplicate_inputs: int
    replayed_events: list[ReplayEvent]


class ReplayService:
    def __init__(self, ingestion_service: RawMessageIngestionService) -> None:
        self._ingestion_service = ingestion_service

    def replay_fixtures(self, fixture_path: Path) -> ReplaySummary:
        envelopes = load_fixture_envelopes(fixture_path)
        replayed_events: list[ReplayEvent] = []
        inserted_versions = 0

        for envelope in envelopes:
            result = self._ingestion_service.ingest(envelope.raw_message, envelope.source_payload)
            inserted_versions += int(result.inserted)
            replayed_events.append(self._to_replay_event(envelope, result))

        processed_inputs = len(envelopes)
        return ReplaySummary(
            processed_inputs=processed_inputs,
            inserted_versions=inserted_versions,
            duplicate_inputs=processed_inputs - inserted_versions,
            replayed_events=replayed_events,
        )

    @staticmethod
    def _to_replay_event(envelope: FixtureEnvelope, result: IngestionResult) -> ReplayEvent:
        return ReplayEvent(
            fixture_id=envelope.raw_message.fixture_id,
            channel_id=result.stored_message.channel_id,
            message_id=result.stored_message.message_id,
            version=result.stored_message.version,
            inserted=result.inserted,
            source_index=envelope.raw_message.source_index,
            occurrence_timestamp=envelope.raw_message.occurrence_timestamp.isoformat(),
        )
