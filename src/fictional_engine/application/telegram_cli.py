from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import Sequence
from pathlib import Path

from fictional_engine.adapters.persistence.database import build_session_factory
from fictional_engine.adapters.persistence.raw_message_repository import (
    SqlAlchemyRawMessageRepository,
)
from fictional_engine.adapters.persistence.telegram_position_repository import (
    SqlAlchemyTelegramPositionRepository,
)
from fictional_engine.adapters.persistence.trading_state_repository import (
    SqlAlchemyTradingStateRepository,
)
from fictional_engine.adapters.telegram.mtproto_adapter import ReadOnlyTelegramMtprotoAdapter
from fictional_engine.adapters.telegram.telethon_client import TelethonUserSessionClient
from fictional_engine.application.ingestion import RawMessageIngestionService
from fictional_engine.application.parsing import DeterministicMessageParser
from fictional_engine.application.replay_cli import run_migrations
from fictional_engine.application.telegram_runtime import TelegramRealtimeProcessingService
from fictional_engine.config import EngineSettings, get_settings
from fictional_engine.observability.json_logging import configure_json_logging


def main(argv: Sequence[str] | None = None) -> int:
    settings = get_settings()
    configure_json_logging(settings.log_level)

    parser = argparse.ArgumentParser(description="Telegram MTProto adapter CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("login", help="bootstrap Telegram user session")
    subparsers.add_parser("list-dialogs", help="list accessible Telegram dialogs")
    subparsers.add_parser("listen", help="listen to configured Telegram channel")

    args = parser.parse_args(argv)

    if settings.execution_mode != "shadow":
        logging.getLogger("fictional_engine.telegram").error(
            "startup_rejected_non_shadow_execution",
            extra={"execution_mode": settings.execution_mode},
        )
        return 1

    try:
        if args.command == "login":
            return asyncio.run(_run_login(settings.telegram_session_path, settings))
        if args.command == "list-dialogs":
            return asyncio.run(_run_list_dialogs(settings.telegram_session_path, settings))
        return asyncio.run(_run_listen(settings.telegram_session_path, settings))
    except ValueError as exc:
        logging.getLogger("fictional_engine.telegram").error(
            "telegram.listen_invalid_configuration",
            extra={"reason": str(exc)},
        )
        return 1


async def _run_login(session_path: Path, settings: EngineSettings) -> int:
    client = _build_telethon_client(session_path, settings)
    await client.login()
    print(json.dumps({"status": "ok", "command": "login"}, sort_keys=True))
    return 0


async def _run_list_dialogs(session_path: Path, settings: EngineSettings) -> int:
    client = _build_telethon_client(session_path, settings)
    dialogs = await client.list_dialogs()
    for dialog in dialogs:
        print(f"{dialog.dialog_id}\t{dialog.title or ''}")
    return 0


async def _run_listen(session_path: Path, settings: EngineSettings) -> int:
    channel_id = settings.telegram_channel_id
    if channel_id is None:
        raise ValueError("TELEGRAM_CHANNEL_ID is required for listen")

    run_migrations(settings.database_url)
    session_factory = build_session_factory(settings.database_url)

    raw_repo = SqlAlchemyRawMessageRepository(session_factory)
    state_repo = SqlAlchemyTradingStateRepository(session_factory)
    position_repo = SqlAlchemyTelegramPositionRepository(session_factory)

    client = _build_telethon_client(session_path, settings)
    adapter = ReadOnlyTelegramMtprotoAdapter(
        client,
        catchup_limit=settings.telegram_catchup_limit,
    )

    service = TelegramRealtimeProcessingService(
        RawMessageIngestionService(raw_repo),
        DeterministicMessageParser(preferred_provider="Cronos Markets"),
        state_repo,
        position_repo,
    )

    await service.process_source(adapter, channel_id=channel_id)
    return 0


def _build_telethon_client(
    session_path: Path,
    settings: EngineSettings,
) -> TelethonUserSessionClient:
    return TelethonUserSessionClient(
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash.get_secret_value(),
        phone_number=settings.telegram_phone_number,
        session_path=session_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
