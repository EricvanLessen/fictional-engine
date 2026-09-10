# Project specification

## Goal

Create an auditable trading engine that reads Trading Busters messages from an authorized Telegram account, distinguishes actionable instructions from promotions, maintains the lifecycle of a trading session, and manages a configured TradeLocker demo account.

The engine must reproduce explicit instructions. It must not infer a trade from promotional or educational language.

## MVP scope

Included:

- long-running Telegram MTProto listener using the user's authorized membership
- historical replay/import for fixtures and recovery
- immutable raw-message persistence
- classification and deterministic parsing
- stateful order/session correlation
- US30 pending-order workflow
- configured mapping from signal symbols to TradeLocker instruments
- shadow mode
- TradeLocker demo execution
- risk and validation gate
- audit log, reconciliation, alerting and kill switch
- Dockerized GCP deployment
- advisory trade-quality scoring interface with a rule-based baseline

Excluded from the MVP:

- live-capital trading
- automatic strategy discovery
- autonomous changes to risk configuration
- automatic price-offset correction between providers
- OCR unless real signals are image-only
- LLM calls in the execution-critical path
- copying arbitrary Telegram channels
- handling unsupported instruments without explicit configuration

## Actors and boundaries

- Telegram user account: already authorized to view the configured channel.
- Telegram adapter: receives posts, edits and available history.
- Engine: the system of record for interpretation and proposed actions.
- TradeLocker demo account: execution destination.
- Operator: resolves ambiguous messages and controls pause/resume.
- GCP: hosts runtime, secrets, persistence and logs.

The channel provider and broker remain external systems. Telegram visibility does not imply permission to redistribute content; retain only operationally necessary data and respect the provider's terms.

## Functional requirements

### Ingestion

- Authenticate using a Telegram user session, not a Bot API token, unless the channel owner later adds a bot.
- Accept exactly one configured channel ID for the MVP.
- Persist `channel_id`, `message_id`, timestamp, edit timestamp, sender/channel metadata, text, caption, media metadata and content hash.
- Resume after restart without replaying broker actions.
- Treat edits as immutable versions linked to the original message.
- Allow deterministic fixture replay without network access.

### Classification

The classifier must separate actionable messages from:

- promotions and discounts
- MasterClass/event announcements
- educational commentary
- disclaimers
- links and performance claims
- bank-holiday/no-trading messages
- seasonal risk advisories

Classification must be deterministic for known templates. An optional statistical classifier may be added later but may not bypass deterministic safety rules.

### Interpretation

A parsed instruction consists of ordered domain events plus evidence:

```json
{
  "source": {
    "channel_id": 0,
    "message_id": 0,
    "version": 1
  },
  "classification": "TRADE_INSTRUCTION",
  "events": [],
  "confidence": 1.0,
  "evidence": [],
  "status": "PROPOSED"
}
```

The parser must support:

- instrument declaration
- provider-specific blocks
- BUY STOP and SELL STOP
- Entry, SL and TP
- triggered-order announcements
- cancel/delete instructions
- replacement pending orders
- explicit position closure
- TP, SL and break-even result announcements
- session ending
- no-trading day

### Execution

Execution modes:

- `replay`: no network; deterministic historical simulation
- `shadow`: live Telegram intake; no broker writes
- `demo`: broker writes allowed only to verified demo accounts
- `live`: reserved, rejected by the MVP

A proposed command is executable only if all validations pass. Any missing account/instrument metadata, ambiguous reference, stale signal, duplicate event, price mismatch or invalid SL/TP geometry blocks execution.

### State and reconciliation

- Maintain local session, order, position and command state.
- Never assume a broker call succeeded from an HTTP status alone; parse the provider response and reconcile.
- After restart, load unfinished commands and query broker state before retrying.
- Never create a second order when the outcome of the first request is unknown.
- Store provider IDs and client correlation IDs.
- Alert on local/broker divergence.

## Example target sequence

At 12:10:

- create US30 Sell Stop at 52547, SL 52832, TP 52413
- create US30 Buy Stop at 52829, SL 52544, TP 52963

At 12:37:

- mark Sell Stop as triggered
- cancel the original Buy Stop
- create replacement Buy Stop at 52832, SL 52547, TP 52893

At 14:13:

- resolve `TP.` to the single active Sell position and record TP closure

At 14:14:

- cancel the remaining replacement Buy Stop
- end the session
- do not interpret session end as a request to close unspecified positions

## Non-functional requirements

- deterministic replay
- at-least-once ingestion with effectively-once broker intent
- UTC internally; retain original Telegram timezone metadata
- structured logs with correlation IDs
- database migrations
- graceful shutdown
- health/readiness indicators
- retry only idempotent operations
- bounded exponential backoff
- testable time and broker abstractions
- least-privilege GCP identities
- sanitized fixtures and logs

## Acceptance definition for MVP

The supplied September 9 sequence passes end-to-end in replay and demo modes; every promotion creates zero broker commands; duplicates create zero additional broker writes; restart/reconciliation creates no duplicate order; all ambiguous messages enter manual review; and live execution remains impossible through ordinary configuration.
