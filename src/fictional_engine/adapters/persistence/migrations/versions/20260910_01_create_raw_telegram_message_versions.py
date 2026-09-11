"""create raw telegram message versions table

Revision ID: 20260910_01
Revises:
Create Date: 2026-09-10 16:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260910_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_telegram_message_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_version_id", sa.String(length=36), nullable=True),
        sa.Column("message_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edit_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurrence_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sender_name", sa.Text(), nullable=True),
        sa.Column("channel_title", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("media_metadata", sa.JSON(), nullable=False),
        sa.Column("telegram_metadata", sa.JSON(), nullable=False),
        sa.Column("source_payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("deduplication_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["previous_version_id"], ["raw_telegram_message_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_id",
            "message_id",
            "deduplication_key",
            name="uq_raw_message_deduplication",
        ),
        sa.UniqueConstraint("channel_id", "message_id", "version", name="uq_raw_message_identity"),
    )
    op.create_index(
        "ix_raw_message_occurrence",
        "raw_telegram_message_versions",
        ["occurrence_timestamp", "channel_id", "message_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_raw_telegram_message_versions_channel_id"),
        "raw_telegram_message_versions",
        ["channel_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_raw_telegram_message_versions_message_id"),
        "raw_telegram_message_versions",
        ["message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_raw_telegram_message_versions_message_id"),
        table_name="raw_telegram_message_versions",
    )
    op.drop_index(
        op.f("ix_raw_telegram_message_versions_channel_id"),
        table_name="raw_telegram_message_versions",
    )
    op.drop_index("ix_raw_message_occurrence", table_name="raw_telegram_message_versions")
    op.drop_table("raw_telegram_message_versions")
