from __future__ import annotations

import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from fictional_engine.adapters.persistence.database import build_engine
from fictional_engine.adapters.persistence.raw_message_repository import (
    SqlAlchemyRawMessageRepository,
)
from fictional_engine.adapters.persistence.trading_state_repository import (
    SqlAlchemyTradingStateRepository,
)
from fictional_engine.adapters.telegram.fixtures import load_fixture_envelopes
from fictional_engine.application.ingestion import RawMessageIngestionService
from fictional_engine.application.parsing import DeterministicMessageParser
from fictional_engine.application.replay_cli import run_migrations
from fictional_engine.application.state_machine import (
    StatefulMessageProcessingService,
    StatefulReplayService,
)
from fictional_engine.domain.parsing import MessageClassification
from fictional_engine.domain.raw_messages import RawTelegramMessage
from fictional_engine.domain.state import (
    BrokerCommandType,
    OrderState,
    OutboxCommandState,
    PositionState,
    SessionState,
)

INITIAL_ORDERS_TEXT = """Hello traders,

These are my first orders for today:

Instrument: US30

Cronos Markets data:
SELL STOP
Entry: 52547.00
SL: 52832.00 (-2 850.0 pips)
TP: 52413.00 (1 340.0 pips)

BUY STOP
Entry: 52829.00
SL: 52544.00 (-2 850.0 pips)
TP: 52963.00 (1 340.0 pips)

Funding Dynasty data:
SELL STOP
Entry: 52547.00
SL: 52832.00
TP: 52413.00

BUY STOP
Entry: 52829.00
SL: 52544.00
TP: 52963.00"""

TRIGGER_REPLACE_TEXT = """The sell stop order was triggered.
Delete the buy stop order.

I've placed a new buy stop:

CronosMarkets data:
BUY STOP Order
Entry: 52832.00
SL: 52547.00 (-2 850.0 pips)
TP: 52893.00 (610.0 pips)

Funding Dynasty data:
BUY STOP Order
Entry: 52832.00
SL: 52547.00
TP: 52893.00"""

TP_TEXT = "TP. 🥇"
AMBIGUOUS_DELETE_TEXT = "Delete the order."
SESSION_END_TEXT = "We are ending today\u2019s session here."
DELETE_AND_END_TEXT = "Delete the buy stop order, we are ending today\u2019s session here."
BUY_TRIGGER_TEXT = "The buy stop order was triggered."
FOLLOW_UP_PLACE_TEXT = """Instrument: US30

Cronos Markets data:
BUY STOP
Entry: 52840.00
SL: 52555.00
TP: 52910.00"""
AMBIGUOUS_INITIAL_TEXT = """Instrument: US30

Cronos Markets data:
SELL STOP
Entry: 52547.00
SL: 52832.00
TP: 52413.00

BUY STOP
Entry: 52829.00
SL: 52544.00
TP: 52963.00

BUY STOP
Entry: 52860.00
SL: 52575.00
TP: 52995.00"""
NO_TRADING_TEXT = """Today we have bank holiday in USA.
We will not trade today. See you back tomorrow."""


def _build_processor(
    session_factory: sessionmaker[Session],
    *,
    preferred_provider: str | None = "Cronos Markets",
) -> tuple[StatefulMessageProcessingService, SqlAlchemyTradingStateRepository]:
    raw_repository = SqlAlchemyRawMessageRepository(session_factory)
    state_repository = SqlAlchemyTradingStateRepository(session_factory)
    processor = StatefulMessageProcessingService(
        RawMessageIngestionService(raw_repository),
        DeterministicMessageParser(preferred_provider=preferred_provider),
        state_repository,
    )
    return processor, state_repository


def _build_replay_service(
    session_factory: sessionmaker[Session],
) -> tuple[StatefulReplayService, SqlAlchemyTradingStateRepository]:
    processor, repository = _build_processor(session_factory)
    return StatefulReplayService(processor), repository


