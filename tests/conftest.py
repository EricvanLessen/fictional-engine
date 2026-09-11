from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from fictional_engine.adapters.persistence.database import build_session_factory
from fictional_engine.application.replay_cli import run_migrations

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "fixtures" / "replay" / "september-9-m1.json"
SOURCE_PAYLOAD_REGRESSION_FIXTURE_PATH = (
    REPO_ROOT / "fixtures" / "replay" / "source-payload-regression.json"
)


@pytest.fixture
def replay_fixture_path() -> Path:
    return FIXTURE_PATH


@pytest.fixture
def source_payload_regression_fixture_path() -> Path:
    return SOURCE_PAYLOAD_REGRESSION_FIXTURE_PATH


@pytest.fixture
def sqlite_database_url(tmp_path: Path) -> str:
    database_path = tmp_path / "replay.sqlite3"
    return f"sqlite:///{database_path}"


@pytest.fixture
def migrated_session_factory(
    sqlite_database_url: str,
) -> sessionmaker[Session]:
    run_migrations(sqlite_database_url)
    return build_session_factory(sqlite_database_url)
