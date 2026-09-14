from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
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


class TelegramChannelPositionRecord(Base):
    __tablename__ = "telegram_channel_positions"
    __table_args__ = (UniqueConstraint("channel_id", name="uq_telegram_channel_positions_channel"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    last_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    last_occurrence_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SessionRecord(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "session_date",
            "instrument",
            name="uq_session_channel_date_instrument",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[int] = mapped_column(index=True)
    session_date: Mapped[date] = mapped_column(Date(), nullable=False, index=True)
    instrument: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NoTradingDayRecord(Base):
    __tablename__ = "no_trading_days"
    __table_args__ = (
        UniqueConstraint("channel_id", "session_date", name="uq_no_trading_day_channel_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel_id: Mapped[int] = mapped_column(index=True)
    session_date: Mapped[date] = mapped_column(Date(), nullable=False, index=True)
    source_event_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrderRecord(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_session_state", "session_id", "state"),
        Index("ix_orders_lookup", "session_id", "instrument", "side", "order_type", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), nullable=False)
    instrument: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry: Mapped[Any] = mapped_column(Numeric(12, 2), nullable=False)
    stop_loss: Mapped[Any] = mapped_column(Numeric(12, 2), nullable=False)
    take_profits: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    source_event_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PositionRecord(Base):
    __tablename__ = "positions"
    __table_args__ = (
        Index("ix_positions_session_state", "session_id", "state"),
        Index("ix_positions_lookup", "session_id", "instrument", "side", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), nullable=False)
    source_order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("orders.id"), nullable=False
    )
    instrument: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    entry: Mapped[Any] = mapped_column(Numeric(12, 2), nullable=False)
    stop_loss: Mapped[Any] = mapped_column(Numeric(12, 2), nullable=False)
    take_profits: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    source_event_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProcessedEventRecordModel(Base):
    __tablename__ = "processed_events"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_processed_event_key"),
        Index(
            "ix_processed_event_message_lookup",
            "channel_id",
            "message_id",
            "event_index",
            "version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_key: Mapped[str] = mapped_column(Text, nullable=False)
    channel_id: Mapped[int] = mapped_column(index=True)
    message_id: Mapped[int] = mapped_column(index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_index: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ManualReviewRecordModel(Base):
    __tablename__ = "manual_reviews"
    __table_args__ = (UniqueConstraint("event_key", name="uq_manual_review_event_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_key: Mapped[str] = mapped_column(Text, nullable=False)
    channel_id: Mapped[int] = mapped_column(index=True)
    message_id: Mapped[int] = mapped_column(index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_index: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CommandOutboxRecord(Base):
    __tablename__ = "command_outbox"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_command_outbox_idempotency_key"),
        Index("ix_command_outbox_state", "state", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    event_key: Mapped[str] = mapped_column(Text, nullable=False)
    command_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("sessions.id"), nullable=True
    )
    order_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("orders.id"), nullable=True)
    position_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("positions.id"), nullable=True
    )
    depends_on_command_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("command_outbox.id"), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
