from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from fictional_engine.domain.parsing import ParsedMessage, PendingOrderType, TradeSide


class SessionState(StrEnum):
    ACCEPTING_SIGNALS = "ACCEPTING_SIGNALS"
    NO_TRADING = "NO_TRADING"
    ENDING = "ENDING"
    ENDED = "ENDED"
    PAUSED_BY_OPERATOR = "PAUSED_BY_OPERATOR"
    ERROR_RECONCILIATION = "ERROR_RECONCILIATION"


class OrderState(StrEnum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class PositionState(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED_TP = "CLOSED_TP"
    CLOSED_SL = "CLOSED_SL"
    CLOSED_BREAK_EVEN = "CLOSED_BREAK_EVEN"
    CLOSED_MANUAL = "CLOSED_MANUAL"


class ProcessedEventOutcome(StrEnum):
    APPLIED = "APPLIED"
    NOOP = "NOOP"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class BrokerCommandType(StrEnum):
    PLACE_PENDING_ORDER = "PLACE_PENDING_ORDER"
    CANCEL_PENDING_ORDER = "CANCEL_PENDING_ORDER"


class OutboxCommandState(StrEnum):
    BLOCKED = "BLOCKED"
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"


@dataclass(frozen=True)
class EventIdentity:
    channel_id: int
    message_id: int
    version: int
    event_index: int

    @property
    def key(self) -> str:
        return f"{self.channel_id}:{self.message_id}:{self.version}:{self.event_index}"


@dataclass
class SessionAggregate:
    id: str
    channel_id: int
    session_date: date
    instrument: str
    state: SessionState
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None


@dataclass
class NoTradingDayAggregate:
    id: str
    channel_id: int
    session_date: date
    source_event_key: str
    created_at: datetime


@dataclass
class OrderAggregate:
    id: str
    session_id: str
    instrument: str
    side: TradeSide
    order_type: PendingOrderType
    provider: str | None
    entry: Decimal
    stop_loss: Decimal
    take_profits: tuple[Decimal, ...]
    state: OrderState
    source_event_key: str
    created_at: datetime
    updated_at: datetime
    filled_at: datetime | None = None
    cancelled_at: datetime | None = None


@dataclass
class PositionAggregate:
    id: str
    session_id: str
    source_order_id: str
    instrument: str
    side: TradeSide
    entry: Decimal
    stop_loss: Decimal
    take_profits: tuple[Decimal, ...]
    state: PositionState
    source_event_key: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None


@dataclass(frozen=True)
class ProcessedEventRecord:
    event_key: str
    channel_id: int
    message_id: int
    version: int
    event_index: int
    event_type: str
    event_fingerprint: str
    outcome: ProcessedEventOutcome
    created_at: datetime


@dataclass(frozen=True)
class ManualReviewRecord:
    id: str
    event_key: str
    channel_id: int
    message_id: int
    version: int
    event_index: int
    reason: str
    details: str | None
    created_at: datetime


@dataclass(frozen=True)
class OutboxCommand:
    id: str
    idempotency_key: str
    event_key: str
    command_type: BrokerCommandType
    payload: dict[str, Any]
    state: OutboxCommandState
    created_at: datetime
    session_id: str | None = None
    order_id: str | None = None
    position_id: str | None = None
    depends_on_command_id: str | None = None
    released_at: datetime | None = None


@dataclass(frozen=True)
class AppliedEventResult:
    event_key: str
    event_type: str
    outcome: ProcessedEventOutcome
    outbox_command_ids: tuple[str, ...] = ()
    manual_review_id: str | None = None


@dataclass(frozen=True)
class MessageProcessingResult:
    parsed_message: ParsedMessage
    applied_events: tuple[AppliedEventResult, ...]
    state_snapshot: TradingStateSnapshot


@dataclass
class TradingStateSnapshot:
    sessions: list[SessionAggregate] = field(default_factory=list)
    no_trading_days: list[NoTradingDayAggregate] = field(default_factory=list)
    orders: list[OrderAggregate] = field(default_factory=list)
    positions: list[PositionAggregate] = field(default_factory=list)

    def has_no_trading_day(self, *, channel_id: int, session_date: date) -> bool:
        return any(
            item.channel_id == channel_id and item.session_date == session_date
            for item in self.no_trading_days
        )

    def pending_orders(self) -> list[OrderAggregate]:
        return [order for order in self.orders if order.state == OrderState.PENDING]

    def active_positions(self) -> list[PositionAggregate]:
        return [position for position in self.positions if position.state == PositionState.ACTIVE]
