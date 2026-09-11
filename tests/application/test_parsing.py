from __future__ import annotations

import socket
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from fictional_engine.application.parsing import DeterministicMessageParser
from fictional_engine.domain.parsing import (
    CancelPendingOrder,
    MarkOrderTriggered,
    MessageClassification,
    MessageSource,
    NoTradingDay,
    ParseStatus,
    PendingOrderType,
    PlacePendingOrder,
    RequestManualReview,
    TradeSide,
)
from fictional_engine.domain.raw_messages import RawTelegramMessage

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

PROMOTION_TEXT = """$54,181.90 paid out to five traders in one week.
The profit target has been reduced and accounts receive a discount.
Use discount code SEPT30."""

MASTERCLASS_TEXT = """MasterClass event announcement: join our trading MasterClass this weekend.
Educational and promotional content only."""

FUNDED_ACCOUNT_TEXT = (
    "A funded-account update: payout completed and the account size has been increased.\n"
    "Discounts are available this week."
)

DISCLAIMER_TEXT = """Disclaimer: this is educational content only and not financial advice.
Nothing here guarantees results."""

NO_TRADING_TEXT = """Today we have bank holiday in USA.
We will not trade today. See you back tomorrow."""

RISK_ADVISORY_TEXT = """Between April 16 and September 30 we normally pause this strategy.
We will continue sending trades, but you should reduce your risk or take a break."""

TP_TEXT = "TP. 🥇"
AMBIGUOUS_DELETE_TEXT = "Delete the order."
MISSING_FIELD_TEXT = """Instrument: US30

Cronos Markets data:
SELL STOP
Entry: 52547.00
TP: 52413.00"""
MALFORMED_NUMBER_TEXT = """Instrument: US30

Cronos Markets data:
SELL STOP
Entry: 52A47.00
SL: 52832.00
TP: 52413.00"""
DUPLICATE_PROVIDER_BLOCK_TEXT = """Instrument: US30

Cronos Markets data:
SELL STOP
Entry: 52547.00
SL: 52832.00
TP: 52413.00

BUY STOP
Entry: 52829.00
SL: 52544.00
TP: 52963.00

CronosMarkets data:
SELL STOP
Entry: 52547.00
SL: 52832.00
TP: 52413.00

BUY STOP
Entry: 52829.00
SL: 52544.00
TP: 52963.00

Funding Dynasty data:
SELL STOP
Entry: 52547.00
SL: 52832.00
TP: 52413.00

BUY STOP
Entry: 52829.00
SL: 52544.00
TP: 52963.00"""
NORMALIZED_VARIANT_TEXT = """hELLO traders\u200b\u200b

Instrument:\u00a0US30

CRONOSMARKETS   data :
SELL STOP
Entry: 52547.00
SL: 52832.00
TP: 52413.00

BUY STOP
Entry: 52829.00
SL: 52544.00
TP: 52963.00

Funding\u00a0Dynasty DATA:
SELL STOP
Entry: 52547.00
SL: 52832.00
TP: 52413.00

BUY STOP
Entry: 52829.00
SL: 52544.00
TP: 52963.00

https://example.com/trade?ref=ignored"""


@pytest.fixture
def parser() -> DeterministicMessageParser:
    return DeterministicMessageParser()


def build_source(
    text: str,
    *,
    message_id: int = 101,
    version: int = 1,
    channel_id: int = 777000,
) -> MessageSource:
    raw_message = RawTelegramMessage(
        channel_id=channel_id,
        message_id=message_id,
        message_date=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
        text=text,
        media_metadata={},
        telegram_metadata={"source": "tests"},
    )
    return MessageSource.from_raw_message(raw_message, version=version)


