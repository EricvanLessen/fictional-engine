from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fictional_engine.adapters.persistence.models import (
    CommandOutboxRecord,
    ManualReviewRecordModel,
    NoTradingDayRecord,
    OrderRecord,
    PositionRecord,
    ProcessedEventRecordModel,
    SessionRecord,
)
from fictional_engine.domain.parsing import (
    CancelPendingOrder,
    EndSession,
    MarkOrderTriggered,
    MessageSource,
    NoTradingDay,
    ParsedEvent,
    ParsedMessage,
    PendingOrderType,
    PlacePendingOrder,
    RecordTradeResult,
    RequestManualReview,
    ResultKind,
    TradeSide,
)
from fictional_engine.domain.state import (
    AppliedEventResult,
    BrokerCommandType,
    EventIdentity,
    ManualReviewRecord,
    MessageProcessingResult,
    NoTradingDayAggregate,
    OrderAggregate,
    OrderState,
    OutboxCommand,
    OutboxCommandState,
    PositionAggregate,
    PositionState,
    ProcessedEventOutcome,
    ProcessedEventRecord,
    SessionAggregate,
    SessionState,
    TradingStateSnapshot,
)

_MANUAL_REVIEW_DEPENDENCY = "__manual_review_dependency__"


@dataclass(frozen=True)
class _EventMutation:
    outcome: ProcessedEventOutcome
    manual_review: ManualReviewRecord | None = None
    outbox_commands: tuple[OutboxCommand, ...] = ()


class SqlAlchemyTradingStateRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def apply_parsed_message(
        self, parsed_message: ParsedMessage, source: MessageSource
    ) -> MessageProcessingResult:
        processed_at = source.edit_date or source.message_date
        session_date = processed_at.astimezone(UTC).date()

        with self._session_factory() as session:
            snapshot = self._load_snapshot(session, parsed_message.source.channel_id, session_date)
            applied_events: list[AppliedEventResult] = []

            for event_index, event in enumerate(parsed_message.events):
                event_identity = EventIdentity(
                    channel_id=parsed_message.source.channel_id,
                    message_id=parsed_message.source.message_id,
                    version=parsed_message.source.version,
                    event_index=event_index,
                )
                event_key = event_identity.key
                existing = self._find_processed_event(session, event_key)
                if existing is not None:
                    applied_events.append(
                        AppliedEventResult(
                            event_key=event_key,
                            event_type=existing.event_type,
                            outcome=existing.outcome,
                        )
                    )
                    continue

                event_fingerprint = _event_fingerprint(event)
                prior_version = self._find_prior_processed_event(session, event_identity)
                if prior_version is not None:
                    applied_events.append(
                        self._apply_prior_version_guard(
                            session,
                            parsed_message,
                            processed_at,
                            event_identity,
                            event,
                            event_fingerprint,
                            prior_version,
                        )
                    )
                    continue

                mutation = self._apply_event(
                    snapshot=snapshot,
                    parsed_message=parsed_message,
                    processed_at=processed_at,
                    event_identity=event_identity,
                    event=event,
                    prior_results=applied_events,
                )
                self._persist_mutation(
                    session=session,
                    snapshot=snapshot,
                    parsed_message=parsed_message,
                    processed_at=processed_at,
                    event_identity=event_identity,
                    event=event,
                    event_fingerprint=event_fingerprint,
                    mutation=mutation,
                )
                applied_events.append(
                    AppliedEventResult(
                        event_key=event_key,
                        event_type=event.event_type,
                        outcome=mutation.outcome,
                        outbox_command_ids=tuple(
                            command.id for command in mutation.outbox_commands
                        ),
                        manual_review_id=(
                            mutation.manual_review.id
                            if mutation.manual_review is not None
                            else None
                        ),
                    )
                )

            session.commit()
            return MessageProcessingResult(
                parsed_message=parsed_message,
                applied_events=tuple(applied_events),
                state_snapshot=snapshot,
            )

    def list_sessions(self) -> list[SessionAggregate]:
        with self._session_factory() as session:
            statement = select(SessionRecord).order_by(
                SessionRecord.session_date, SessionRecord.channel_id, SessionRecord.instrument
            )
            return [_to_session(record) for record in session.scalars(statement)]

    def list_orders(self) -> list[OrderAggregate]:
        with self._session_factory() as session:
            statement = select(OrderRecord).order_by(OrderRecord.created_at, OrderRecord.id)
            return [_to_order(record) for record in session.scalars(statement)]

    def list_positions(self) -> list[PositionAggregate]:
        with self._session_factory() as session:
            statement = select(PositionRecord).order_by(
                PositionRecord.created_at, PositionRecord.id
            )
            return [_to_position(record) for record in session.scalars(statement)]

    def list_processed_events(self) -> list[ProcessedEventRecord]:
        with self._session_factory() as session:
            statement = select(ProcessedEventRecordModel).order_by(
                ProcessedEventRecordModel.channel_id,
                ProcessedEventRecordModel.message_id,
                ProcessedEventRecordModel.version,
                ProcessedEventRecordModel.event_index,
            )
            return [_to_processed_event(record) for record in session.scalars(statement)]

    def list_manual_reviews(self) -> list[ManualReviewRecord]:
        with self._session_factory() as session:
            statement = select(ManualReviewRecordModel).order_by(ManualReviewRecordModel.created_at)
            return [_to_manual_review(record) for record in session.scalars(statement)]

    def list_outbox_commands(self) -> list[OutboxCommand]:
        with self._session_factory() as session:
            statement = select(CommandOutboxRecord).order_by(CommandOutboxRecord.created_at)
            return [_to_outbox_command(record) for record in session.scalars(statement)]

    def update_outbox_command_state(
        self,
        command_id: str,
        state: OutboxCommandState,
        *,
        observed_at: datetime | None = None,
    ) -> tuple[str, ...]:
        released_ids: list[str] = []
        timestamp = observed_at or datetime.now(UTC)

        with self._session_factory() as session:
            record = session.get(CommandOutboxRecord, command_id)
            if record is None:
                raise ValueError(f"command not found: {command_id}")

            record.state = state.value

            if (
                record.command_type == BrokerCommandType.CANCEL_PENDING_ORDER.value
                and record.order_id is not None
            ):
                order = session.get(OrderRecord, record.order_id)
                if order is not None:
                    order.state = _order_state_from_cancel_command_state(state).value
                    order.updated_at = timestamp
                    if state == OutboxCommandState.SUCCEEDED:
                        order.cancelled_at = timestamp
                    else:
                        order.cancelled_at = None

            if state == OutboxCommandState.SUCCEEDED:
                blocked_dependents = list(
                    session.scalars(
                        select(CommandOutboxRecord)
                        .where(
                            CommandOutboxRecord.depends_on_command_id == command_id,
                            CommandOutboxRecord.state == OutboxCommandState.BLOCKED.value,
                        )
                        .order_by(CommandOutboxRecord.created_at, CommandOutboxRecord.id)
                    )
                )
                for dependent in blocked_dependents:
                    dependent.state = OutboxCommandState.PENDING.value
                    dependent.released_at = timestamp
                    released_ids.append(dependent.id)

            session.commit()

        return tuple(released_ids)


    def _apply_prior_version_guard(
        self,
        session: Session,
        parsed_message: ParsedMessage,
        processed_at: datetime,
        event_identity: EventIdentity,
        event: ParsedEvent,
        event_fingerprint: str,
        prior_version: ProcessedEventRecord,
    ) -> AppliedEventResult:
        if (
            prior_version.event_type == event.event_type
            and prior_version.event_fingerprint == event_fingerprint
        ):
            mutation = _EventMutation(outcome=ProcessedEventOutcome.NOOP)
        else:
            mutation = _EventMutation(
                outcome=ProcessedEventOutcome.MANUAL_REVIEW,
                manual_review=_build_manual_review(
                    event_identity=event_identity,
                    processed_at=processed_at,
                    reason="edited message changed previously processed event",
                    details="manual reconciliation required before applying edited instructions",
                ),
            )
        self._persist_mutation(
            session=session,
            snapshot=None,
            parsed_message=parsed_message,
            processed_at=processed_at,
            event_identity=event_identity,
            event=event,
            event_fingerprint=event_fingerprint,
            mutation=mutation,
        )
        return AppliedEventResult(
            event_key=event_identity.key,
            event_type=event.event_type,
            outcome=mutation.outcome,
            manual_review_id=(
                mutation.manual_review.id if mutation.manual_review is not None else None
            ),
        )

    def _persist_mutation(
        self,
        session: Session,
        snapshot: TradingStateSnapshot | None,
        parsed_message: ParsedMessage,
        processed_at: datetime,
        event_identity: EventIdentity,
        event: ParsedEvent,
        event_fingerprint: str,
        mutation: _EventMutation,
    ) -> None:
        if snapshot is not None:
            self._upsert_snapshot(session, snapshot)

        session.add(
            ProcessedEventRecordModel(
                id=str(uuid4()),
                event_key=event_identity.key,
                channel_id=event_identity.channel_id,
                message_id=event_identity.message_id,
                version=event_identity.version,
                event_index=event_identity.event_index,
                event_type=event.event_type,
                event_fingerprint=event_fingerprint,
                outcome=mutation.outcome.value,
                created_at=processed_at,
            )
        )

        if mutation.manual_review is not None:
            session.add(
                ManualReviewRecordModel(
                    id=mutation.manual_review.id,
                    event_key=mutation.manual_review.event_key,
                    channel_id=mutation.manual_review.channel_id,
                    message_id=mutation.manual_review.message_id,
                    version=mutation.manual_review.version,
                    event_index=mutation.manual_review.event_index,
                    reason=mutation.manual_review.reason,
                    details=mutation.manual_review.details,
                    created_at=mutation.manual_review.created_at,
                )
            )

        for command in mutation.outbox_commands:
            session.add(
                CommandOutboxRecord(
                    id=command.id,
                    idempotency_key=command.idempotency_key,
                    event_key=command.event_key,
                    command_type=command.command_type.value,
                    payload=command.payload,
                    state=command.state.value,
                    created_at=command.created_at,
                    session_id=command.session_id,
                    order_id=command.order_id,
                    position_id=command.position_id,
                    depends_on_command_id=command.depends_on_command_id,
                    released_at=command.released_at,
                )
            )

        session.flush()

    def _apply_event(
        self,
        snapshot: TradingStateSnapshot,
        parsed_message: ParsedMessage,
        processed_at: datetime,
        event_identity: EventIdentity,
        event: ParsedEvent,
        prior_results: list[AppliedEventResult],
    ) -> _EventMutation:
        if isinstance(event, PlacePendingOrder):
            return self._apply_place_pending_order(
                snapshot, parsed_message, processed_at, event_identity, event, prior_results
            )
        if isinstance(event, CancelPendingOrder):
            return self._apply_cancel_pending_order(
                snapshot, parsed_message, processed_at, event_identity, event
            )
        if isinstance(event, MarkOrderTriggered):
            return self._apply_mark_order_triggered(
                snapshot, parsed_message, processed_at, event_identity, event
            )
        if isinstance(event, RecordTradeResult):
            return self._apply_record_trade_result(
                snapshot, parsed_message, processed_at, event_identity, event
            )
        if isinstance(event, EndSession):
            return self._apply_end_session(snapshot, parsed_message, processed_at, event_identity)
        if isinstance(event, NoTradingDay):
            return self._apply_no_trading_day(
                snapshot, parsed_message, processed_at, event_identity
            )
        if isinstance(event, RequestManualReview):
            return _EventMutation(
                outcome=ProcessedEventOutcome.MANUAL_REVIEW,
                manual_review=_build_manual_review(
                    event_identity=event_identity,
                    processed_at=processed_at,
                    reason=event.reason,
                    details=event.details,
                ),
            )
        return _EventMutation(
            outcome=ProcessedEventOutcome.MANUAL_REVIEW,
            manual_review=_build_manual_review(
                event_identity=event_identity,
                processed_at=processed_at,
                reason="unsupported event for M3",
                details=event.event_type,
            ),
        )

    def _apply_place_pending_order(
        self,
        snapshot: TradingStateSnapshot,
        parsed_message: ParsedMessage,
        processed_at: datetime,
        event_identity: EventIdentity,
        event: PlacePendingOrder,
        prior_results: list[AppliedEventResult],
    ) -> _EventMutation:
        dependency_command_id = self._resolve_replacement_dependency(prior_results)
        if dependency_command_id == _MANUAL_REVIEW_DEPENDENCY:
            return _EventMutation(
                outcome=ProcessedEventOutcome.MANUAL_REVIEW,
                manual_review=_build_manual_review(
                    event_identity=event_identity,
                    processed_at=processed_at,
                    reason="replacement placement blocked until cancellation is resolved",
                    details="pending-order cancellation outcome is unresolved",
                ),
            )

        if snapshot.has_no_trading_day(
            channel_id=parsed_message.source.channel_id,
            session_date=processed_at.astimezone(UTC).date(),
        ):
            return _EventMutation(
                outcome=ProcessedEventOutcome.MANUAL_REVIEW,
                manual_review=_build_manual_review(
                    event_identity=event_identity,
                    processed_at=processed_at,
                    reason="session does not accept new entries",
                    details=SessionState.NO_TRADING.value,
                ),
            )

        session = self._resolve_or_create_session(
            snapshot=snapshot,
            parsed_message=parsed_message,
            processed_at=processed_at,
            instrument=event.instrument,
            event_identity=event_identity,
        )
        if isinstance(session, ManualReviewRecord):
            return _EventMutation(
                outcome=ProcessedEventOutcome.MANUAL_REVIEW,
                manual_review=session,
            )

        if session.state in {SessionState.ENDED, SessionState.NO_TRADING}:
            return _EventMutation(
                outcome=ProcessedEventOutcome.MANUAL_REVIEW,
                manual_review=_build_manual_review(
                    event_identity=event_identity,
                    processed_at=processed_at,
                    reason="session does not accept new entries",
                    details=session.state.value,
                ),
            )

        matching_order = next(
            (
                order
                for order in snapshot.orders
                if order.session_id == session.id
                and order.state == OrderState.PENDING
                and order.instrument == session.instrument
                and order.side == event.side
                and order.order_type == event.order_type
                and order.entry == event.entry
                and order.stop_loss == event.stop_loss
                and order.take_profits == event.take_profits
            ),
            None,
        )
        if matching_order is not None:
            return _EventMutation(outcome=ProcessedEventOutcome.NOOP)

        order = OrderAggregate(
            id=str(uuid4()),
            session_id=session.id,
            instrument=session.instrument,
            side=event.side,
            order_type=event.order_type,
            provider=event.provider,
            entry=event.entry,
            stop_loss=event.stop_loss,
            take_profits=event.take_profits,
            state=OrderState.PENDING,
            source_event_key=event_identity.key,
            created_at=processed_at,
            updated_at=processed_at,
        )
        snapshot.orders.append(order)
        session.updated_at = processed_at

        return _EventMutation(
            outcome=ProcessedEventOutcome.APPLIED,
            outbox_commands=(
                OutboxCommand(
                    id=str(uuid4()),
                    idempotency_key=_command_idempotency_key(
                        event_identity, BrokerCommandType.PLACE_PENDING_ORDER
                    ),
                    event_key=event_identity.key,
                    command_type=BrokerCommandType.PLACE_PENDING_ORDER,
                    payload={
                        "entry": str(event.entry),
                        "instrument": session.instrument,
                        "order_type": event.order_type.value,
                        "provider": event.provider,
                        "side": event.side.value,
                        "stop_loss": str(event.stop_loss),
                        "take_profits": [str(value) for value in event.take_profits],
                    },
                    state=(
                        OutboxCommandState.BLOCKED
                        if dependency_command_id is not None
                        else OutboxCommandState.PENDING
                    ),
                    created_at=processed_at,
                    session_id=session.id,
                    order_id=order.id,
                    depends_on_command_id=dependency_command_id,
                ),
            ),
        )

    def _apply_cancel_pending_order(
        self,
        snapshot: TradingStateSnapshot,
        parsed_message: ParsedMessage,
        processed_at: datetime,
        event_identity: EventIdentity,
        event: CancelPendingOrder,
    ) -> _EventMutation:
        pending_matches = [
            order
            for order in snapshot.pending_orders()
            if order.side == event.side and order.order_type == event.order_type
        ]
        if len(pending_matches) != 1:
            return _EventMutation(
                outcome=ProcessedEventOutcome.MANUAL_REVIEW,
                manual_review=_build_manual_review(
                    event_identity=event_identity,
                    processed_at=processed_at,
                    reason="cancel instruction lacks a unique pending order target",
                    details=_candidate_detail(pending_matches),
                ),
            )

        target = pending_matches[0]
        target.state = OrderState.CANCEL_REQUESTED
        target.updated_at = processed_at
        return _EventMutation(
            outcome=ProcessedEventOutcome.APPLIED,
            outbox_commands=(
                OutboxCommand(
                    id=str(uuid4()),
                    idempotency_key=_command_idempotency_key(
                        event_identity, BrokerCommandType.CANCEL_PENDING_ORDER
                    ),
                    event_key=event_identity.key,
                    command_type=BrokerCommandType.CANCEL_PENDING_ORDER,
                    payload={
                        "instrument": target.instrument,
                        "order_id": target.id,
                        "order_type": target.order_type.value,
                        "side": target.side.value,
                    },
                    state=OutboxCommandState.PENDING,
                    created_at=processed_at,
                    session_id=target.session_id,
                    order_id=target.id,
                ),
            ),
        )

    def _apply_mark_order_triggered(
        self,
        snapshot: TradingStateSnapshot,
        parsed_message: ParsedMessage,
        processed_at: datetime,
        event_identity: EventIdentity,
        event: MarkOrderTriggered,
    ) -> _EventMutation:
        pending_matches = [
            order
            for order in snapshot.pending_orders()
            if order.side == event.side and order.order_type == event.order_type
        ]
        if len(pending_matches) != 1:
            return _EventMutation(
                outcome=ProcessedEventOutcome.MANUAL_REVIEW,
                manual_review=_build_manual_review(
                    event_identity=event_identity,
                    processed_at=processed_at,
                    reason="trigger instruction lacks a unique pending order target",
                    details=_candidate_detail(pending_matches),
                ),
            )

        order = pending_matches[0]
        order.state = OrderState.FILLED
        order.filled_at = processed_at
        order.updated_at = processed_at

        existing_position = next(
            (position for position in snapshot.positions if position.source_order_id == order.id),
            None,
        )
        if existing_position is None:
            snapshot.positions.append(
                PositionAggregate(
                    id=str(uuid4()),
                    session_id=order.session_id,
                    source_order_id=order.id,
                    instrument=order.instrument,
                    side=order.side,
                    entry=order.entry,
                    stop_loss=order.stop_loss,
                    take_profits=order.take_profits,
                    state=PositionState.ACTIVE,
                    source_event_key=event_identity.key,
                    created_at=processed_at,
                    updated_at=processed_at,
                )
            )

        return _EventMutation(outcome=ProcessedEventOutcome.APPLIED)

    def _apply_record_trade_result(
        self,
        snapshot: TradingStateSnapshot,
        parsed_message: ParsedMessage,
        processed_at: datetime,
        event_identity: EventIdentity,
        event: RecordTradeResult,
    ) -> _EventMutation:
        active_positions = snapshot.active_positions()
        if len(active_positions) != 1:
            return _EventMutation(
                outcome=ProcessedEventOutcome.MANUAL_REVIEW,
                manual_review=_build_manual_review(
                    event_identity=event_identity,
                    processed_at=processed_at,
                    reason="trade result lacks a unique active position target",
                    details=_candidate_detail(active_positions),
                ),
            )

        position = active_positions[0]
        position.state = _position_state_from_result_kind(event.result_kind)
        position.closed_at = processed_at
        position.updated_at = processed_at
        return _EventMutation(outcome=ProcessedEventOutcome.APPLIED)

    def _apply_end_session(
        self,
        snapshot: TradingStateSnapshot,
        parsed_message: ParsedMessage,
        processed_at: datetime,
        event_identity: EventIdentity,
    ) -> _EventMutation:
        candidates = [
            session
            for session in snapshot.sessions
            if session.state in {SessionState.ACCEPTING_SIGNALS, SessionState.ENDING}
        ]
        if len(candidates) != 1:
            return _EventMutation(
                outcome=ProcessedEventOutcome.MANUAL_REVIEW,
                manual_review=_build_manual_review(
                    event_identity=event_identity,
                    processed_at=processed_at,
                    reason="session end lacks a unique active session target",
                    details=_candidate_detail(candidates),
                ),
            )

        session = candidates[0]
        session.state = SessionState.ENDED
        session.ended_at = processed_at
        session.updated_at = processed_at
        return _EventMutation(outcome=ProcessedEventOutcome.APPLIED)

    def _apply_no_trading_day(
        self,
        snapshot: TradingStateSnapshot,
        parsed_message: ParsedMessage,
        processed_at: datetime,
        event_identity: EventIdentity,
    ) -> _EventMutation:
        session_date = processed_at.astimezone(UTC).date()
        if snapshot.has_no_trading_day(
            channel_id=parsed_message.source.channel_id,
            session_date=session_date,
        ):
            return _EventMutation(outcome=ProcessedEventOutcome.NOOP)

        snapshot.no_trading_days.append(
            NoTradingDayAggregate(
                id=str(uuid4()),
                channel_id=parsed_message.source.channel_id,
                session_date=session_date,
                source_event_key=event_identity.key,
                created_at=processed_at,
            )
        )
        for session in snapshot.sessions:
            if session.state in {SessionState.ACCEPTING_SIGNALS, SessionState.ENDING}:
                session.state = SessionState.NO_TRADING
                session.updated_at = processed_at

        return _EventMutation(outcome=ProcessedEventOutcome.APPLIED)

    @staticmethod
    def _resolve_replacement_dependency(
        prior_results: Sequence[AppliedEventResult],
    ) -> str | None:
        cancel_result = next(
            (
                result
                for result in reversed(prior_results)
                if result.event_type == BrokerCommandType.CANCEL_PENDING_ORDER.value
            ),
            None,
        )
        if cancel_result is None:
            return None
        if cancel_result.outcome != ProcessedEventOutcome.APPLIED:
            return _MANUAL_REVIEW_DEPENDENCY
        if len(cancel_result.outbox_command_ids) != 1:
            return _MANUAL_REVIEW_DEPENDENCY
        return cancel_result.outbox_command_ids[0]

    def _resolve_or_create_session(
        self,
        snapshot: TradingStateSnapshot,
        parsed_message: ParsedMessage,
        processed_at: datetime,
        instrument: str | None,
        event_identity: EventIdentity,
    ) -> SessionAggregate | ManualReviewRecord:
        if instrument is not None:
            existing = next(
                (session for session in snapshot.sessions if session.instrument == instrument),
                None,
            )
            if existing is not None:
                return existing
            session = SessionAggregate(
                id=str(uuid4()),
                channel_id=parsed_message.source.channel_id,
                session_date=processed_at.astimezone(UTC).date(),
                instrument=instrument,
                state=SessionState.ACCEPTING_SIGNALS,
                created_at=processed_at,
                updated_at=processed_at,
            )
            snapshot.sessions.append(session)
            return session

        candidates = [
            session
            for session in snapshot.sessions
            if session.state
            in {SessionState.ACCEPTING_SIGNALS, SessionState.ENDING, SessionState.ENDED}
        ]
        unique_instruments = {session.instrument for session in candidates}
        if len(unique_instruments) == 1 and candidates:
            return candidates[0]
        return _build_manual_review(
            event_identity=event_identity,
            processed_at=processed_at,
            reason="missing instrument cannot be resolved from session context",
            details=_candidate_detail(candidates),
        )

    def _load_snapshot(
        self, session: Session, channel_id: int, session_date: date
    ) -> TradingStateSnapshot:
        no_trading_day_records = list(
            session.scalars(
                select(NoTradingDayRecord).where(
                    NoTradingDayRecord.channel_id == channel_id,
                    NoTradingDayRecord.session_date == session_date,
                )
            )
        )
        session_records = list(
            session.scalars(
                select(SessionRecord)
                .where(
                    SessionRecord.channel_id == channel_id,
                    SessionRecord.session_date == session_date,
                )
                .order_by(SessionRecord.created_at, SessionRecord.id)
            )
        )
        session_ids = [record.id for record in session_records]
        order_records = (
            list(
                session.scalars(
                    select(OrderRecord)
                    .where(OrderRecord.session_id.in_(session_ids))
                    .order_by(OrderRecord.created_at, OrderRecord.id)
                )
            )
            if session_ids
            else []
        )
        position_records = (
            list(
                session.scalars(
                    select(PositionRecord)
                    .where(PositionRecord.session_id.in_(session_ids))
                    .order_by(PositionRecord.created_at, PositionRecord.id)
                )
            )
            if session_ids
            else []
        )
        return TradingStateSnapshot(
            sessions=[_to_session(record) for record in session_records],
            no_trading_days=[
                _to_no_trading_day(record) for record in no_trading_day_records
            ],
            orders=[_to_order(record) for record in order_records],
            positions=[_to_position(record) for record in position_records],
        )

    def _upsert_snapshot(self, session: Session, snapshot: TradingStateSnapshot) -> None:
        for session_item in snapshot.sessions:
            existing_session = session.get(SessionRecord, session_item.id)
            if existing_session is None:
                session.add(
                    SessionRecord(
                        id=session_item.id,
                        channel_id=session_item.channel_id,
                        session_date=session_item.session_date,
                        instrument=session_item.instrument,
                        state=session_item.state.value,
                        created_at=session_item.created_at,
                        updated_at=session_item.updated_at,
                        ended_at=session_item.ended_at,
                    )
                )
            else:
                existing_session.state = session_item.state.value
                existing_session.updated_at = session_item.updated_at
                existing_session.ended_at = session_item.ended_at

        for no_trading_day_item in snapshot.no_trading_days:
            existing_no_trading_day = session.get(NoTradingDayRecord, no_trading_day_item.id)
            if existing_no_trading_day is None:
                session.add(
                    NoTradingDayRecord(
                        id=no_trading_day_item.id,
                        channel_id=no_trading_day_item.channel_id,
                        session_date=no_trading_day_item.session_date,
                        source_event_key=no_trading_day_item.source_event_key,
                        created_at=no_trading_day_item.created_at,
                    )
                )

        for order_item in snapshot.orders:
            existing_order = session.get(OrderRecord, order_item.id)
            payload = {
                "cancelled_at": order_item.cancelled_at,
                "created_at": order_item.created_at,
                "entry": order_item.entry,
                "filled_at": order_item.filled_at,
                "id": order_item.id,
                "instrument": order_item.instrument,
                "order_type": order_item.order_type.value,
                "provider": order_item.provider,
                "session_id": order_item.session_id,
                "side": order_item.side.value,
                "source_event_key": order_item.source_event_key,
                "state": order_item.state.value,
                "stop_loss": order_item.stop_loss,
                "take_profits": [str(value) for value in order_item.take_profits],
                "updated_at": order_item.updated_at,
            }
            if existing_order is None:
                session.add(OrderRecord(**payload))
            else:
                existing_order.provider = order_item.provider
                existing_order.state = order_item.state.value
                existing_order.updated_at = order_item.updated_at
                existing_order.filled_at = order_item.filled_at
                existing_order.cancelled_at = order_item.cancelled_at

        for position_item in snapshot.positions:
            existing_position = session.get(PositionRecord, position_item.id)
            payload = {
                "closed_at": position_item.closed_at,
                "created_at": position_item.created_at,
                "entry": position_item.entry,
                "id": position_item.id,
                "instrument": position_item.instrument,
                "session_id": position_item.session_id,
                "side": position_item.side.value,
                "source_event_key": position_item.source_event_key,
                "source_order_id": position_item.source_order_id,
                "state": position_item.state.value,
                "stop_loss": position_item.stop_loss,
                "take_profits": [str(value) for value in position_item.take_profits],
                "updated_at": position_item.updated_at,
            }
            if existing_position is None:
                session.add(PositionRecord(**payload))
            else:
                existing_position.state = position_item.state.value
                existing_position.updated_at = position_item.updated_at
                existing_position.closed_at = position_item.closed_at

    @staticmethod
    def _find_processed_event(session: Session, event_key: str) -> ProcessedEventRecord | None:
        record = session.scalar(
            select(ProcessedEventRecordModel).where(
                ProcessedEventRecordModel.event_key == event_key
            )
        )
        return None if record is None else _to_processed_event(record)

    @staticmethod
    def _find_prior_processed_event(
        session: Session, event_identity: EventIdentity
    ) -> ProcessedEventRecord | None:
        record = session.scalar(
            select(ProcessedEventRecordModel)
            .where(
                ProcessedEventRecordModel.channel_id == event_identity.channel_id,
                ProcessedEventRecordModel.message_id == event_identity.message_id,
                ProcessedEventRecordModel.event_index == event_identity.event_index,
                ProcessedEventRecordModel.version < event_identity.version,
            )
            .order_by(ProcessedEventRecordModel.version.desc())
            .limit(1)
        )
        return None if record is None else _to_processed_event(record)