def _raw_message(
    text: str,
    *,
    message_id: int,
    message_date: datetime,
    edit_date: datetime | None = None,
) -> RawTelegramMessage:
    return RawTelegramMessage(
        channel_id=777000,
        message_id=message_id,
        message_date=message_date,
        edit_date=edit_date,
        sender_name="Trading Busters",
        channel_title="Trading Busters Signals",
        text=text,
        media_metadata={},
        telegram_metadata={"source": "tests", "timezone": "UTC"},
    )


def test_m3_migration_adds_state_tables(sqlite_database_url: str) -> None:
    run_migrations(sqlite_database_url)

    tables = set(inspect(build_engine(sqlite_database_url)).get_table_names())

    assert {
        "raw_telegram_message_versions",
        "sessions",
        "orders",
        "positions",
        "processed_events",
        "manual_reviews",
        "command_outbox",
    }.issubset(tables)


def test_canonical_september_9_sequence_reaches_expected_final_state(
    migrated_session_factory: sessionmaker[Session],
    replay_fixture_path: Path,
) -> None:
    service, repository = _build_replay_service(migrated_session_factory)

    summary = service.replay_fixtures(replay_fixture_path)
    sessions = repository.list_sessions()
    orders = repository.list_orders()
    positions = repository.list_positions()
    commands = repository.list_outbox_commands()
    reviews = repository.list_manual_reviews()

    assert summary.processed_inputs == 6
    assert summary.inserted_versions == 5
    assert summary.duplicate_inputs == 1
    assert len(sessions) == 1
    assert sessions[0].state == SessionState.ENDED
    assert all(order.state != OrderState.PENDING for order in orders)
    assert all(position.state != PositionState.ACTIVE for position in positions)
    assert len(commands) == 5
    assert (
        sum(command.command_type == BrokerCommandType.PLACE_PENDING_ORDER for command in commands)
        == 3
    )
    assert (
        sum(command.command_type == BrokerCommandType.CANCEL_PENDING_ORDER for command in commands)
        == 2
    )
    assert not any(command.command_type.value == "CLOSE_POSITION" for command in commands)
    assert reviews == []


def test_duplicate_replay_creates_zero_additional_commands(
    migrated_session_factory: sessionmaker[Session],
    replay_fixture_path: Path,
) -> None:
    service, repository = _build_replay_service(migrated_session_factory)

    first_summary = service.replay_fixtures(replay_fixture_path)
    first_command_count = len(repository.list_outbox_commands())
    first_processed_event_count = len(repository.list_processed_events())

    second_summary = service.replay_fixtures(replay_fixture_path)

    assert first_summary.inserted_versions == 5
    assert second_summary.inserted_versions == 0
    assert len(repository.list_outbox_commands()) == first_command_count == 5
    assert len(repository.list_processed_events()) == first_processed_event_count


def test_restart_safe_replay_continues_from_persisted_state(
    migrated_session_factory: sessionmaker[Session],
    replay_fixture_path: Path,
) -> None:
    envelopes = load_fixture_envelopes(replay_fixture_path)
    first_processor, _ = _build_processor(migrated_session_factory)

    for envelope in envelopes[:3]:
        first_processor.process_raw_message(envelope.raw_message, envelope.source_payload)

    second_processor, repository = _build_processor(migrated_session_factory)
    for envelope in envelopes[3:]:
        second_processor.process_raw_message(envelope.raw_message, envelope.source_payload)

    sessions = repository.list_sessions()
    commands = repository.list_outbox_commands()

    assert len(sessions) == 1
    assert sessions[0].state == SessionState.ENDED
    assert len(commands) == 5


