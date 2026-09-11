from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from fictional_engine.domain.parsing import (
    CancelPendingOrder,
    EndSession,
    EvidenceSpan,
    MarkOrderTriggered,
    MessageClassification,
    MessageSource,
    NoTradingDay,
    ParsedEvent,
    ParsedMessage,
    ParsedOrderInstruction,
    ParsedProviderBlock,
    ParseStatus,
    PendingOrderType,
    PlacePendingOrder,
    RequestManualReview,
    TradeSide,
)

ZERO_WIDTH_PATTERN: Final[re.Pattern[str]] = re.compile(r"[\u200b\u200c\u200d\ufeff]")
URL_PATTERN: Final[re.Pattern[str]] = re.compile(r"https?://\S+", re.IGNORECASE)
WHITESPACE_PATTERN: Final[re.Pattern[str]] = re.compile(r"\s+")
PROVIDER_HEADER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?P<provider>cronos\s*markets|cronosmarkets|funding\s*dynasty|fundingdynasty)"
    r"(?:\s+data)?\s*[:.\-]*$",
    re.IGNORECASE,
)
SIDE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?P<side>buy|sell)\s*stop(?:\s*order)?\s*[:.\-]*$",
    re.IGNORECASE,
)
INSTRUMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"instrument\s*:\s*(?P<instrument>[A-Za-z0-9_\-/]+)", re.IGNORECASE
)
ENTRY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"entry\s*:\s*(?P<value>[0-9][0-9, ]*(?:\.[0-9]+)?|[0-9]+)(?![0-9a-z])",
    re.IGNORECASE,
)
SL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"sl\s*:\s*(?P<value>[0-9][0-9, ]*(?:\.[0-9]+)?|[0-9]+)(?![0-9a-z])",
    re.IGNORECASE,
)
TP_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"tp\s*:\s*(?P<value>[0-9][0-9, ]*(?:\.[0-9]+)?|[0-9]+)(?![0-9a-z])",
    re.IGNORECASE,
)
TRIGGER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"the\s+(?P<side>buy|sell)\s*stop\s+order\s+was\s+triggered", re.IGNORECASE
)
DELETE_ORDER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"delete\s+the\s+(?P<side>buy|sell)\s*stop\s+order", re.IGNORECASE
)
SESSION_END_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(we\s+are\s+ending\s+today['\"]s\s+session|end\s+session|session\s+end)",
    re.IGNORECASE,
)
NO_TRADING_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(we\s+will\s+not\s+trade\s+today|bank\s+holiday|no\s+trading\s+day)", re.IGNORECASE
)
RISK_ADVISORY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(reduce\s+your\s+risk|pause\s+this\s+strategy|seasonal\s+risk|summer\s+period|between\s+april\s+16\s+and\s+september\s+30)",
    re.IGNORECASE,
)
PROMOTION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(payout|discount|funded\s*account|masterclass|academy|bonus|profit\s+target\s+has\s+been\s+reduced|discount\s+code|trade\s+challenge)",
    re.IGNORECASE,
)
EDUCATIONAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(educational|how\s+to\s+trade|lesson|training|webinar|workshop|explain|explainer)",
    re.IGNORECASE,
)
TP_ONLY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^tp\.?\s*(?:[🥇\u2764\ufe0f✨]+)?$", re.IGNORECASE
)
SL_ONLY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^sl\.?\s*$", re.IGNORECASE)
BREAK_EVEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"break[- ]?even", re.IGNORECASE)


@dataclass(frozen=True)
class LineSpan:
    start: int
    end: int
    text: str
    normalized: str


@dataclass(frozen=True)
class ParsedBlockData:
    provider: str
    start: int
    end: int
    text: str
    orders: tuple[ParsedOrderInstruction, ...]
    evidence: tuple[EvidenceSpan, ...]


