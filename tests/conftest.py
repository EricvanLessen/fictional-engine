from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session, sessionmaker

from fictional_engine.adapters.persistence.database import build_session_factory

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "fixtures" / "replay" / "september-9-m1.json"


@pytest.fixture
def replay_fixture_path() -> Path:
    return FIXTURE_PATH


@pytest.fixture
def sqlite_database_url(tmp_path: Path) -> str:
    database_path = tmp_path / "replay.sqlite3"
    return f"sqlite:///{database_path}"


@pytest.fixture
def alembic_config(sqlite_database_url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", sqlite_database_url)
    return config


@pytest.fixture
def migrated_session_factory(
    alembic_config: Config, sqlite_database_url: str
) -> sessionmaker[Session]:
    command.upgrade(alembic_config, "head")
    return build_session_factory(sqlite_database_url)
