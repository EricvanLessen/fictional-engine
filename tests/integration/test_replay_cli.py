from __future__ import annotations

import json
import subprocess
import sys
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


def test_replay_cli_runs_from_installed_wheel_runtime(
    tmp_path: Path,
    replay_fixture_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    wheel_dir = tmp_path / "wheelhouse"
    installed_dir = tmp_path / "installed"
    database_path = tmp_path / "runtime.sqlite3"
    wheel_dir.mkdir()
    installed_dir.mkdir()

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
        ],
        check=True,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    wheel_path = next(wheel_dir.glob("fictional_engine-*.whl"))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(installed_dir),
            str(wheel_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert (
        installed_dir
        / "fictional_engine"
        / "adapters"
        / "persistence"
        / "migrations"
        / "env.py"
    ).exists()

    command = (
        "import sys; "
        f"sys.path.insert(0, {str(installed_dir)!r}); "
        "from fictional_engine.application.replay_cli import main; "
        f"raise SystemExit(main([{str(replay_fixture_path)!r}, '--database-url', {f'sqlite:///{database_path}'!r}]))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", command],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["processed_inputs"] == 6
    assert payload["inserted_versions"] == 5
    assert payload["duplicate_inputs"] == 1