def test_tp_creates_no_close_command_and_closes_single_active_position(
    migrated_session_factory: sessionmaker[Session],
) -> None:
    processor, repository = _build_processor(migrated_session_factory)

    processor.process_raw_message(
        _raw_message(
            INITIAL_ORDERS_TEXT,
            message_id=201,
            message_date=datetime(2026, 9, 9, 12, 10, tzinfo=UTC),
        ),
        {"fixture_id": "initial"},
    )
    processor.process_raw_message(
        _raw_message(
            TRIGGER_REPLACE_TEXT,
            message_id=202,
            message_date=datetime(2026, 9, 9, 12, 37, tzinfo=UTC),
        ),
        {"fixture_id": "trigger"},
    )
    result = processor.process_raw_message(
        _raw_message(
            TP_TEXT,
            message_id=203,
            message_date=datetime(2026, 9, 9, 14, 13, tzinfo=UTC),
        ),
        {"fixture_id": "tp"},
    )

    assert result.parsing.classification == MessageClassification.ORDER_STATUS
    assert len(repository.list_outbox_commands()) == 4
    assert not any(
        command.command_type.value == "CLOSE_POSITION"
        for command in repository.list_outbox_commands()
    )
    assert repository.list_positions()[0].state == PositionState.CLOSED_TP


def test_ambiguous_tp_creates_manual_review_and_no_state_mutation(
    migrated_session_factory: sessionmaker[Session],
) -> None:
    processor, repository = _build_processor(migrated_session_factory)
    processor.process_raw_message(
        _raw_message(
            INITIAL_ORDERS_TEXT,
            message_id=301,
            message_date=datetime(2026, 9, 9, 12, 10, tzinfo=UTC),
        ),
        {"fixture_id": "initial"},
    )
    processor.process_raw_message(
        _raw_message(
            "The sell stop order was triggered.",
            message_id=302,
            message_date=datetime(2026, 9, 9, 12, 20, tzinfo=UTC),
        ),
        {"fixture_id": "trigger-sell"},
    )
    processor.process_raw_message(
        _raw_message(
            BUY_TRIGGER_TEXT,
            message_id=303,
            message_date=datetime(2026, 9, 9, 12, 21, tzinfo=UTC),
        ),
        {"fixture_id": "trigger-buy"},
    )

    processor.process_raw_message(
        _raw_message(
            TP_TEXT,
            message_id=304,
            message_date=datetime(2026, 9, 9, 14, 13, tzinfo=UTC),
        ),
        {"fixture_id": "tp"},
    )

    assert len(repository.list_manual_reviews()) == 1
    assert (
        repository.list_manual_reviews()[0].reason
        == "trade result lacks a unique active position target"
    )
    assert len(repository.list_outbox_commands()) == 2
    assert (
        sum(position.state == PositionState.ACTIVE for position in repository.list_positions()) == 2
    )


def test_ambiguous_delete_creates_manual_review_and_no_cancellation(
    migrated_session_factory: sessionmaker[Session],
) -> None:
    processor, repository = _build_processor(migrated_session_factory)
    processor.process_raw_message(
        _raw_message(
            INITIAL_ORDERS_TEXT,
            message_id=401,
            message_date=datetime(2026, 9, 9, 12, 10, tzinfo=UTC),
        ),
        {"fixture_id": "initial"},
    )

    processor.process_raw_message(
        _raw_message(
            AMBIGUOUS_DELETE_TEXT,
            message_id=402,
            message_date=datetime(2026, 9, 9, 12, 11, tzinfo=UTC),
        ),
        {"fixture_id": "ambiguous-delete"},
    )

    assert len(repository.list_manual_reviews()) == 1
    assert repository.list_manual_reviews()[0].reason == "delete instruction is ambiguous"
    assert sum(order.state == OrderState.PENDING for order in repository.list_orders()) == 2
    assert len(repository.list_outbox_commands()) == 2


