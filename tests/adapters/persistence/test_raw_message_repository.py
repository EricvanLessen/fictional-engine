from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from fictional_engine.adapters.persistence.migration_support import packaged_alembic_config
from fictional_engine.adapters.persistence.raw_message_repository import (
    SqlAlchemyRawMessageRepository,
)
from fictional_engine.adapters.telegram.fixtures import load_fixture_envelopes


def test_duplicate_ingestion_is_idempotent_and_edits_create_new_versions(
    migrated_session_factory: sessionmaker[Session], replay_fixture_path: Path
) -> None:
    repository = SqlAlchemyRawMessageRepository(migrated_session_factory)
    envelopes = load_fixture_envelopes(replay_fixture_path)

    original = envelopes[0]
    duplicate = envelopes[1]
    edited_original = envelopes[2]
    edited_version = envelopes[3]

    first_result = repository.ingest_message(original.raw_message, original.source_payload)
    duplicate_result = repository.ingest_message(duplicate.raw_message, duplicate.source_payload)
    edited_original_result = repository.ingest_message(
        edited_original.raw_message, edited_original.source_payload
    )
    edit_result = repository.ingest_message(
        edited_version.raw_message, edited_version.source_payload
    )

    assert first_result.inserted is True
    assert first_result.stored_message.version == 1
    assert duplicate_result.inserted is False
    assert duplicate_result.stored_message.id == first_result.stored_message.id
    assert edited_original_result.inserted is True
    assert edited_original_result.stored_message.version == 1
    assert edit_result.inserted is True
    assert edit_result.stored_message.version == 2
    assert (
        edit_result.stored_message.previous_version_id
        == edited_original_result.stored_message.id
    )

    versions = repository.list_message_versions(777000, 101)
    assert len(versions) == 1

    edited_versions = repository.list_message_versions(777000, 102)
    assert len(edited_versions) == 2


def test_migration_upgrade_and_downgrade_round_trip(
    sqlite_database_url: str,
) -> None:
    from alembic import command

    with packaged_alembic_config(sqlite_database_url) as config:
        command.upgrade(config, "head")

    engine = create_engine(sqlite_database_url, future=True)
    assert "raw_telegram_message_versions" in inspect(engine).get_table_names()

    with packaged_alembic_config(sqlite_database_url) as config:
        command.downgrade(config, "base")
    assert "raw_telegram_message_versions" not in inspect(engine).get_table_names()


def test_repository_persists_exact_original_source_payload(
    migrated_session_factory: sessionmaker[Session],
    source_payload_regression_fixture_path: Path,
) -> None:
    repository = SqlAlchemyRawMessageRepository(migrated_session_factory)
    envelope = load_fixture_envelopes(source_payload_regression_fixture_path)[0]

    result = repository.ingest_message(envelope.raw_message, envelope.source_payload)

    assert result.inserted is True
    assert result.stored_message.source_payload == envelope.source_payload
    assert result.stored_message.source_payload["unknown_scalar"] == "preserve-me"
    assert result.stored_message.source_payload["unknown_nested_export"] == {
        "outer": {"inner": [1, 2, {"keep": True}]}
    }