def _event_fingerprint(event: ParsedEvent) -> str:
    payload = _normalize_json_value(event.model_dump(exclude={"evidence"}))
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _normalize_json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    return value


def _build_manual_review(
    event_identity: EventIdentity,
    processed_at: datetime,
    reason: str,
    details: str | None,
) -> ManualReviewRecord:
    return ManualReviewRecord(
        id=str(uuid4()),
        event_key=event_identity.key,
        channel_id=event_identity.channel_id,
        message_id=event_identity.message_id,
        version=event_identity.version,
        event_index=event_identity.event_index,
        reason=reason,
        details=details,
        created_at=processed_at,
    )


def _command_idempotency_key(event_identity: EventIdentity, command_type: BrokerCommandType) -> str:
    return f"{event_identity.key}:{command_type.value}"


def _candidate_detail(items: Sequence[object]) -> str:
    return f"matching candidates={len(items)}"


def _position_state_from_result_kind(result_kind: ResultKind) -> PositionState:
    if result_kind == ResultKind.TP:
        return PositionState.CLOSED_TP
    if result_kind == ResultKind.SL:
        return PositionState.CLOSED_SL
    return PositionState.CLOSED_BREAK_EVEN


def _order_state_from_cancel_command_state(state: OutboxCommandState) -> OrderState:
    if state == OutboxCommandState.SUCCEEDED:
        return OrderState.CANCELLED
    if state == OutboxCommandState.UNKNOWN:
        return OrderState.CANCEL_UNKNOWN
    if state == OutboxCommandState.FAILED:
        return OrderState.CANCEL_FAILED
    return OrderState.CANCEL_REQUESTED