def test_initial_order_message_produces_two_pending_orders(
    parser: DeterministicMessageParser,
) -> None:
    result = parser.parse(build_source(INITIAL_ORDERS_TEXT))

    assert result.source.channel_id == 777000
    assert result.source.message_id == 101
    assert result.source.version == 1
    assert result.classification == MessageClassification.TRADE_INSTRUCTION
    assert result.status == ParseStatus.PROPOSED
    assert result.confidence == 1.0
    assert len(result.provider_blocks) == 2
    assert {block.provider for block in result.provider_blocks} == {
        "Cronos Markets",
        "Funding Dynasty",
    }
    assert len(result.events) == 2

    first_event = result.events[0]
    second_event = result.events[1]

    assert isinstance(first_event, PlacePendingOrder)
    assert first_event.side == TradeSide.SELL
    assert first_event.order_type == PendingOrderType.STOP
    assert first_event.instrument == "US30"
    assert first_event.entry == Decimal("52547.00")
    assert first_event.stop_loss == Decimal("52832.00")
    assert first_event.take_profits == (Decimal("52413.00"),)
    assert any("SELL STOP" in span.text for span in first_event.evidence)
    assert any("Entry: 52547.00" in span.text for span in first_event.evidence)

    assert isinstance(second_event, PlacePendingOrder)
    assert second_event.side == TradeSide.BUY
    assert second_event.order_type == PendingOrderType.STOP
    assert second_event.instrument == "US30"
    assert second_event.entry == Decimal("52829.00")
    assert second_event.stop_loss == Decimal("52544.00")
    assert second_event.take_profits == (Decimal("52963.00"),)
    assert any("BUY STOP" in span.text for span in second_event.evidence)


def test_provider_blocks_are_explicit_and_not_mixed(parser: DeterministicMessageParser) -> None:
    result = parser.parse(build_source(INITIAL_ORDERS_TEXT))

    assert len(result.provider_blocks) == 2
    cronos, funding = result.provider_blocks
    assert cronos.provider == "Cronos Markets"
    assert funding.provider == "Funding Dynasty"
    assert cronos.orders[0].entry == funding.orders[0].entry
    assert cronos.orders[1].entry == funding.orders[1].entry
    assert cronos.orders[0].stop_loss == funding.orders[0].stop_loss
    assert cronos.orders[1].take_profits == funding.orders[1].take_profits


def test_duplicate_provider_blocks_do_not_double_count_events(
    parser: DeterministicMessageParser,
) -> None:
    result = parser.parse(build_source(DUPLICATE_PROVIDER_BLOCK_TEXT))

    assert result.classification == MessageClassification.TRADE_INSTRUCTION
    assert len(result.provider_blocks) == 3
    assert len(result.events) == 2
    assert [type(event) for event in result.events] == [PlacePendingOrder, PlacePendingOrder]


@pytest.mark.parametrize(
    ("text", "expected_classification"),
    [
        (PROMOTION_TEXT, MessageClassification.PROMOTION),
        (MASTERCLASS_TEXT, MessageClassification.PROMOTION),
        (FUNDED_ACCOUNT_TEXT, MessageClassification.PROMOTION),
        (DISCLAIMER_TEXT, MessageClassification.EDUCATIONAL),
    ],
)
def test_promotions_and_educational_filters(
    parser: DeterministicMessageParser,
    text: str,
    expected_classification: MessageClassification,
) -> None:
    result = parser.parse(build_source(text))

    assert result.classification == expected_classification
    assert result.events == ()
    assert result.status == ParseStatus.PROPOSED


def test_no_trading_day_message_yields_no_trading_day(parser: DeterministicMessageParser) -> None:
    result = parser.parse(build_source(NO_TRADING_TEXT))

    assert result.classification == MessageClassification.NO_TRADING_DAY
    assert result.status == ParseStatus.PROPOSED
    assert len(result.events) == 1
    assert isinstance(result.events[0], NoTradingDay)
    assert result.events[0].reason == "bank holiday or no-trading announcement"


def test_seasonal_advisory_yields_zero_events(parser: DeterministicMessageParser) -> None:
    result = parser.parse(build_source(RISK_ADVISORY_TEXT))

    assert result.classification == MessageClassification.RISK_ADVISORY
    assert result.status == ParseStatus.PROPOSED
    assert result.events == ()


