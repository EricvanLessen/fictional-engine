from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from telethon import TelegramClient  # type: ignore[import-untyped]
from telethon.errors import FloodWaitError  # type: ignore[import-untyped]
from telethon.tl.patched import Message  # type: ignore[import-untyped]

from fictional_engine.adapters.telegram.mtproto_adapter import TelegramReconnectableError
from fictional_engine.application.telegram_runtime import (
    TelegramChannelPosition,
    TelegramIncomingMessage,
)


@dataclass(frozen=True)
class TelegramDialogSummary:
    dialog_id: int
    title: str | None


class TelethonUserSessionClient:
    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        session_path: Path,
        phone_number: str | None,
        overlap: int = 25,
    ) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_path = session_path
        self._phone_number = phone_number
        self._overlap = overlap
        self._client: TelegramClient | None = None

    async def connect(self) -> None:
        client = self._build_client()
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("telegram session is not authorized; run login first")

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()

    async def login(self) -> None:
        if self._phone_number is None:
            raise RuntimeError("TELEGRAM_PHONE_NUMBER is required for login")

        client = self._build_client()
        await client.connect()
        await _restrict_owner_only(self._session_path)

        if await client.is_user_authorized():
            return

        sent = await client.send_code_request(self._phone_number)
        code = await _prompt("Telegram code: ")

        try:
            await client.sign_in(
                phone=self._phone_number,
                code=code,
                phone_code_hash=sent.phone_code_hash,
            )
        except Exception as exc:
            # Telethon raises SessionPasswordNeededError for 2FA.
            if exc.__class__.__name__ != "SessionPasswordNeededError":
                raise
            password = await _prompt("Telegram 2FA password: ")
            await client.sign_in(password=password)

        await _restrict_owner_only(self._session_path)

    async def list_dialogs(self) -> list[TelegramDialogSummary]:
        client = self._build_client()
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("telegram session is not authorized; run login first")

        dialogs: list[TelegramDialogSummary] = []
        async for dialog in client.iter_dialogs():
            dialogs.append(TelegramDialogSummary(dialog_id=dialog.id, title=dialog.title))
        return dialogs

    async def iter_channel_messages(
        self,
        *,
        channel_id: int,
        after_position: TelegramChannelPosition | None,
        catchup_limit: int,
    ) -> AsyncIterator[TelegramIncomingMessage]:
        client = self._require_client()
        try:
            entity = await client.get_entity(channel_id)
        except FloodWaitError as exc:
            raise TelegramReconnectableError(
                "telegram flood wait",
                retry_after_seconds=float(exc.seconds),
            ) from exc

        history = await self._catchup_messages(
            channel_id=channel_id,
            entity=entity,
            after_position=after_position,
            catchup_limit=catchup_limit,
        )
        for message in history:
            yield _to_incoming(message, channel_id=channel_id)

    async def _catchup_messages(
        self,
        *,
        channel_id: int,
        entity: Any,
        after_position: TelegramChannelPosition | None,
        catchup_limit: int,
    ) -> list[Message]:
        client = self._require_client()
        limit = max(catchup_limit, 1)
        min_id = 0
        if after_position is not None:
            min_id = max(after_position.last_message_id - self._overlap, 0)

        try:
            messages = [
                item
                async for item in client.iter_messages(entity, limit=limit, min_id=min_id)
                if _is_supported_message(item, channel_id=channel_id)
            ]
        except FloodWaitError as exc:
            raise TelegramReconnectableError(
                "telegram flood wait",
                retry_after_seconds=float(exc.seconds),
            ) from exc

        messages.sort(key=lambda message: _message_sort_key(message))
        return messages

    def _build_client(self) -> TelegramClient:
        if self._client is None:
            self._session_path.parent.mkdir(parents=True, exist_ok=True)
            self._client = TelegramClient(
                str(self._session_path),
                self._api_id,
                self._api_hash,
            )
        return self._client

    def _require_client(self) -> TelegramClient:
        client = self._client
        if client is None:
            raise RuntimeError("telegram client is not connected")
        return client


def _to_incoming(message: Message, *, channel_id: int) -> TelegramIncomingMessage:
    edit_date = _to_utc(message.edit_date)
    message_date = _to_utc(message.date)
    if message_date is None:
        raise ValueError("telegram message missing date")
    source_payload = {
        "chat_id": _extract_chat_id(message),
        "date": message_date.isoformat(),
        "edit_date": None if edit_date is None else edit_date.isoformat(),
        "id": message.id,
        "message": message.message,
        "peer_id": _serialize_peer_id(message.peer_id),
        "post": getattr(message, "post", None),
        "raw_text": message.raw_text,
    }
    return TelegramIncomingMessage(
        channel_id=channel_id,
        message_id=message.id,
        message_date=message_date,
        edit_date=edit_date,
        sender_name=None,
        channel_title=None,
        text=message.message,
        caption=message.message,
        media_metadata={},
        telegram_metadata={"source": "telethon"},
        source_payload=source_payload,
    )


def _extract_chat_id(message: Message) -> int | None:
    chat_id = getattr(message, "chat_id", None)
    return int(chat_id) if chat_id is not None else None


def _serialize_peer_id(peer_id: Any) -> dict[str, Any] | None:
    if peer_id is None:
        return None
    if hasattr(peer_id, "to_dict"):
        value = peer_id.to_dict()
        if isinstance(value, dict):
            return value
        return {"value": str(value)}
    return {"value": str(peer_id)}


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _message_sort_key(message: Message) -> tuple[datetime, int]:
    date_value = _to_utc(message.edit_date) or _to_utc(message.date)
    assert date_value is not None
    return date_value, message.id


def _is_supported_message(message: Message, *, channel_id: int) -> bool:
    chat_id = _extract_chat_id(message)
    if chat_id is None:
        return False
    if int(chat_id) != int(channel_id):
        return False
    if message.id is None or message.date is None:
        return False
    return bool(message.message or getattr(message, "media", None) is not None)


async def _restrict_owner_only(session_path: Path) -> None:
    def chmod() -> None:
        try:
            session_path.chmod(0o600)
        except FileNotFoundError:
            return

    await asyncio.to_thread(chmod)


async def _prompt(message: str) -> str:
    return (await asyncio.to_thread(input, message)).strip()
