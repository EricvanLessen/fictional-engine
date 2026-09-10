from __future__ import annotations

import json
from pathlib import Path

import pytest

from fictional_engine.application.replay_cli import main as replay_main


def test_replay_cli_applies_migrations_and_persists_versions(
    sqlite_database_url: str, replay_fixture_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = replay_main([str(replay_fixture_path), "--database-url", sqlite_database_url])

    assert exit_code == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["processed_inputs"] == 6
    assert payload["inserted_versions"] == 5
    assert payload["duplicate_inputs"] == 1
