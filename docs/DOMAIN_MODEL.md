# Domain model and state machine

## Identity

Use stable identifiers:

- raw message: `channel_id/message_id/version`
- interpreted event: raw-message identity plus event index
- broker intent: interpreted-event identity plus command type
- trading session: configured channel, instrument and trading date/session key
- local order: internal UUID plus optional broker order ID

A content hash detects repeated exports, but Telegram message identity is authoritative for live ingestion.

## Value objects

### PriceInstruction

- provider
- instrument
- order type
- side
- entry
- stop loss
- take profits
- source evidence

### OrderReference

May contain:

- broker order ID
- local order ID
- source message/event ID
- instrument
- side
- order type
- current session

Resolution priority is exact IDs, then unique stateful match. Text-only side/type matching is allowed only when exactly one eligible order exists.

## Message classifications

| Class | Meaning | Broker effects |
| --- | --- | --- |
| TRADE_INSTRUCTION | Place, replace, modify, cancel or close | Possible after validation |
| ORDER_STATUS | Trigger, TP, SL or break-even | State/reconciliation; explicit commands only |
| SESSION_CONTROL | End session or no-trading day | Session state and explicit cancellations |
| RISK_ADVISORY | General seasonal/risk guidance | None automatically |
| PROMOTION | Discount, payout or product marketing | None |
| EDUCATIONAL | General trading explanation | None |
| UNKNOWN | Unsupported or uncertain | Manual review |

## Session states

- `IDLE`
- `ACCEPTING_SIGNALS`
- `NO_TRADING`
- `ENDING`
- `ENDED`
- `PAUSED_BY_OPERATOR`
- `ERROR_RECONCILIATION`

Rules:

- a valid first order may open an `ACCEPTING_SIGNALS` session
- `NoTradingDay` transitions to `NO_TRADING`
- `EndSession` transitions through `ENDING` to `ENDED`
- `ENDED` rejects new placement events unless an explicit new session is opened
- `EndSession` does not implicitly close or cancel anything
- explicit events in the same message execute before `EndSession`
- operator pause blocks all broker writes but ingestion continues

## Order states

- `PROPOSED`
- `VALIDATED`
- `SUBMITTING`
- `PENDING`
- `TRIGGERED`
- `PARTIALLY_FILLED`
- `FILLED`
- `CANCEL_REQUESTED`
- `CANCELLED`
- `CLOSE_REQUESTED`
- `CLOSED_TP`
- `CLOSED_SL`
- `CLOSED_BREAK_EVEN`
- `CLOSED_MANUAL`
- `REJECTED`
- `UNKNOWN_BROKER_STATE`

Terminal states are cancelled, closed variants and rejected.

## Event semantics

### PlacePendingOrder

Requires instrument, side, pending order type, entry, SL, at least one TP or an explicitly supported no-TP policy, quantity policy and provider source.

### CancelPendingOrder

Targets only `PENDING` orders. It must fail safely for filled/open positions. If no unique target exists, emit manual review.

### ReplacePendingOrder

A compound intent:

1. resolve and cancel the old pending order
2. confirm cancellation or reconcile terminal state
3. place the new order

Never place the replacement when cancellation outcome is unknown unless an explicit OCO policy makes the state safe.

### MarkOrderTriggered

Updates/reconciles state. It is not itself permission to create another order.

### ClosePosition

Requires explicit close language and a unique open position. `Delete` is never normalized to this event.

### RecordTradeResult

`TP.`, `SL.` or break-even is resolved to exactly one plausible active position. Otherwise use manual review.

### EndSession

Blocks new orders for the session. It does not mutate existing broker objects unless accompanied by explicit cancellation/closure events.

## Message-to-event examples

`The sell stop order was triggered. Delete the buy stop order. I've placed a new buy stop...`

1. `MarkOrderTriggered(SELL, STOP)`
2. `CancelPendingOrder(BUY, STOP)`
3. `PlacePendingOrder(BUY, STOP, new levels)`

`Delete the buy stop order, we are ending today's session here.`

1. `CancelPendingOrder(BUY, STOP)`
2. `EndSession`

`TP.`

- exactly one active eligible position: `RecordTradeResult(TP, resolved_position)`
- otherwise: `RequestManualReview(AMBIGUOUS_POSITION_REFERENCE)`

## Invariants

- no broker write for promotions, education or unknown messages
- no pending order without SL in the MVP
- no order outside the configured account and environment
- no duplicate broker intent
- no cancellation of a filled position
- no position close caused by the word `delete`
- no implicit action from a generic performance or risk statement
- no score-driven execution in the MVP
- no replacement until the prior cancellation is known
- every state transition has source evidence and timestamp
