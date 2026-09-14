from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
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
        catchup_limit: int,
    ) -> AsyncIterator[TelegramIncomingMessage]: ...


@dataclass(frozen=True)
class ReconnectPolicy:
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0


class ReadOnlyTelegramMtprotoAdapter:
    def __init__(
        self,
        client: MtprotoSessionClient,
        *,
        catchup_limit: int,
        reconnect_policy: ReconnectPolicy | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._catchup_limit = catchup_limit
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
                self._logger.info("telegram.connecting", extra={"channel_id": channel_id})
                await self._client.connect()
                self._logger.info("telegram.connected", extra={"channel_id": channel_id})

                async for message in self._client.iter_channel_messages(
                    channel_id=channel_id,
                    after_position=position,
                    catchup_limit=self._catchup_limit,
                ):
                    position = TelegramChannelPosition(
                        channel_id=message.channel_id,
                        last_message_id=message.message_id,
                        last_occurrence_timestamp=_ensure_utc(message.occurrence_timestamp),
                    )
                    yield message

                return
            except TelegramReconnectableError as exc:
                wait_seconds = exc.retry_after_seconds or delay
                self._logger.warning(
                    "telegram.flood_wait" if exc.retry_after_seconds else "telegram.reconnecting",
                    extra={
                        "channel_id": channel_id,
                        "retry_after_seconds": wait_seconds,
                    },
                )
                await asyncio.sleep(wait_seconds)
                delay = min(delay * 2.0, self._reconnect_policy.max_delay_seconds)
            except Exception:
                self._logger.warning(
                    "telegram.reconnecting",
                    extra={
                        "channel_id": channel_id,
                        "retry_after_seconds": delay,
                    },
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, self._reconnect_policy.max_delay_seconds)
            finally:
                try:
                    await self._client.disconnect()
                except Exception:
                    self._logger.warning(
                        "telegram.reconnecting",
                        extra={"channel_id": channel_id, "retry_after_seconds": delay},
                    )


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
