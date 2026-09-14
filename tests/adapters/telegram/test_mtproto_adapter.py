from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from io import StringIO
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from fictional_engine.adapters.persistence.raw_message_repository import (
    SqlAlchemyRawMessageRepository,
)
from fictional_engine.adapters.persistence.telegram_position_repository import (
    SqlAlchemyTelegramPositionRepository,
)
from fictional_engine.adapters.persistence.trading_state_repository import (
    SqlAlchemyTradingStateRepository,
)
from fictional_engine.adapters.telegram.mtproto_adapter import (
    ReadOnlyTelegramMtprotoAdapter,
    TelegramReconnectableError,
)
from fictional_engine.application.ingestion import RawMessageIngestionService
from fictional_engine.application.parsing import DeterministicMessageParser
from fictional_engine.application.telegram_runtime import (
    TelegramChannelPosition,
    TelegramIncomingMessage,
    TelegramRealtimeProcessingService,
)
from fictional_engine.domain.state import OutboxCommandState
from fictional_engine.observability.json_logging import JsonFormatter

INITIAL_ORDERS_TEXT = """Instrument: US30

Cronos Markets data:
SELL STOP
Entry: 52547.00
SL: 52832.00
TP: 52413.00

BUY STOP
Entry: 52829.00
SL: 52544.00
TP: 52963.00"""

EDITED_ORDERS_TEXT = """Instrument: US30

Cronos Markets data:
SELL STOP
Entry: 52540.00
SL: 52832.00
TP: 52413.00

BUY STOP
Entry: 52829.00
SL: 52544.00
TP: 52963.00"""


class StubUpdateSource:
    def __init__(self, messages: list[TelegramIncomingMessage]) -> None:
        self._messages = messages
        self.after_position_calls: list[TelegramChannelPosition | None] = []

    async def iter_messages(
        self,
        *,
        channel_id: int,
        after_position: TelegramChannelPosition | None,
    ) -> AsyncIterator[TelegramIncomingMessage]:
        self.after_position_calls.append(after_position)
        for message in self._messages:
            yield message


class StubMtprotoClient:
    def __init__(self, messages: list[TelegramIncomingMessage]) -> None:
        self._messages = messages
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.iter_calls = 0
        self.after_positions: list[TelegramChannelPosition | None] = []
        self.catchup_limits: list[int] = []

    async def connect(self) -> None:
        self.connect_calls += 1

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def iter_channel_messages(
        self,
        *,
        channel_id: int,
        after_position: TelegramChannelPosition | None,
        catchup_limit: int,
    ) -> AsyncIterator[TelegramIncomingMessage]:
        self.iter_calls += 1
        self.after_positions.append(after_position)
        self.catchup_limits.append(catchup_limit)

        if self.iter_calls == 1:
            raise TelegramReconnectableError("flood wait", retry_after_seconds=0.01)

        for message in self._messages:
            yield message


class CountingStateRepository:
    def __init__(self, inner: SqlAlchemyTradingStateRepository) -> None:
        self._inner = inner
        self.apply_calls = 0
        self.message_ids: list[int] = []

    def apply_parsed_message(self, parsed_message: Any, source: Any) -> Any:
        self.apply_calls += 1
        self.message_ids.append(parsed_message.source.message_id)
        return self._inner.apply_parsed_message(parsed_message, source)


def _incoming(
    *,
    channel_id: int,
    message_id: int,
    message_date: datetime,
    text: str,
    edit_date: datetime | None = None,
) -> TelegramIncomingMessage:
    return TelegramIncomingMessage(
        channel_id=channel_id,
        message_id=message_id,
        message_date=message_date,
        edit_date=edit_date,
        sender_name="Trading Busters",
        channel_title="Trading Busters Signals",
        text=text,
        caption=None,
        media_metadata={},
        telegram_metadata={"transport": "mtproto"},
        source_payload={"message_id": message_id, "channel_id": channel_id},
    )


