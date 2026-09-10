from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from alembic import command

from fictional_engine.adapters.persistence.database import build_session_factory
from fictional_engine.adapters.persistence.migration_support import packaged_alembic_config
from fictional_engine.adapters.persistence.raw_message_repository import (
    SqlAlchemyRawMessageRepository,
)
from fictional_engine.application.ingestion import RawMessageIngestionService
from fictional_engine.application.replay import ReplayService, ReplaySummary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic raw Telegram replay")
    parser.add_argument("fixture_path", help="Path to a JSON fixture file or directory")
    parser.add_argument("--database-url", required=True, help="SQLAlchemy database URL")
    args = parser.parse_args(argv)

    run_migrations(args.database_url)

    session_factory = build_session_factory(args.database_url)
    repository = SqlAlchemyRawMessageRepository(session_factory)
    service = ReplayService(RawMessageIngestionService(repository))
    summary = service.replay_fixtures(Path(args.fixture_path))

    print(json.dumps(_summary_payload(summary), sort_keys=True))
    return 0


def run_migrations(database_url: str) -> None:
    with packaged_alembic_config(database_url) as config:
        command.upgrade(config, "head")


def _summary_payload(summary: ReplaySummary) -> dict[str, object]:
    return {
        "duplicate_inputs": summary.duplicate_inputs,
        "inserted_versions": summary.inserted_versions,
        "processed_inputs": summary.processed_inputs,
        "replayed_events": [
            {
                "channel_id": item.channel_id,
                "fixture_id": item.fixture_id,
                "inserted": item.inserted,
                "message_id": item.message_id,
                "occurrence_timestamp": item.occurrence_timestamp,
                "source_index": item.source_index,
                "version": item.version,
            }
            for item in summary.replayed_events
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