def test_delete_never_closes_a_filled_position(
    migrated_session_factory: sessionmaker[Session],
) -> None:
    processor, repository = _build_processor(migrated_session_factory)
    processor.process_raw_message(
        _raw_message(
            INITIAL_ORDERS_TEXT,
            message_id=501,
            message_date=datetime(2026, 9, 9, 12, 10, tzinfo=UTC),
        ),
        {"fixture_id": "initial"},
    )
    processor.process_raw_message(
        _raw_message(
            BUY_TRIGGER_TEXT,
            message_id=502,
            message_date=datetime(2026, 9, 9, 12, 20, tzinfo=UTC),
        ),
        {"fixture_id": "trigger-buy"},
    )

    processor.process_raw_message(
        _raw_message(
            "Delete the buy stop order.",
            message_id=503,
            message_date=datetime(2026, 9, 9, 12, 21, tzinfo=UTC),
        ),
        {"fixture_id": "delete-buy"},
    )

    assert len(repository.list_manual_reviews()) == 1
    assert repository.list_positions()[0].state == PositionState.ACTIVE
    assert (
        sum(
            command.command_type == BrokerCommandType.CANCEL_PENDING_ORDER
            for command in repository.list_outbox_commands()
        )
        == 0
    )


def test_session_end_with_active_position_does_not_close_or_cancel_unspecified_actions(
    migrated_session_factory: sessionmaker[Session],
) -> None:
    processor, repository = _build_processor(migrated_session_factory)
    processor.process_raw_message(
        _raw_message(
            INITIAL_ORDERS_TEXT,
            message_id=601,
            message_date=datetime(2026, 9, 9, 12, 10, tzinfo=UTC),
        ),
        {"fixture_id": "initial"},
    )
    processor.process_raw_message(
        _raw_message(
            "The sell stop order was triggered.",
            message_id=602,
            message_date=datetime(2026, 9, 9, 12, 20, tzinfo=UTC),
        ),
        {"fixture_id": "trigger-sell"},
    )

    processor.process_raw_message(
        _raw_message(
            SESSION_END_TEXT,
            message_id=603,
            message_date=datetime(2026, 9, 9, 12, 21, tzinfo=UTC),
        ),
        {"fixture_id": "end"},
    )

    assert repository.list_sessions()[0].state == SessionState.ENDED
    assert repository.list_positions()[0].state == PositionState.ACTIVE
    assert sum(order.state == OrderState.PENDING for order in repository.list_orders()) == 1


def test_terminal_order_cannot_return_to_pending_after_session_end(
    migrated_session_factory: sessionmaker[Session],
) -> None:
    processor, repository = _build_processor(migrated_session_factory)
    processor.process_raw_message(
        _raw_message(
            INITIAL_ORDERS_TEXT,
            message_id=701,
            message_date=datetime(2026, 9, 9, 12, 10, tzinfo=UTC),
        ),
        {"fixture_id": "initial"},
    )
    processor.process_raw_message(
        _raw_message(
            TRIGGER_REPLACE_TEXT,
            message_id=702,
            message_date=datetime(2026, 9, 9, 12, 37, tzinfo=UTC),
        ),
        {"fixture_id": "trigger"},
    )
    processor.process_raw_message(
        _raw_message(
            TP_TEXT,
            message_id=703,
            message_date=datetime(2026, 9, 9, 14, 13, tzinfo=UTC),
        ),
        {"fixture_id": "tp"},
    )
    processor.process_raw_message(
        _raw_message(
            DELETE_AND_END_TEXT,
            message_id=704,
            message_date=datetime(2026, 9, 9, 14, 14, tzinfo=UTC),
        ),
        {"fixture_id": "end"},
    )

    processor.process_raw_message(
        _raw_message(
            FOLLOW_UP_PLACE_TEXT,
            message_id=705,
            message_date=datetime(2026, 9, 9, 14, 15, tzinfo=UTC),
        ),
        {"fixture_id": "follow-up"},
    )

    assert repository.list_sessions()[0].state == SessionState.ENDED
    assert sum(order.state == OrderState.PENDING for order in repository.list_orders()) == 0
    assert len(repository.list_manual_reviews()) == 1


