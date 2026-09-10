from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from fictional_engine.domain.raw_messages import RawTelegramMessage


@dataclass(frozen=True)
class FixtureEnvelope:
    raw_message: RawTelegramMessage
    source_payload: dict[str, Any]


class FixtureRecord(BaseModel):
    fixture_id: str
    channel_id: int
    message_id: int
    message_date: str
    edit_date: str | None = None
    sender_name: str | None = None
    channel_title: str | None = None
    text: str | None = None
    caption: str | None = None
    media_metadata: dict[str, Any] = Field(default_factory=dict)
    telegram_metadata: dict[str, Any] = Field(default_factory=dict)

    def to_raw_message(self, source_index: int) -> RawTelegramMessage:
        return RawTelegramMessage.model_validate(
            {
                "fixture_id": self.fixture_id,
                "source_index": source_index,
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


class FixtureBundle(BaseModel):
    fixtures: list[FixtureRecord]


def load_fixture_envelopes(path: Path) -> list[FixtureEnvelope]:
    records = _load_fixture_records(path)
    envelopes = [
        FixtureEnvelope(
            raw_message=record.to_raw_message(index),
            source_payload=source_payload,
        )
        for index, (record, source_payload) in enumerate(records)
    ]
    return sorted(envelopes, key=_fixture_sort_key)


def _load_fixture_records(path: Path) -> list[tuple[FixtureRecord, dict[str, Any]]]:
    if path.is_dir():
        records: list[tuple[FixtureRecord, dict[str, Any]]] = []
        for file_path in sorted(path.glob("*.json")):
            records.extend(_load_fixture_records(file_path))
        return records

    raw_payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw_payload, list):
        raw_records = raw_payload
    else:
        raw_records = raw_payload["fixtures"]

    return [
        (FixtureRecord.model_validate(raw_record), deepcopy(raw_record))
        for raw_record in raw_records
    ]


def _fixture_sort_key(envelope: FixtureEnvelope) -> tuple[object, ...]:
    raw_message = envelope.raw_message
    return (
        raw_message.occurrence_timestamp,
        raw_message.channel_id,
        raw_message.message_id,
        raw_message.source_index,
    )
