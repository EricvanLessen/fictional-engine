from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from fictional_engine.config.settings import EngineSettings


@pytest.fixture(autouse=True)
def clear_env_telegram_cli(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in (
        "EXECUTION_MODE",
        "ALLOW_LIVE_TRADING",
        "TELEGRAM_CHANNEL_ID",
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_PHONE_NUMBER",
        "TELEGRAM_PHONE",
        "TELEGRAM_SESSION_PATH",
        "TELEGRAM_CATCHUP_LIMIT",
        "DATABASE_URL",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


def _base_settings(monkeypatch: pytest.MonkeyPatch) -> EngineSettings:
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    monkeypatch.setenv("TELEGRAM_PHONE_NUMBER", "+123456789")
    monkeypatch.setenv("TELEGRAM_SESSION_PATH", ".telegram/test-session")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    return EngineSettings()


def test_listen_requires_channel_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fictional_engine.application import telegram_cli

    settings = _base_settings(monkeypatch)
    monkeypatch.setattr(telegram_cli, "get_settings", lambda: settings)

    assert telegram_cli.main(["listen"]) == 1


def test_login_allows_missing_channel_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fictional_engine.application import telegram_cli

    settings = _base_settings(monkeypatch)
    monkeypatch.setattr(telegram_cli, "get_settings", lambda: settings)

    async def fake_login(session_path: Path, cli_settings: EngineSettings) -> int:
        assert cli_settings.telegram_channel_id is None
        assert session_path.name == "test-session"
        return 0

    monkeypatch.setattr(telegram_cli, "_run_login", fake_login)

    assert telegram_cli.main(["login"]) == 0


def test_list_dialogs_allows_missing_channel_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fictional_engine.application import telegram_cli

    settings = _base_settings(monkeypatch)
    monkeypatch.setattr(telegram_cli, "get_settings", lambda: settings)

    async def fake_list_dialogs(session_path: Path, cli_settings: EngineSettings) -> int:
        assert cli_settings.telegram_channel_id is None
        assert session_path.name == "test-session"
        return 0

    monkeypatch.setattr(telegram_cli, "_run_list_dialogs", fake_list_dialogs)

    assert telegram_cli.main(["list-dialogs"]) == 0