class DeterministicMessageParser:
    def __init__(self, preferred_provider: str | None = None) -> None:
        self._preferred_provider = (
            _normalize_provider_name(preferred_provider) if preferred_provider else None
        )

    def classify(self, source: MessageSource) -> MessageClassification:
        text = source.full_text
        normalized = _normalize_text(text)

        if NO_TRADING_PATTERN.search(normalized):
            return MessageClassification.NO_TRADING_DAY
        if RISK_ADVISORY_PATTERN.search(normalized):
            return MessageClassification.RISK_ADVISORY
        if PROMOTION_PATTERN.search(normalized):
            return MessageClassification.PROMOTION
        if EDUCATIONAL_PATTERN.search(normalized):
            return MessageClassification.EDUCATIONAL
        if SESSION_END_PATTERN.search(normalized):
            return MessageClassification.SESSION_CONTROL
        if (
            TP_ONLY_PATTERN.match(normalized)
            or SL_ONLY_PATTERN.match(normalized)
            or BREAK_EVEN_PATTERN.search(normalized)
        ):
            return MessageClassification.ORDER_STATUS
        if _extract_provider_boundaries(_split_lines_with_spans(text)):
            return MessageClassification.TRADE_INSTRUCTION
        if TRIGGER_PATTERN.search(normalized):
            return MessageClassification.ORDER_STATUS
        if DELETE_ORDER_PATTERN.search(normalized):
            return MessageClassification.TRADE_INSTRUCTION
        return MessageClassification.UNKNOWN

    def parse(self, source: MessageSource) -> ParsedMessage:
        text = source.full_text
        lines = _split_lines_with_spans(text)
        classification = self.classify(source)
        evidence: list[EvidenceSpan] = []
        parsed_provider_blocks: list[ParsedBlockData] = []
        events: list[ParsedEvent] = []

        if classification == MessageClassification.PROMOTION:
            return self._result(
                source, classification, tuple(), evidence, ParseStatus.PROPOSED, tuple()
            )
        if classification == MessageClassification.EDUCATIONAL:
            return self._result(
                source, classification, tuple(), evidence, ParseStatus.PROPOSED, tuple()
            )
        if classification == MessageClassification.RISK_ADVISORY:
            return self._result(
                source, classification, tuple(), evidence, ParseStatus.PROPOSED, tuple()
            )
        if classification == MessageClassification.NO_TRADING_DAY:
            span = _first_match_span(text, NO_TRADING_PATTERN)
            if span is not None:
                evidence.append(span)
            events.append(
                NoTradingDay(
                    reason="bank holiday or no-trading announcement",
                    evidence=tuple(evidence),
                )
            )
            return self._result(
                source, classification, tuple(events), evidence, ParseStatus.PROPOSED, tuple()
            )

        parsed_provider_blocks = _parse_provider_blocks(text, lines)
        if parsed_provider_blocks:
            if not self._provider_blocks_compatible(parsed_provider_blocks):
                return self._manual_review(
                    source,
                    classification,
                    "provider blocks disagree",
                    evidence=tuple(self._collect_provider_evidence(parsed_provider_blocks)),
                    provider_blocks=tuple(self._to_provider_blocks(parsed_provider_blocks)),
                )
            selected_blocks = self._select_provider_blocks(parsed_provider_blocks)
            for block in selected_blocks:
                evidence.extend(block.evidence)
                events.extend(_order_events_from_block(block))

        status_events = _parse_message_status_events(text, lines)
        if status_events.manual_review is not None:
            manual = status_events.manual_review
            return self._manual_review(
                source,
                classification,
                manual.reason,
                details=manual.details,
                evidence=tuple(evidence or manual.evidence),
                provider_blocks=tuple(self._to_provider_blocks(parsed_provider_blocks)),
            )
        events.extend(status_events.events)
        evidence.extend(status_events.evidence)
        events = _sort_events_by_evidence(events)

        if classification == MessageClassification.ORDER_STATUS and not events:
            return self._manual_review(
                source,
                classification,
                "order status lacks resolvable target",
                evidence=tuple(evidence),
                provider_blocks=tuple(self._to_provider_blocks(parsed_provider_blocks)),
            )

        if (
            classification
            in {MessageClassification.TRADE_INSTRUCTION, MessageClassification.SESSION_CONTROL}
            and not events
        ):
            return self._manual_review(
                source,
                classification,
                "instruction is malformed or ambiguous",
                evidence=tuple(evidence),
                provider_blocks=tuple(self._to_provider_blocks(parsed_provider_blocks)),
            )

        if classification == MessageClassification.UNKNOWN and not events:
            return self._manual_review(
                source,
                classification,
                "unsupported or uncertain message",
                evidence=tuple(evidence),
                provider_blocks=tuple(self._to_provider_blocks(parsed_provider_blocks)),
            )

        status = ParseStatus.PROPOSED
        if any(
            isinstance(event, PlacePendingOrder) and event.instrument is None for event in events
        ):
            status = ParseStatus.CONTEXT_REQUIRED

        return self._result(
            source,
            classification,
            tuple(events),
            evidence,
            status,
            tuple(self._to_provider_blocks(parsed_provider_blocks)),
        )

    def _result(
        self,
        source: MessageSource,
        classification: MessageClassification,
        events: tuple[ParsedEvent, ...],
        evidence: Iterable[EvidenceSpan],
        status: ParseStatus,
        provider_blocks: tuple[ParsedProviderBlock, ...],
    ) -> ParsedMessage:
        return ParsedMessage(
            source=source.identity,
            classification=classification,
            events=events,
            confidence=1.0,
            evidence=tuple(evidence),
            status=status,
            provider_blocks=provider_blocks,
        )

    def _manual_review(
        self,
        source: MessageSource,
        classification: MessageClassification,
        reason: str,
        details: str | None = None,
        evidence: Iterable[EvidenceSpan] = (),
        provider_blocks: tuple[ParsedProviderBlock, ...] = (),
    ) -> ParsedMessage:
        return ParsedMessage(
            source=source.identity,
            classification=classification,
            events=(RequestManualReview(reason=reason, details=details, evidence=tuple(evidence)),),
            confidence=0.0,
            evidence=tuple(evidence),
            status=ParseStatus.MANUAL_REVIEW,
            provider_blocks=provider_blocks,
        )

    def _provider_blocks_compatible(self, provider_blocks: list[ParsedBlockData]) -> bool:
        canonical = {_canonical_provider_block(block) for block in provider_blocks}
        return len(canonical) <= 1

    def _select_provider_blocks(
        self, provider_blocks: list[ParsedBlockData]
    ) -> list[ParsedBlockData]:
        if self._preferred_provider is not None:
            selected = [
                block for block in provider_blocks if block.provider == self._preferred_provider
            ]
            if selected:
                return selected
        return [provider_blocks[0]]

    def _collect_provider_evidence(
        self, provider_blocks: list[ParsedBlockData]
    ) -> list[EvidenceSpan]:
        return [span for block in provider_blocks for span in block.evidence]

    def _to_provider_blocks(
        self, provider_blocks: list[ParsedBlockData]
    ) -> list[ParsedProviderBlock]:
        return [
            ParsedProviderBlock(
                provider=block.provider,
                start=block.start,
                end=block.end,
                text=block.text,
                orders=block.orders,
                evidence=block.evidence,
            )
            for block in provider_blocks
        ]


