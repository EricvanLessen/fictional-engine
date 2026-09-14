"""add m3 state tables

Revision ID: 20260914_01
Revises: 20260910_01
Create Date: 2026-09-14 15:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260914_01"
down_revision = "20260910_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("instrument", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_id",
            "session_date",
            "instrument",
            name="uq_session_channel_date_instrument",
        ),
    )
    op.create_index(op.f("ix_sessions_channel_id"), "sessions", ["channel_id"], unique=False)
    op.create_index(op.f("ix_sessions_session_date"), "sessions", ["session_date"], unique=False)

    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("instrument", sa.Text(), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("order_type", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("entry", sa.Numeric(12, 2), nullable=False),
        sa.Column("stop_loss", sa.Numeric(12, 2), nullable=False),
        sa.Column("take_profits", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("source_event_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_orders_lookup",
        "orders",
        ["session_id", "instrument", "side", "order_type", "state"],
        unique=False,
    )
    op.create_index("ix_orders_session_state", "orders", ["session_id", "state"], unique=False)

    op.create_table(
        "positions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("source_order_id", sa.String(length=36), nullable=False),
        sa.Column("instrument", sa.Text(), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("entry", sa.Numeric(12, 2), nullable=False),
        sa.Column("stop_loss", sa.Numeric(12, 2), nullable=False),
        sa.Column("take_profits", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("source_event_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.ForeignKeyConstraint(["source_order_id"], ["orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_positions_lookup",
        "positions",
        ["session_id", "instrument", "side", "state"],
        unique=False,
    )
    op.create_index(
        "ix_positions_session_state", "positions", ["session_id", "state"], unique=False
    )

    op.create_table(
        "processed_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("event_index", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_fingerprint", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key", name="uq_processed_event_key"),
    )
    op.create_index(
        "ix_processed_event_message_lookup",
        "processed_events",
        ["channel_id", "message_id", "event_index", "version"],
        unique=False,
    )
    op.create_index(
        op.f("ix_processed_events_channel_id"), "processed_events", ["channel_id"], unique=False
    )
    op.create_index(
        op.f("ix_processed_events_message_id"), "processed_events", ["message_id"], unique=False
    )

    op.create_table(
        "manual_reviews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("event_index", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key", name="uq_manual_review_event_key"),
    )
    op.create_index(
        op.f("ix_manual_reviews_channel_id"), "manual_reviews", ["channel_id"], unique=False
    )
    op.create_index(
        op.f("ix_manual_reviews_message_id"), "manual_reviews", ["message_id"], unique=False
    )

    op.create_table(
        "command_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("command_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("order_id", sa.String(length=36), nullable=True),
        sa.Column("position_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_command_outbox_idempotency_key"),
    )
    op.create_index(
        "ix_command_outbox_state", "command_outbox", ["state", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_command_outbox_state", table_name="command_outbox")
    op.drop_table("command_outbox")
    op.drop_index(op.f("ix_manual_reviews_message_id"), table_name="manual_reviews")
    op.drop_index(op.f("ix_manual_reviews_channel_id"), table_name="manual_reviews")
    op.drop_table("manual_reviews")
    op.drop_index(op.f("ix_processed_events_message_id"), table_name="processed_events")
    op.drop_index(op.f("ix_processed_events_channel_id"), table_name="processed_events")
    op.drop_index("ix_processed_event_message_lookup", table_name="processed_events")
    op.drop_table("processed_events")
    op.drop_index("ix_positions_session_state", table_name="positions")
    op.drop_index("ix_positions_lookup", table_name="positions")
    op.drop_table("positions")
    op.drop_index("ix_orders_session_state", table_name="orders")
    op.drop_index("ix_orders_lookup", table_name="orders")
    op.drop_table("orders")
    op.drop_index(op.f("ix_sessions_session_date"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_channel_id"), table_name="sessions")
    op.drop_table("sessions")
