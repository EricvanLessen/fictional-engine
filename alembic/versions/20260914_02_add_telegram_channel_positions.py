"""add telegram channel positions

Revision ID: 20260914_02
Revises: 20260914_01
Create Date: 2026-09-14 16:40:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260914_02"
down_revision = "20260914_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_channel_positions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("last_message_id", sa.Integer(), nullable=False),
        sa.Column("last_occurrence_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id", name="uq_telegram_channel_positions_channel"),
    )
    op.create_index(
        op.f("ix_telegram_channel_positions_channel_id"),
        "telegram_channel_positions",
        ["channel_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_telegram_channel_positions_channel_id"),
        table_name="telegram_channel_positions",
    )
    op.drop_table("telegram_channel_positions")
