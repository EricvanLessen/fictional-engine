# Copilot instructions

## Mission

Build a stateful Telegram-to-TradeLocker trading engine. The MVP consumes messages from one explicitly configured Telegram channel through an authorized user session, derives deterministic domain events, and executes them only in TradeLocker demo mode.

Do not add features outside the active milestone. Prefer small pull requests that complete one vertical slice and include tests.

## Mandatory safety constraints

1. Default to `EXECUTION_MODE=shadow` and `ALLOW_LIVE_TRADING=false`.
2. Never add a code path that enables live trading merely because credentials exist.
3. Reject startup when `ALLOW_LIVE_TRADING=true` unless a separately implemented, reviewed production gate exists. That gate is not part of the MVP.
4. Never log Telegram session strings, phone/login codes, 2FA secrets, TradeLocker credentials, access tokens, or complete environment dumps.
5. Never commit `.env`, session files, databases, broker responses containing secrets, or raw account credentials.
6. Only process the configured Telegram channel ID.
7. Store every received message before parsing it.
8. Broker writes must use an idempotency key derived from source message and domain-event identity.
9. Ambiguous, stale, inconsistent, or unsupported instructions must produce `MANUAL_REVIEW`; never guess.
10. A Telegram message saying `delete` cancels a pending order. It must never close a filled position.
11. `SESSION_END` prevents new entries for that signal session. It does not close positions or cancel unspecified orders.
12. Never automatically translate a general risk advisory into a live configuration change.
13. The quality score is advisory only and may not affect execution during the MVP.

## Technology

Use:

- Python 3.12
- `asyncio`
- Pydantic v2 for configuration and domain models
- Telethon behind a Telegram adapter interface
- `httpx` behind a TradeLocker adapter interface
- SQLAlchemy 2 with PostgreSQL; SQLite may be used by unit/integration tests
- Alembic for migrations
- pytest, pytest-asyncio and Hypothesis where invariants benefit
- Ruff and mypy in strict-enough mode
- structured JSON logging
- Docker with a non-root runtime user

Do not couple domain logic to Telethon, HTTP response classes, SQLAlchemy ORM objects, or GCP SDK types.

## Architecture boundaries

Use a package layout equivalent to:

```text
src/fictional_engine/
  config/
  domain/
  application/
  adapters/telegram/
  adapters/tradelocker/
  adapters/persistence/
  scoring/
  observability/
```

Dependency direction:

```text
adapters -> application -> domain
```

The domain package has no network, database, framework or environment dependencies.

## Required processing pipeline

1. Telegram adapter receives a new or edited update.
2. Persist an immutable raw-message version.
3. Deduplicate by `channel_id + message_id + edit_date/content_hash`.
4. Classify the message.
5. Parse supported trade instructions into domain events.
6. Load the relevant session and order state.
7. Validate the transition and resolve references.
8. Produce a proposed broker command or `MANUAL_REVIEW`.
9. In shadow mode, persist the proposal without calling TradeLocker.
10. In demo mode, pass the proposal through the risk gate and TradeLocker adapter.
11. Persist request, response, correlation IDs and resulting state.
12. Reconcile local state with broker state.

## Message classes

Support these classifier outputs:

- `TRADE_INSTRUCTION`
- `ORDER_STATUS`
- `SESSION_CONTROL`
- `RISK_ADVISORY`
- `PROMOTION`
- `EDUCATIONAL`
- `UNKNOWN`

Only the first four may reach the domain-event parser. `RISK_ADVISORY` is recorded but creates no broker command unless an operator-approved configuration change already exists.

## Core events

Implement typed events, including:

- `PlacePendingOrder`
- `CancelPendingOrder`
- `ReplacePendingOrder`
- `MarkOrderTriggered`
- `ModifyOrder`
- `ClosePosition`
- `RecordTradeResult`
- `EndSession`
- `NoTradingDay`
- `RequestManualReview`

One Telegram message may yield multiple ordered events. Example: “Delete the buy stop order, we are ending today’s session here” yields `CancelPendingOrder(BUY, STOP)` followed by `EndSession`.

## Parsing rules

- Normalize Unicode spacing, emoji, repeated separators, capitalization and broker-section formatting.
- Preserve original text and offsets for auditability.
- Extract all provider blocks independently.
- If multiple provider blocks disagree, use the explicitly configured source; otherwise request manual review.
- Do not adapt provider prices to another broker automatically in the MVP.
- Parse decimal values without relying on process locale.
- Phrases such as `TP.` and `SL.` require stateful resolution against current positions.
- When zero or multiple plausible positions exist, request manual review.
- Detect edits and cancellations as new message versions; do not erase previous interpretations.
- Ignore promotions even when they contain currencies, percentages, account sizes or the word `trade`.

## Broker rules

The TradeLocker implementation must satisfy a provider-neutral `BrokerPort`. Keep endpoint paths, authentication refresh and payload translation inside the adapter.

Do not invent undocumented TradeLocker endpoints. Add contract tests from sanitized real demo responses before enabling broker writes.

Required broker operations:

- list accounts
- resolve instrument metadata
- obtain current quote
- list pending orders and open positions
- place pending order with SL and TP
- cancel pending order
- modify pending order or protection when supported
- close position
- retrieve order/position status
- reconcile by client correlation/idempotency key

Before placement validate account, environment, symbol mapping, order direction/type, quantity, SL/TP geometry, price deviation, signal age, session state and exposure limits.

## Pull request requirements

Every implementation PR must include:

- milestone and acceptance criterion addressed
- tests for success, duplicates and failures
- no reductions to safety defaults
- migration when persistence changes
- sanitized sample fixtures
- documentation updates
- exact commands run and their results

Do not combine refactoring with behavior changes unless necessary.
