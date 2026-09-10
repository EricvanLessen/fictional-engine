# Test plan

## Test layers

### Unit tests

Test pure domain and parser behavior without network or database:

- Unicode and emoji normalization
- decimal parsing
- classification
- provider-section extraction
- message-to-event ordering
- state transitions
- order-reference resolution
- risk calculations
- idempotency-key construction

### Persistence integration tests

Use an isolated database:

- immutable message versions
- unique constraints
- command outbox transactionality
- restart recovery
- concurrent duplicate ingestion
- migration upgrade/downgrade where safe

### Adapter contract tests

Use sanitized recorded responses and explicit schemas:

- Telegram new and edited update mapping
- TradeLocker authentication mapping
- instrument/account mapping
- order request/response mapping
- cancellation and close semantics
- rate limits and transient errors
- ambiguous/unknown broker outcome

Do not call a real broker in ordinary CI.

### Demo end-to-end tests

Run only with an explicit CI/manual environment:

- verify demo account before writes
- place minimum-size pending orders far enough from market to remain pending
- cancel them
- reconcile zero unintended positions/orders
- record broker IDs and sanitized evidence

## Canonical sequence test: September 9

### Input A — initial orders

Expected events, in order:

1. place US30 Sell Stop: entry 52547, SL 52832, TP 52413
2. place US30 Buy Stop: entry 52829, SL 52544, TP 52963

Expected state: two pending orders in an accepting session.

### Input B — trigger, cancellation and replacement

Expected events:

1. mark Sell Stop triggered
2. cancel original Buy Stop
3. place replacement Buy Stop: entry 52832, SL 52547, TP 52893

Expected state: one active Sell position and one replacement Buy Stop pending.

### Input C — `TP.`

Expected event: record TP for the unique active Sell position.

Expected state: Sell position `CLOSED_TP`; replacement Buy Stop remains pending.

### Input D — cancel and session end

Expected events:

1. cancel replacement Buy Stop
2. end session

Expected final state: no pending orders, no active positions, session `ENDED`.

Expected broker-write count across the logical sequence:

- three placements
- two cancellations
- no close-position command caused by TP/status text
- no additional writes from duplicate ingestion

The exact TradeLocker API-call count may include safe reads/reconciliation and is asserted separately.

## Negative sequence tests

### Duplicate export

Replay every message twice.

Expected: identical final state and no additional broker intents.

### Promotion containing trade vocabulary

Input includes payouts, account sizes, profit targets, discounts and links.

Expected: `PROMOTION`, zero events, zero broker commands.

### No-trading day

Input: `We will not trade today.`

Expected: `NoTradingDay`; session does not accept placements for that date.

### Seasonal advisory

Input says risk should be reduced during summer.

Expected: `RISK_ADVISORY`; zero broker commands and no automatic configuration mutation.

### Ambiguous TP

Input: `TP.` while two positions are active.

Expected: manual review, zero state closure until reconciled.

### Ambiguous delete

Input: `Delete the order` while two pending orders match.

Expected: manual review and zero cancellation.

### Delete versus close

Input: `Delete the buy stop order` while a Buy position is filled and no Buy Stop is pending.

Expected: no close call; manual review or explicit no-match result.

### Session end with active position

Input: `We are ending today's session here` with an active position.

Expected: session ends for new entries; active position is unchanged.

### Replacement cancellation uncertainty

Broker times out while canceling the old pending order.

Expected: replacement is not placed; reconciliation required.

### Stale signal

Message age exceeds configured maximum when received.

Expected: stored and parsed, but execution blocked.

### Wrong channel

Valid-looking order from any other channel ID.

Expected: ignored before parsing and audited as rejected source.

## Property/invariant tests

Generate event sequences and assert:

- a terminal order never returns to pending
- duplicate event application is idempotent
- cancellation never closes a filled position
- session end creates no implicit broker action
- promotions create no trading events
- every executable order has account, instrument, quantity, entry, SL and validated geometry
- score values cannot influence command eligibility in MVP

## Operational tests

- SIGTERM drains processing and persists checkpoints
- process restart replays outbox safely
- database unavailable means no broker write
- broker unavailable preserves proposed command without blind retry
- Telegram reconnect catches up without duplication
- kill switch blocks all writes immediately
- logs remain useful with secrets redacted
