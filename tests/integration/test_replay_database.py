from __future__ import annotations

from pathlib import Path

from fictional_engine.adapters.persistence.database import build_session_factory
from fictional_engine.adapters.persistence.raw_message_repository import (
    SqlAlchemyRawMessageRepository,
)
from fictional_engine.application.replay_cli import run_migrations


def test_replay_persists_traceable_versions_with_source_payload(
    sqlite_database_url: str, replay_fixture_path: Path
) -> None:
    run_migrations(sqlite_database_url)
    repository = SqlAlchemyRawMessageRepository(build_session_factory(sqlite_database_url))

    from fictional_engine.application.ingestion import RawMessageIngestionService
    from fictional_engine.application.replay import ReplayService

    summary = ReplayService(RawMessageIngestionService(repository)).replay_fixtures(
        replay_fixture_path
    )
    stored_messages = repository.list_messages()

    assert summary.inserted_versions == 5
    assert len(stored_messages) == 5
    assert [message.version for message in stored_messages if message.message_id == 102] == [1, 2]
    assert stored_messages[1].source_payload["fixture_id"] == "orders-trigger-replace-001"
    assert stored_messages[2].previous_version_id == stored_messages[1].id
