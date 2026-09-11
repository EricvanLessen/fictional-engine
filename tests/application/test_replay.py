from __future__ import annotations

import socket
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from fictional_engine.adapters.persistence.raw_message_repository import (
    SqlAlchemyRawMessageRepository,
)
from fictional_engine.application.ingestion import RawMessageIngestionService
from fictional_engine.application.replay import ReplayService


def test_replay_service_replays_in_timestamp_message_order(
    migrated_session_factory: sessionmaker[Session], replay_fixture_path: Path
) -> None:
    repository = SqlAlchemyRawMessageRepository(migrated_session_factory)
    service = ReplayService(RawMessageIngestionService(repository))

    summary = service.replay_fixtures(replay_fixture_path)

    assert summary.processed_inputs == 6
    assert summary.inserted_versions == 5
    assert summary.duplicate_inputs == 1
    replayed = [
        (event.message_id, event.version, event.inserted)
        for event in summary.replayed_events
    ]
    assert replayed == [
        (101, 1, True),
        (101, 1, False),
        (102, 1, True),
        (102, 2, True),
        (103, 1, True),
        (104, 1, True),
    ]


def test_replay_service_makes_no_network_calls(
    migrated_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    replay_fixture_path: Path,
) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access during replay is forbidden")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket.socket, "connect", fail_network)

    repository = SqlAlchemyRawMessageRepository(migrated_session_factory)
    service = ReplayService(RawMessageIngestionService(repository))

    summary = service.replay_fixtures(replay_fixture_path)

    assert summary.inserted_versions == 5
