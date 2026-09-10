from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, DateTime


class Base(DeclarativeBase):
    pass


class RawTelegramMessageRecord(Base):
    __tablename__ = "raw_telegram_message_versions"
    __table_args__ = (
        UniqueConstraint("channel_id", "message_id", "version", name="uq_raw_message_identity"),
        UniqueConstraint(
            "channel_id",
            "message_id",
            "deduplication_key",
            name="uq_raw_message_deduplication",
        ),
        Index("ix_raw_message_occurrence", "occurrence_timestamp", "channel_id", "message_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[int] = mapped_column(index=True)
    message_id: Mapped[int] = mapped_column(index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("raw_telegram_message_versions.id"), nullable=True
    )

    message_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edit_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurrence_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    sender_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)

    media_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    telegram_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    source_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    deduplication_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    previous_version: Mapped[RawTelegramMessageRecord | None] = relationship(remote_side=[id])
