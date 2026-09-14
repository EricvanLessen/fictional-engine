from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC
from typing import Protocol

from fictional_engine.application.telegram_runtime import (
    TelegramChannelPosition,
    TelegramIncomingMessage,
)


class TelegramReconnectableError(Exception):
    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class MtprotoSessionClient(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    def iter_channel_messages(
        self,
        *,
        channel_id: int,
        after_position: TelegramChannelPosition | None,
    ) -> AsyncIterator[TelegramIncomingMessage]: ...


@dataclass(frozen=True)
class ReconnectPolicy:
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0


class ReadOnlyTelegramMtprotoAdapter:
    """Read-only Telegram user-session adapter with reconnect/backoff behavior."""

    def __init__(
        self,
        client: MtprotoSessionClient,
        *,
        reconnect_policy: ReconnectPolicy | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._reconnect_policy = reconnect_policy or ReconnectPolicy()
        self._logger = logger or logging.getLogger("fictional_engine.telegram")

    async def iter_messages(
        self,
        *,
        channel_id: int,
        after_position: TelegramChannelPosition | None,
    ) -> AsyncIterator[TelegramIncomingMessage]:
        delay = self._reconnect_policy.base_delay_seconds
        position = after_position

        while True:
            try:
                self._logger.info(
                    "telegram_connecting",
                    extra={"channel_id": channel_id, "has_position": position is not None},
                )
                await self._client.connect()
                self._logger.info("telegram_connected", extra={"channel_id": channel_id})

                async for message in self._client.iter_channel_messages(
                    channel_id=channel_id,
                    after_position=position,
                ):
                    position = TelegramChannelPosition(
                        channel_id=message.channel_id,
                        last_message_id=message.message_id,
                        last_occurrence_timestamp=message.edit_date
                        or message.message_date.astimezone(UTC),
                    )
                    yield message

                self._logger.info("telegram_stream_idle", extra={"channel_id": channel_id})
                return
            except TelegramReconnectableError as exc:
                sleep_seconds = exc.retry_after_seconds or delay
                self._logger.warning(
                    "telegram_reconnect_backoff",
                    extra={
                        "channel_id": channel_id,
                        "retry_after_seconds": sleep_seconds,
                        "error": type(exc).__name__,
                    },
                )
                await asyncio.sleep(sleep_seconds)
                delay = min(delay * 2.0, self._reconnect_policy.max_delay_seconds)
            except Exception as exc:
                self._logger.warning(
                    "telegram_reconnect_backoff",
                    extra={
                        "channel_id": channel_id,
                        "retry_after_seconds": delay,
                        "error": type(exc).__name__,
                    },
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, self._reconnect_policy.max_delay_seconds)
            finally:
                try:
                    await self._client.disconnect()
                except Exception:
                    self._logger.warning(
                        "telegram_disconnect_failed",
                        extra={"channel_id": channel_id},
                    )
