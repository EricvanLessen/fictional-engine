from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field, model_validator


class RawTelegramMessage(BaseModel):
    """Immutable representation of a Telegram message version."""

    fixture_id: str | None = None
    source_index: int = 0

    channel_id: int
    message_id: int
    message_date: datetime
    edit_date: datetime | None = None

    sender_name: str | None = None
    channel_title: str | None = None
    text: str | None = None
    caption: str | None = None

    media_metadata: dict[str, Any] = Field(default_factory=dict)
    telegram_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_message_payload(self) -> RawTelegramMessage:
        if self.message_date.tzinfo is None:
            raise ValueError("message_date must be timezone-aware")

        if self.edit_date is not None and self.edit_date.tzinfo is None:
            raise ValueError("edit_date must be timezone-aware")

        if self.text is None and self.caption is None and not self.media_metadata:
            raise ValueError("raw Telegram message must include text, caption, or media metadata")

        return self

    @property
    def occurrence_timestamp(self) -> datetime:
        return self.edit_date or self.message_date

    @property
    def content_hash(self) -> str:
        payload = {
            "channel_title": self.channel_title,
            "caption": self.caption,
            "media_metadata": self.media_metadata,
            "message_date": self.message_date.isoformat(),
            "sender_name": self.sender_name,
            "telegram_metadata": self.telegram_metadata,
            "text": self.text,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def deduplication_key(self) -> str:
        edit_token = self.edit_date.isoformat() if self.edit_date is not None else "original"
        return f"{edit_token}:{self.content_hash}"


class StoredRawTelegramMessage(BaseModel):
    """Stored immutable raw message version."""

    id: str
    channel_id: int
    message_id: int
    version: int
    previous_version_id: str | None = None

    message_date: datetime
    edit_date: datetime | None = None
    occurrence_timestamp: datetime

    sender_name: str | None = None
    channel_title: str | None = None
    text: str | None = None
    caption: str | None = None

    media_metadata: dict[str, Any]
    telegram_metadata: dict[str, Any]
    source_payload: dict[str, Any]

    content_hash: str
    deduplication_key: str