def _parse_message_status_events(text: str, lines: list[LineSpan]) -> _StatusParseResult:
    normalized = _normalize_text(text)
    evidence: list[EvidenceSpan] = []
    events: list[ParsedEvent] = []
    manual_review: _ManualReview | None = None

    trigger_match = TRIGGER_PATTERN.search(normalized)
    if trigger_match is not None:
        span = _span_for_match(text, trigger_match)
        if span is not None:
            evidence.append(span)
            side = TradeSide(trigger_match.group("side").upper())
            events.append(
                MarkOrderTriggered(
                    side=side,
                    order_type=PendingOrderType.STOP,
                    evidence=(span,),
                )
            )

    delete_matches = list(DELETE_ORDER_PATTERN.finditer(normalized))
    if delete_matches:
        for match in delete_matches:
            span = _span_for_match(text, match)
            if span is None:
                continue
            side = TradeSide(match.group("side").upper())
            evidence.append(span)
            events.append(
                CancelPendingOrder(
                    side=side,
                    order_type=PendingOrderType.STOP,
                    evidence=(span,),
                )
            )
    elif "delete" in normalized:
        manual_review = _ManualReview(
            reason="delete instruction is ambiguous",
            details="delete must name a side and order type",
            evidence=tuple(_all_matching_spans(lines, "delete")),
        )

    if SESSION_END_PATTERN.search(normalized):
        matches = list(SESSION_END_PATTERN.finditer(normalized))
        for match in matches:
            span = _span_for_match(text, match)
            if span is not None:
                evidence.append(span)
        events.append(EndSession(evidence=tuple(evidence[-1:]) if evidence else tuple()))

    if not events and TP_ONLY_PATTERN.match(normalized):
        span = EvidenceSpan(start=0, end=len(text), text=text, label="status")
        manual_review = _ManualReview(
            reason="tp instruction requires position context",
            details="M3 reference resolution not yet implemented",
            evidence=(span,),
        )

    if not events and BREAK_EVEN_PATTERN.search(normalized):
        manual_review = _ManualReview(
            reason="break-even instruction requires position context",
            details="M3 reference resolution not yet implemented",
            evidence=tuple(_all_matching_spans(lines, "break")),
        )

    return _StatusParseResult(
        events=tuple(events), evidence=tuple(evidence), manual_review=manual_review
    )