@pytest.mark.asyncio
async def test_realtime_processing_filters_channel_and_handles_versions(
    migrated_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden in adapter tests")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket.socket, "connect", fail_network)

    raw_repository = SqlAlchemyRawMessageRepository(migrated_session_factory)
    position_repository = SqlAlchemyTelegramPositionRepository(migrated_session_factory)
    state_repository = CountingStateRepository(
        SqlAlchemyTradingStateRepository(migrated_session_factory)
    )

    service = TelegramRealtimeProcessingService(
        RawMessageIngestionService(raw_repository),
        DeterministicMessageParser(preferred_provider="Cronos Markets"),
        state_repository,
        position_repository,
        logger=logging.getLogger("fictional_engine.telegram.test"),
    )

    t0 = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
    source_one = StubUpdateSource(
        [
            _incoming(channel_id=777000, message_id=100, message_date=t0, text=INITIAL_ORDERS_TEXT),
            _incoming(channel_id=777000, message_id=100, message_date=t0, text=INITIAL_ORDERS_TEXT),
            _incoming(channel_id=123456, message_id=200, message_date=t0, text=INITIAL_ORDERS_TEXT),
            _incoming(
                channel_id=777000,
                message_id=100,
                message_date=t0,
                edit_date=datetime(2026, 9, 14, 10, 5, tzinfo=UTC),
                text=EDITED_ORDERS_TEXT,
            ),
            _incoming(
                channel_id=777000,
                message_id=101,
                message_date=datetime(2026, 9, 14, 10, 6, tzinfo=UTC),
                text="Delete the order.",
            ),
        ]
    )

    summary_one = await service.process_source(source_one, channel_id=777000)

    assert summary_one.received == 4
    assert summary_one.inserted == 3
    assert summary_one.duplicates == 1
    assert state_repository.apply_calls == 3
    assert state_repository.message_ids == [100, 100, 101]

    versions = raw_repository.list_message_versions(777000, 100)
    assert [item.version for item in versions] == [1, 2]

    position = position_repository.get_position(777000)
    assert position is not None
    assert position.last_message_id == 101


@pytest.mark.asyncio
async def test_realtime_processing_restart_catchup_is_idempotent(
    migrated_session_factory: sessionmaker[Session],
) -> None:
    raw_repository = SqlAlchemyRawMessageRepository(migrated_session_factory)
    position_repository = SqlAlchemyTelegramPositionRepository(migrated_session_factory)
    state_repository = CountingStateRepository(
        SqlAlchemyTradingStateRepository(migrated_session_factory)
    )
    service = TelegramRealtimeProcessingService(
        RawMessageIngestionService(raw_repository),
        DeterministicMessageParser(preferred_provider="Cronos Markets"),
        state_repository,
        position_repository,
    )

    first_source = StubUpdateSource(
        [
            _incoming(
                channel_id=777000,
                message_id=101,
                message_date=datetime(2026, 9, 14, 10, 6, tzinfo=UTC),
                text="Delete the order.",
            )
        ]
    )
    await service.process_source(first_source, channel_id=777000)

    second_source = StubUpdateSource(
        [
            _incoming(
                channel_id=777000,
                message_id=101,
                message_date=datetime(2026, 9, 14, 10, 6, tzinfo=UTC),
                text="Delete the order.",
            ),
            _incoming(
                channel_id=777000,
                message_id=102,
                message_date=datetime(2026, 9, 14, 10, 7, tzinfo=UTC),
                text="Today we have bank holiday in USA. We will not trade today.",
            ),
        ]
    )

    summary_two = await service.process_source(second_source, channel_id=777000)

    assert second_source.after_position_calls[0] is not None
    assert second_source.after_position_calls[0].last_message_id == 101
    assert summary_two.inserted == 1
    assert summary_two.duplicates == 1


@pytest.mark.asyncio
async def test_mtproto_adapter_reconnects_with_floodwait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = datetime(2026, 9, 14, 11, 0, tzinfo=UTC)
    client = StubMtprotoClient(
        [_incoming(channel_id=777000, message_id=700, message_date=t0, text=INITIAL_ORDERS_TEXT)]
    )
    adapter = ReadOnlyTelegramMtprotoAdapter(client, catchup_limit=50)

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    messages = [
        message
        async for message in adapter.iter_messages(channel_id=777000, after_position=None)
    ]

    assert len(messages) == 1
    assert client.connect_calls == 2
    assert client.disconnect_calls >= 2
    assert client.after_positions[0] is None
    assert client.catchup_limits == [50, 50]
    assert sleep_calls == [0.01]


@pytest.mark.asyncio
async def test_logging_is_redacted_and_structured(
    migrated_session_factory: sessionmaker[Session],
) -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())

    logger = logging.getLogger("fictional_engine.telegram.logtest")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel("INFO")

    raw_repository = SqlAlchemyRawMessageRepository(migrated_session_factory)
    position_repository = SqlAlchemyTelegramPositionRepository(migrated_session_factory)
    state_repository = SqlAlchemyTradingStateRepository(migrated_session_factory)

    service = TelegramRealtimeProcessingService(
        RawMessageIngestionService(raw_repository),
        DeterministicMessageParser(preferred_provider="Cronos Markets"),
        state_repository,
        position_repository,
        logger=logger,
    )

    source = StubUpdateSource(
        [
            TelegramIncomingMessage(
                channel_id=777000,
                message_id=810,
                message_date=datetime(2026, 9, 14, 12, 0, tzinfo=UTC),
                edit_date=None,
                sender_name="Trading Busters",
                channel_title="Trading Busters Signals",
                text=INITIAL_ORDERS_TEXT,
                caption=None,
                media_metadata={},
                telegram_metadata={"session_token": "VERY_SECRET_SESSION"},
                source_payload={"api_hash": "TOP_SECRET_HASH"},
            )
        ]
    )

    await service.process_source(source, channel_id=777000)

    logs = stream.getvalue()
    assert "telegram.catchup_started" in logs
    assert "telegram.message_received" in logs
    assert "ingestion.inserted" in logs
    assert "parser.completed" in logs
    assert "state.applied" in logs
    assert "outbox.created" in logs
    assert "telegram.catchup_completed" in logs
    assert "TOP_SECRET_HASH" not in logs
    assert "VERY_SECRET_SESSION" not in logs
    assert "Entry: 52547.00" not in logs


