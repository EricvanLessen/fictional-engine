from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fictional_engine.domain.raw_messages import RawTelegramMessage


class MessageClassification(StrEnum):
    TRADE_INSTRUCTION = "TRADE_INSTRUCTION"
    ORDER_STATUS = "ORDER_STATUS"
    SESSION_CONTROL = "SESSION_CONTROL"
    NO_TRADING_DAY = "NO_TRADING_DAY"
    RISK_ADVISORY = "RISK_ADVISORY"
    PROMOTION = "PROMOTION"
    EDUCATIONAL = "EDUCATIONAL"
    UNKNOWN = "UNKNOWN"


class ParseStatus(StrEnum):
    PROPOSED = "PROPOSED"
    CONTEXT_REQUIRED = "CONTEXT_REQUIRED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class TradeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class PendingOrderType(StrEnum):
    STOP = "STOP"


class ResultKind(StrEnum):
    TP = "TP"
    SL = "SL"
    BREAK_EVEN = "BREAK_EVEN"


class MessageIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel_id: int
    message_id: int
    version: int
    content_hash: str | None = None


class MessageSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity: MessageIdentity
    message_date: datetime
    edit_date: datetime | None = None
    sender_name: str | None = None
    channel_title: str | None = None
    text: str | None = None
    caption: str | None = None

    @classmethod
    def from_raw_message(
        cls, raw_message: RawTelegramMessage, version: int = 1, content_hash: str | None = None
    ) -> MessageSource:
        return cls(
            identity=MessageIdentity(
                channel_id=raw_message.channel_id,
                message_id=raw_message.message_id,
                version=version,
                content_hash=content_hash or raw_message.content_hash,
            ),
            message_date=raw_message.message_date,
            edit_date=raw_message.edit_date,
            sender_name=raw_message.sender_name,
            channel_title=raw_message.channel_title,
            text=raw_message.text,
            caption=raw_message.caption,
        )

    @property
    def full_text(self) -> str:
        return self.caption if self.caption is not None else (self.text or "")


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: int
    end: int
    text: str
    label: str | None = None


class ParsedOrderInstruction(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    instrument: str | None = None
    side: TradeSide
    order_type: PendingOrderType
    entry: Decimal
    stop_loss: Decimal
    take_profits: tuple[Decimal, ...]
    evidence: tuple[EvidenceSpan, ...] = Field(default_factory=tuple)


class ParsedProviderBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    start: int
    end: int
    text: str
    orders: tuple[ParsedOrderInstruction, ...] = Field(default_factory=tuple)
    evidence: tuple[EvidenceSpan, ...] = Field(default_factory=tuple)


class ParsingEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: str
    evidence: tuple[EvidenceSpan, ...] = Field(default_factory=tuple)


class PlacePendingOrder(ParsingEvent):
    event_type: Literal["PLACE_PENDING_ORDER"] = "PLACE_PENDING_ORDER"
    instrument: str | None = None
    side: TradeSide
    order_type: PendingOrderType
    entry: Decimal
    stop_loss: Decimal
    take_profits: tuple[Decimal, ...]
    provider: str | None = None


class CancelPendingOrder(ParsingEvent):
    event_type: Literal["CANCEL_PENDING_ORDER"] = "CANCEL_PENDING_ORDER"
    side: TradeSide
    order_type: PendingOrderType
    provider: str | None = None


class ReplacePendingOrder(ParsingEvent):
    event_type: Literal["REPLACE_PENDING_ORDER"] = "REPLACE_PENDING_ORDER"
    instrument: str | None = None
    old_side: TradeSide
    old_order_type: PendingOrderType
    new_side: TradeSide
    new_order_type: PendingOrderType
    entry: Decimal
    stop_loss: Decimal
    take_profits: tuple[Decimal, ...]
    provider: str | None = None


class MarkOrderTriggered(ParsingEvent):
    event_type: Literal["MARK_ORDER_TRIGGERED"] = "MARK_ORDER_TRIGGERED"
    side: TradeSide
    order_type: PendingOrderType


class ModifyOrder(ParsingEvent):
    event_type: Literal["MODIFY_ORDER"] = "MODIFY_ORDER"
    target: str
    details: str | None = None


class ClosePosition(ParsingEvent):
    event_type: Literal["CLOSE_POSITION"] = "CLOSE_POSITION"
    target: str


class RecordTradeResult(ParsingEvent):
    event_type: Literal["RECORD_TRADE_RESULT"] = "RECORD_TRADE_RESULT"
    result_kind: ResultKind
    target: str | None = None


class EndSession(ParsingEvent):
    event_type: Literal["END_SESSION"] = "END_SESSION"


class NoTradingDay(ParsingEvent):
    event_type: Literal["NO_TRADING_DAY"] = "NO_TRADING_DAY"
    reason: str | None = None


class RequestManualReview(ParsingEvent):
    event_type: Literal["REQUEST_MANUAL_REVIEW"] = "REQUEST_MANUAL_REVIEW"
    reason: str
    details: str | None = None


ParsedEvent = (
    PlacePendingOrder
    | CancelPendingOrder
    | ReplacePendingOrder
    | MarkOrderTriggered
    | ModifyOrder
    | ClosePosition
    | RecordTradeResult
    | EndSession
    | NoTradingDay
    | RequestManualReview
)


class ParsedMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: MessageIdentity
    classification: MessageClassification
    events: tuple[ParsedEvent, ...] = Field(default_factory=tuple)
    confidence: float
    evidence: tuple[EvidenceSpan, ...] = Field(default_factory=tuple)
    status: ParseStatus
    provider_blocks: tuple[ParsedProviderBlock, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_confidence(self) -> ParsedMessage:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return self


def evidence_from_span(start: int, end: int, text: str, label: str | None = None) -> EvidenceSpan:
    return EvidenceSpan(start=start, end=end, text=text, label=label)


def event_sequence(*events: ParsedEvent) -> tuple[ParsedEvent, ...]:
    return tuple(events)


def provider_block_sequence(*blocks: ParsedProviderBlock) -> tuple[ParsedProviderBlock, ...]:
    return tuple(blocks)


def evidence_sequence(*spans: EvidenceSpan) -> tuple[EvidenceSpan, ...]:
    return tuple(spans)


def take_profits_sequence(values: Sequence[Decimal]) -> tuple[Decimal, ...]:
    return tuple(values)