def test_trigger_replace_message_parses_ordered_events(parser: DeterministicMessageParser) -> None:
    result = parser.parse(build_source(TRIGGER_REPLACE_TEXT, message_id=102))

    assert result.classification == MessageClassification.TRADE_INSTRUCTION
    assert result.status == ParseStatus.CONTEXT_REQUIRED
    assert [type(event) for event in result.events] == [
        MarkOrderTriggered,
        CancelPendingOrder,
        PlacePendingOrder,
    ]

    trigger_event = result.events[0]
    cancel_event = result.events[1]
    place_event = result.events[2]

    assert isinstance(trigger_event, MarkOrderTriggered)
    assert trigger_event.side == TradeSide.SELL
    assert trigger_event.order_type == PendingOrderType.STOP
    assert any("triggered" in span.text.lower() for span in trigger_event.evidence)

    assert isinstance(cancel_event, CancelPendingOrder)
    assert cancel_event.side == TradeSide.BUY
    assert cancel_event.order_type == PendingOrderType.STOP

    assert isinstance(place_event, PlacePendingOrder)
    assert place_event.instrument is None
    assert place_event.side == TradeSide.BUY
    assert place_event.entry == Decimal("52832.00")
    assert place_event.stop_loss == Decimal("52547.00")
    assert place_event.take_profits == (Decimal("52893.00"),)


def test_replacement_message_does_not_infer_instrument(parser: DeterministicMessageParser) -> None:
    result = parser.parse(build_source(TRIGGER_REPLACE_TEXT, message_id=120))

    assert result.classification == MessageClassification.TRADE_INSTRUCTION
    assert result.status == ParseStatus.CONTEXT_REQUIRED
    assert all(
        order.instrument is None for block in result.provider_blocks for order in block.orders
    )
    assert all(
        event.instrument is None
        for event in result.events
        if isinstance(event, PlacePendingOrder)
    )


def test_tp_message_requires_manual_review(parser: DeterministicMessageParser) -> None:
    result = parser.parse(build_source(TP_TEXT, message_id=103))

    assert result.classification == MessageClassification.ORDER_STATUS
    assert result.status == ParseStatus.MANUAL_REVIEW
    assert result.confidence == 0.0
    assert len(result.events) == 1
    assert isinstance(result.events[0], RequestManualReview)
    assert result.events[0].reason == "tp instruction requires position context"
    assert result.provider_blocks == ()


def test_ambiguous_delete_message_requires_manual_review(
    parser: DeterministicMessageParser,
) -> None:
    result = parser.parse(build_source(AMBIGUOUS_DELETE_TEXT, message_id=104))

    assert result.status == ParseStatus.MANUAL_REVIEW
    assert len(result.events) == 1
    assert isinstance(result.events[0], RequestManualReview)
    assert result.events[0].reason == "delete instruction is ambiguous"


def test_malformed_numbers_and_missing_fields_require_manual_review(
    parser: DeterministicMessageParser,
) -> None:
    malformed_result = parser.parse(build_source(MALFORMED_NUMBER_TEXT, message_id=105))
    missing_result = parser.parse(build_source(MISSING_FIELD_TEXT, message_id=106))

    assert malformed_result.status == ParseStatus.MANUAL_REVIEW
    assert missing_result.status == ParseStatus.MANUAL_REVIEW
    assert isinstance(malformed_result.events[0], RequestManualReview)
    assert isinstance(missing_result.events[0], RequestManualReview)


def test_normalization_handles_unicode_spaces_emoji_and_provider_variants(
    parser: DeterministicMessageParser,
) -> None:
    result = parser.parse(build_source(NORMALIZED_VARIANT_TEXT, message_id=107))

    assert result.classification == MessageClassification.TRADE_INSTRUCTION
    assert len(result.events) == 2
    first_event, second_event = result.events
    assert isinstance(first_event, PlacePendingOrder)
    assert isinstance(second_event, PlacePendingOrder)
    assert first_event.entry == Decimal("52547.00")
    assert second_event.entry == Decimal("52829.00")
    assert result.provider_blocks[0].provider == "Cronos Markets"
    assert result.provider_blocks[1].provider == "Funding Dynasty"


def test_parser_does_not_make_network_calls(
    parser: DeterministicMessageParser, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access during parsing is forbidden")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket.socket, "connect", fail_network)

    result = parser.parse(build_source(INITIAL_ORDERS_TEXT, message_id=108))

    assert result.classification == MessageClassification.TRADE_INSTRUCTION
    assert len(result.events) == 2