def _to_session(record: SessionRecord) -> SessionAggregate:
    return SessionAggregate(
        id=record.id,
        channel_id=record.channel_id,
        session_date=record.session_date,
        instrument=record.instrument,
        state=SessionState(record.state),
        created_at=record.created_at,
        updated_at=record.updated_at,
        ended_at=record.ended_at,
    )


def _to_no_trading_day(record: NoTradingDayRecord) -> NoTradingDayAggregate:
    return NoTradingDayAggregate(
        id=record.id,
        channel_id=record.channel_id,
        session_date=record.session_date,
        source_event_key=record.source_event_key,
        created_at=record.created_at,
    )


def _to_order(record: OrderRecord) -> OrderAggregate:
    return OrderAggregate(
        id=record.id,
        session_id=record.session_id,
        instrument=record.instrument,
        side=TradeSide(record.side),
        order_type=PendingOrderType(record.order_type),
        provider=record.provider,
        entry=Decimal(str(record.entry)),
        stop_loss=Decimal(str(record.stop_loss)),
        take_profits=tuple(Decimal(value) for value in record.take_profits),
        state=OrderState(record.state),
        source_event_key=record.source_event_key,
        created_at=record.created_at,
        updated_at=record.updated_at,
        filled_at=record.filled_at,
        cancelled_at=record.cancelled_at,
    )