def test_replacement_placement_is_blocked_when_cancellation_is_unresolved(
    migrated_session_factory: sessionmaker[Session],
) -> None:
    processor, repository = _build_processor(migrated_session_factory)
    processor.process_raw_message(
        _raw_message(
            AMBIGUOUS_INITIAL_TEXT,
            message_id=801,
            message_date=datetime(2026, 9, 9, 12, 10, tzinfo=UTC),
        ),
        {"fixture_id": "ambiguous-initial"},
    )

    processor.process_raw_message(
        _raw_message(
            TRIGGER_REPLACE_TEXT,
            message_id=802,
            message_date=datetime(2026, 9, 9, 12, 37, tzinfo=UTC),
        ),
        {"fixture_id": "trigger-replace"},
    )

    reviews = repository.list_manual_reviews()
    assert [review.reason for review in reviews] == [
        "cancel instruction lacks a unique pending order target",
        "replacement placement blocked until cancellation is resolved",
    ]
    assert (
        sum(
            command.command_type == BrokerCommandType.PLACE_PENDING_ORDER
            for command in repository.list_outbox_commands()
        )
        == 3
    )
    assert sum(order.state == OrderState.PENDING for order in repository.list_orders()) == 2


def test_no_trading_day_persists_and_blocks_entries_after_restart(
    migrated_session_factory: sessionmaker[Session],
) -> None:
    first_processor, _ = _build_processor(migrated_session_factory)
    first_processor.process_raw_message(
        _raw_message(
            NO_TRADING_TEXT,
            message_id=901,
            message_date=datetime(2026, 9, 10, 7, 0, tzinfo=UTC),
        ),
        {"fixture_id": "no-trading"},
    )

    second_processor, repository = _build_processor(migrated_session_factory)
    second_processor.process_raw_message(
        _raw_message(
            FOLLOW_UP_PLACE_TEXT,
            message_id=902,
            message_date=datetime(2026, 9, 10, 8, 0, tzinfo=UTC),
        ),
        {"fixture_id": "follow-up-place"},
    )

    assert repository.list_sessions() == []
    assert repository.list_orders() == []
    assert repository.list_outbox_commands() == []
    assert [review.reason for review in repository.list_manual_reviews()] == [
        "session does not accept new entries"
    ]


def test_replacement_placement_waits_for_cancellation_success(
    migrated_session_factory: sessionmaker[Session],
) -> None:
    processor, repository = _build_processor(migrated_session_factory)
    processor.process_raw_message(
        _raw_message(
            INITIAL_ORDERS_TEXT,
            message_id=910,
            message_date=datetime(2026, 9, 9, 12, 10, tzinfo=UTC),
        ),
        {"fixture_id": "initial"},
    )
    processor.process_raw_message(
        _raw_message(
            TRIGGER_REPLACE_TEXT,
            message_id=911,
            message_date=datetime(2026, 9, 9, 12, 37, tzinfo=UTC),
        ),
        {"fixture_id": "trigger-replace"},
    )

    commands = repository.list_outbox_commands()
    cancel_command = next(
        command
        for command in commands
        if command.command_type == BrokerCommandType.CANCEL_PENDING_ORDER
    )
    blocked_place = next(
        command
        for command in commands
        if command.command_type == BrokerCommandType.PLACE_PENDING_ORDER
        and command.depends_on_command_id == cancel_command.id
    )

    assert blocked_place.state == OutboxCommandState.BLOCKED

    assert repository.update_outbox_command_state(
        cancel_command.id,
        OutboxCommandState.PENDING,
    ) == ()
    blocked_place_after_pending = next(
        command for command in repository.list_outbox_commands() if command.id == blocked_place.id
    )
    assert blocked_place_after_pending.state == OutboxCommandState.BLOCKED

    assert (
        repository.update_outbox_command_state(cancel_command.id, OutboxCommandState.UNKNOWN)
        == ()
    )
    blocked_place_after_unknown = next(
        command for command in repository.list_outbox_commands() if command.id == blocked_place.id
    )
    assert blocked_place_after_unknown.state == OutboxCommandState.BLOCKED

    assert (
        repository.update_outbox_command_state(cancel_command.id, OutboxCommandState.FAILED)
        == ()
    )
    blocked_place_after_failed = next(
        command for command in repository.list_outbox_commands() if command.id == blocked_place.id
    )
    assert blocked_place_after_failed.state == OutboxCommandState.BLOCKED

    released = repository.update_outbox_command_state(
        cancel_command.id, OutboxCommandState.SUCCEEDED
    )
    assert released == (blocked_place.id,)

    blocked_place_after_success = next(
        command for command in repository.list_outbox_commands() if command.id == blocked_place.id
    )
    assert blocked_place_after_success.state == OutboxCommandState.PENDING
    assert blocked_place_after_success.released_at is not None

    assert (
        repository.update_outbox_command_state(cancel_command.id, OutboxCommandState.SUCCEEDED)
        == ()
    )


