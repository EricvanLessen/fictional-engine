from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from fictional_engine.domain.raw_messages import RawTelegramMessage, StoredRawTelegramMessage


@dataclass(frozen=True)
class IngestionResult:
    stored_message: StoredRawTelegramMessage
    inserted: bool


class RawMessageRepository(Protocol):
    def ingest_message(
        self, raw_message: RawTelegramMessage, source_payload: dict[str, Any]
    ) -> IngestionResult: ...

    def list_messages(self) -> list[StoredRawTelegramMessage]: ...

    def list_message_versions(
        self, channel_id: int, message_id: int
    ) -> list[StoredRawTelegramMessage]: ...


class RawMessageIngestionService:
    def __init__(self, repository: RawMessageRepository) -> None:
        self._repository = repository

    def ingest(
        self, raw_message: RawTelegramMessage, source_payload: dict[str, Any]
    ) -> IngestionResult:
        return self._repository.ingest_message(raw_message, source_payload)