def _parse_provider_blocks(text: str, lines: list[LineSpan]) -> list[ParsedBlockData]:
    block_boundaries = _extract_provider_boundaries(lines)
    if not block_boundaries:
        return []

    instrument = _extract_instrument(text)

    parsed_blocks: list[ParsedBlockData] = []
    for boundary in block_boundaries:
        block_lines = lines[boundary.start_line : boundary.end_line]
        block_text = text[boundary.start : boundary.end]
        orders = _parse_order_sections(instrument, boundary.provider, block_lines)
        evidence = (
            EvidenceSpan(
                start=boundary.start, end=boundary.end, text=block_text, label="provider_block"
            ),
        )
        parsed_blocks.append(
            ParsedBlockData(
                provider=boundary.provider,
                start=boundary.start,
                end=boundary.end,
                text=block_text,
                orders=tuple(orders),
                evidence=evidence,
            )
        )
    return parsed_blocks


def _parse_order_sections(
    instrument: str | None, provider: str, lines: list[LineSpan]
) -> list[ParsedOrderInstruction]:
    orders: list[ParsedOrderInstruction] = []
    current: list[LineSpan] = []
    current_side: TradeSide | None = None
    for line in lines:
        if PROVIDER_HEADER_PATTERN.match(line.normalized):
            continue
        side_match = SIDE_PATTERN.match(line.normalized)
        if side_match is not None:
            if current and current_side is not None:
                parsed = _parse_order_instruction(instrument, provider, current_side, current)
                if parsed is not None:
                    orders.append(parsed)
            current = [line]
            current_side = TradeSide(side_match.group("side").upper())
            continue
        if current:
            current.append(line)
    if current and current_side is not None:
        parsed = _parse_order_instruction(instrument, provider, current_side, current)
        if parsed is not None:
            orders.append(parsed)
    return orders


def _parse_order_instruction(
    instrument: str | None, provider: str, side: TradeSide, lines: list[LineSpan]
) -> ParsedOrderInstruction | None:
    entry = _extract_decimal_line(lines, ENTRY_PATTERN, "entry")
    stop_loss = _extract_decimal_line(lines, SL_PATTERN, "sl")
    take_profits = tuple(_extract_all_decimal_lines(lines, TP_PATTERN, "tp"))
    if entry is None or stop_loss is None or not take_profits:
        return None
    evidence = tuple(
        EvidenceSpan(
            start=line.start, end=line.end, text=line.text, label=_evidence_label(line.normalized)
        )
        for line in lines
    )
    return ParsedOrderInstruction(
        provider=provider,
        instrument=instrument,
        side=side,
        order_type=PendingOrderType.STOP,
        entry=entry,
        stop_loss=stop_loss,
        take_profits=take_profits,
        evidence=evidence,
    )


def _order_events_from_block(block: ParsedBlockData) -> list[ParsedEvent]:
    events: list[ParsedEvent] = []
    for order in block.orders:
        events.append(
            PlacePendingOrder(
                instrument=order.instrument,
                side=order.side,
                order_type=order.order_type,
                entry=order.entry,
                stop_loss=order.stop_loss,
                take_profits=order.take_profits,
                provider=order.provider,
                evidence=order.evidence,
            )
        )
    return events


def _extract_provider_boundaries(lines: list[LineSpan]) -> list[_ProviderBoundary]:
    boundaries: list[_ProviderBoundary] = []
    for index, line in enumerate(lines):
        match = PROVIDER_HEADER_PATTERN.match(line.normalized)
        if match is None:
            continue
        provider = _normalize_provider_name(match.group("provider"))
        end_line = len(lines)
        for next_index in range(index + 1, len(lines)):
            if PROVIDER_HEADER_PATTERN.match(lines[next_index].normalized):
                end_line = next_index
                break
        boundaries.append(
            _ProviderBoundary(
                provider=provider,
                start_line=index,
                end_line=end_line,
                start=line.start,
                end=lines[end_line - 1].end if end_line > 0 else line.end,
            )
        )
    return boundaries


def _extract_instrument(text: str) -> str | None:
    match = INSTRUMENT_PATTERN.search(text)
    if match is None:
        return None
    return match.group("instrument").upper()