def test_replacement_dependency_survives_restart_without_duplicate_release(
    migrated_session_factory: sessionmaker[Session],
) -> None:
    first_processor, _ = _build_processor(migrated_session_factory)
    first_processor.process_raw_message(
        _raw_message(
            INITIAL_ORDERS_TEXT,
            message_id=920,
            message_date=datetime(2026, 9, 9, 12, 10, tzinfo=UTC),
        ),
        {"fixture_id": "initial"},
    )
    first_processor.process_raw_message(
        _raw_message(
            TRIGGER_REPLACE_TEXT,
            message_id=921,
            message_date=datetime(2026, 9, 9, 12, 37, tzinfo=UTC),
        ),
        {"fixture_id": "trigger-replace"},
    )

    second_repository = SqlAlchemyTradingStateRepository(migrated_session_factory)
    commands = second_repository.list_outbox_commands()
    cancel_command = next(
        command
        for command in commands
        if command.command_type == BrokerCommandType.CANCEL_PENDING_ORDER
    )
    blocked_place = next(
        command
        for command in commands
        if command.command_type == BrokerCommandType.PLACE_PENDING_ORDER
        and command.depends_on_command_id == cancel_command.id
    )

    released = second_repository.update_outbox_command_state(
        cancel_command.id,
        OutboxCommandState.SUCCEEDED,
    )
    assert released == (blocked_place.id,)
    assert len(second_repository.list_outbox_commands()) == 4
    assert (
        second_repository.update_outbox_command_state(
            cancel_command.id,
            OutboxCommandState.SUCCEEDED,
        )
        == ()
    )
    assert len(second_repository.list_outbox_commands()) == 4


def test_transactional_rollback_on_persist_failure(
    migrated_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processor, repository = _build_processor(migrated_session_factory)
    original_persist_mutation = repository._persist_mutation

    def fail_after_persist(
        session: Session,
        snapshot: Any,
        parsed_message: Any,
        processed_at: datetime,
        event_identity: Any,
        event: Any,
        event_fingerprint: str,
        mutation: Any,
    ) -> None:
        original_persist_mutation(
            session,
            snapshot,
            parsed_message,
            processed_at,
            event_identity,
            event,
            event_fingerprint,
            mutation,
        )
        raise RuntimeError("injected persistence failure")

    monkeypatch.setattr(repository, "_persist_mutation", fail_after_persist)

    with pytest.raises(RuntimeError, match="injected persistence failure"):
        processor.process_raw_message(
            _raw_message(
                INITIAL_ORDERS_TEXT,
                message_id=930,
                message_date=datetime(2026, 9, 9, 12, 10, tzinfo=UTC),
            ),
            {"fixture_id": "initial"},
        )

    assert repository.list_sessions() == []
    assert repository.list_orders() == []
    assert repository.list_positions() == []
    assert repository.list_processed_events() == []
    assert repository.list_outbox_commands() == []


def test_stateful_replay_makes_no_network_calls(
    migrated_session_factory: sessionmaker[Session],
    replay_fixture_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access during M3 replay is forbidden")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket.socket, "connect", fail_network)

    service, repository = _build_replay_service(migrated_session_factory)
    summary = service.replay_fixtures(replay_fixture_path)

    assert summary.inserted_versions == 5
    assert len(repository.list_outbox_commands()) == 5