def _to_position(record: PositionRecord) -> PositionAggregate:
    return PositionAggregate(
        id=record.id,
        session_id=record.session_id,
        source_order_id=record.source_order_id,
        instrument=record.instrument,
        side=TradeSide(record.side),
        entry=Decimal(str(record.entry)),
        stop_loss=Decimal(str(record.stop_loss)),
        take_profits=tuple(Decimal(value) for value in record.take_profits),
        state=PositionState(record.state),
        source_event_key=record.source_event_key,
        created_at=record.created_at,
        updated_at=record.updated_at,
        closed_at=record.closed_at,
    )


def _to_processed_event(record: ProcessedEventRecordModel) -> ProcessedEventRecord:
    return ProcessedEventRecord(
        event_key=record.event_key,
        channel_id=record.channel_id,
        message_id=record.message_id,
        version=record.version,
        event_index=record.event_index,
        event_type=record.event_type,
        event_fingerprint=record.event_fingerprint,
        outcome=ProcessedEventOutcome(record.outcome),
        created_at=record.created_at,
    )


def _to_manual_review(record: ManualReviewRecordModel) -> ManualReviewRecord:
    return ManualReviewRecord(
        id=record.id,
        event_key=record.event_key,
        channel_id=record.channel_id,
        message_id=record.message_id,
        version=record.version,
        event_index=record.event_index,
        reason=record.reason,
        details=record.details,
        created_at=record.created_at,
    )


def _to_outbox_command(record: CommandOutboxRecord) -> OutboxCommand:
    return OutboxCommand(
        id=record.id,
        idempotency_key=record.idempotency_key,
        event_key=record.event_key,
        command_type=BrokerCommandType(record.command_type),
        payload=record.payload,
        state=OutboxCommandState(record.state),
        created_at=record.created_at,
        session_id=record.session_id,
        order_id=record.order_id,
        position_id=record.position_id,
        depends_on_command_id=record.depends_on_command_id,
        released_at=record.released_at,
    )