def test_cancel_order_state_tracks_broker_outcome(
    migrated_session_factory: sessionmaker[Session],
) -> None:
    raw_repository = SqlAlchemyRawMessageRepository(migrated_session_factory)
    position_repository = SqlAlchemyTelegramPositionRepository(migrated_session_factory)
    state_repository = SqlAlchemyTradingStateRepository(migrated_session_factory)
    service = TelegramRealtimeProcessingService(
        RawMessageIngestionService(raw_repository),
        DeterministicMessageParser(preferred_provider="Cronos Markets"),
        state_repository,
        position_repository,
    )

    async def run_flow() -> None:
        source = StubUpdateSource(
            [
                _incoming(
                    channel_id=777000,
                    message_id=900,
                    message_date=datetime(2026, 9, 14, 13, 0, tzinfo=UTC),
                    text=INITIAL_ORDERS_TEXT,
                ),
                _incoming(
                    channel_id=777000,
                    message_id=901,
                    message_date=datetime(2026, 9, 14, 13, 5, tzinfo=UTC),
                    text="Delete the buy stop order.",
                ),
            ]
        )
        await service.process_source(source, channel_id=777000)

    asyncio.run(run_flow())

    commands = state_repository.list_outbox_commands()
    cancel_command = next(
        command
        for command in commands
        if command.command_type.value == "CANCEL_PENDING_ORDER"
    )
    order_id = cancel_command.order_id
    assert order_id is not None

    state_repository.update_outbox_command_state(cancel_command.id, OutboxCommandState.UNKNOWN)
    unknown_state = next(
        order for order in state_repository.list_orders() if order.id == order_id
    ).state.value
    assert unknown_state == "CANCEL_UNKNOWN"

    state_repository.update_outbox_command_state(cancel_command.id, OutboxCommandState.FAILED)
    failed_state = next(
        order for order in state_repository.list_orders() if order.id == order_id
    ).state.value
    assert failed_state == "CANCEL_FAILED"

    state_repository.update_outbox_command_state(cancel_command.id, OutboxCommandState.SUCCEEDED)
    succeeded_state = next(
        order for order in state_repository.list_orders() if order.id == order_id
    ).state.value
    assert succeeded_state == "CANCELLED"