def _extract_decimal_line(
    lines: list[LineSpan], pattern: re.Pattern[str], label: str
) -> Decimal | None:
    decimals = _extract_all_decimal_lines(lines, pattern, label)
    return decimals[0] if decimals else None


def _extract_all_decimal_lines(
    lines: list[LineSpan], pattern: re.Pattern[str], label: str
) -> list[Decimal]:
    decimals: list[Decimal] = []
    for line in lines:
        match = pattern.match(line.normalized)
        if match is None:
            continue
        value = _parse_decimal(match.group("value"))
        if value is None:
            return []
        decimals.append(value)
    return decimals


def _parse_decimal(value: str) -> Decimal | None:
    cleaned = value.replace(",", "").replace(" ", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = ZERO_WIDTH_PATTERN.sub("", normalized)
    normalized = URL_PATTERN.sub(" ", normalized)
    normalized = "".join(" " if ch.isspace() else ch for ch in normalized)
    normalized = WHITESPACE_PATTERN.sub(" ", normalized)
    return normalized.strip().lower()


def _normalize_provider_name(raw_provider: str) -> str:
    normalized = _normalize_text(raw_provider)
    if "cronos" in normalized:
        return "Cronos Markets"
    if "funding" in normalized:
        return "Funding Dynasty"
    return raw_provider.strip()


def _split_lines_with_spans(text: str) -> list[LineSpan]:
    lines: list[LineSpan] = []
    start = 0
    for raw_line in text.splitlines(keepends=True):
        stripped = raw_line.rstrip("\r\n")
        end = start + len(stripped)
        lines.append(
            LineSpan(
                start=start,
                end=end,
                text=stripped,
                normalized=_normalize_text(stripped),
            )
        )
        start += len(raw_line)
    if not lines and text:
        lines.append(LineSpan(start=0, end=len(text), text=text, normalized=_normalize_text(text)))
    return lines


def _first_match_span(text: str, pattern: re.Pattern[str]) -> EvidenceSpan | None:
    match = pattern.search(_normalize_text(text))
    if match is None:
        return None
    return EvidenceSpan(start=0, end=len(text), text=text, label="classification")


def _span_for_match(text: str, match: re.Match[str]) -> EvidenceSpan | None:
    start = match.start()
    end = match.end()
    if start < 0 or end < 0:
        return None
    return EvidenceSpan(start=start, end=end, text=text[start:end], label="status")


def _all_matching_spans(lines: list[LineSpan], needle: str) -> tuple[EvidenceSpan, ...]:
    spans: list[EvidenceSpan] = []
    for line in lines:
        if needle in line.normalized:
            spans.append(
                EvidenceSpan(start=line.start, end=line.end, text=line.text, label="status")
            )
    return tuple(spans)


def _evidence_label(normalized_line: str) -> str:
    if normalized_line.startswith("entry"):
        return "entry"
    if normalized_line.startswith("sl"):
        return "stop_loss"
    if normalized_line.startswith("tp"):
        return "take_profit"
    if normalized_line.startswith(("buy stop", "sell stop")):
        return "side"
    return "provider_block"


@dataclass(frozen=True)
class _ProviderBoundary:
    provider: str
    start_line: int
    end_line: int
    start: int
    end: int


@dataclass(frozen=True)
class _ManualReview:
    reason: str
    details: str | None
    evidence: tuple[EvidenceSpan, ...]


@dataclass(frozen=True)
class _StatusParseResult:
    events: tuple[ParsedEvent, ...]
    evidence: tuple[EvidenceSpan, ...]
    manual_review: _ManualReview | None


def _canonical_order(
    order: ParsedOrderInstruction,
) -> tuple[str | None, str, str, Decimal, Decimal, tuple[Decimal, ...]]:
    return (
        order.instrument,
        order.side.value,
        order.order_type.value,
        order.entry,
        order.stop_loss,
        order.take_profits,
    )


def _canonical_provider_block(
    block: ParsedBlockData,
) -> tuple[tuple[str | None, str, str, Decimal, Decimal, tuple[Decimal, ...]], ...]:
    return tuple(_canonical_order(order) for order in block.orders)


def _sort_events_by_evidence(events: list[ParsedEvent]) -> list[ParsedEvent]:
    indexed_events = list(enumerate(events))

    def sort_key(item: tuple[int, ParsedEvent]) -> tuple[int, int]:
        index, event = item
        if event.evidence:
            return (min(span.start for span in event.evidence), index)
        return (10**9, index)

    return [event for _, event in sorted(indexed_events, key=sort_key)]
