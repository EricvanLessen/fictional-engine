# Milestones

Complete milestones in order. Each milestone must finish with a small pull request, green CI and the acceptance evidence listed below.

## M0 — Repository foundation

Deliver:

- Python 3.12 project metadata
- package structure from Copilot instructions
- configuration model
- Ruff, mypy and pytest configuration
- pre-commit hooks
- Dockerfile and local compose file
- CI for lint, type check and tests
- secret scanning or equivalent checks

Acceptance:

- one documented command runs all checks
- container runs as non-root
- missing required configuration fails without printing secrets
- `.env` and Telegram sessions are ignored

## M1 — Immutable ingestion and replay

Deliver:

- raw-message model and repository
- JSON fixture loader
- Telegram identity/version deduplication
- deterministic replay CLI
- content hashing and original metadata preservation

Acceptance:

- supplied fixtures replay in timestamp/message order
- exact duplicates do not create second versions
- edits create linked immutable versions
- replay performs no network or broker calls

## M2 — Classification and deterministic parsing

Deliver:

- message classifier
- provider-block parser
- typed parsing results with evidence
- normalization for emoji, Unicode spaces and inconsistent capitalization
- promotional/educational filters

Acceptance:

- September 9 initial-order message yields two pending orders
- promotional examples yield zero broker events
- bank-holiday message yields `NoTradingDay`
- seasonal advisory yields no broker command
- malformed instructions go to manual review

## M3 — Session and order state machine

Deliver:

- session/order aggregates
- event application and transition validation
- stateful reference resolution
- command outbox
- restart-safe persistence

Acceptance:

- complete September 9 sequence reaches the expected final state
- `TP.` resolves only with one eligible position
- delete never closes a position
- session end never performs unspecified actions
- duplicate replay creates no additional commands

## M4 — Telegram user-session adapter

Deliver:

- authorized MTProto login/session bootstrap
- configured-channel filter
- new-message and edited-message listeners
- bounded historical catch-up
- reconnect and flood-wait handling
- secure session loading

Acceptance:

- shadow environment receives posts only from the configured channel
- reconnect resumes without duplicate domain events
- credentials and session data never appear in logs
- edited messages create new immutable versions
- account/API terms and rate limits are respected

## M5 — TradeLocker demo adapter

Deliver:

- `BrokerPort` implementation
- authentication and refresh handling based on verified documentation/account flow
- account and instrument discovery
- pending order placement/cancellation
- position close and protection modification where supported
- sanitized contract fixtures
- reconciliation

Acceptance:

- startup proves the selected account is demo
- US30 mapping is explicit and verified
- dry contract tests match sanitized real responses
- unknown response outcome blocks retries until reconciliation
- all broker calls carry correlation data where supported

## M6 — Risk gate, shadow and demo execution

Deliver:

- fixed minimal demo quantity mode
- optional risk-percent sizing after instrument metadata is verified
- signal-age, price-deviation and exposure limits
- operator pause and kill switch
- shadow proposal comparison
- demo command execution

Acceptance:

- invalid SL/TP geometry is rejected
- stale or price-divergent signals are blocked
- repeated messages do not create repeated orders
- kill switch blocks writes while ingestion continues
- the supplied sequence executes correctly on a demo account

## M7 — GCP deployment and observability

Deliver:

- container build/publish workflow
- always-on GCP runtime
- Secret Manager integration
- managed PostgreSQL connection
- structured Cloud Logging
- health checks, alerts and runbook
- backup/restore procedure

Acceptance:

- restart does not duplicate an order
- loss of Telegram, database or broker connectivity fails safe
- secrets are not stored in image, repository or plain deployment files
- alerts cover manual review, reconciliation mismatch and listener outage
- rollback is documented and tested

## M8 — Trade Quality Intelligence

Deliver:

- immutable feature snapshot at signal time
- outcome labels and model-version registry
- rule-based baseline score
- offline training pipeline
- probability calibration and walk-forward evaluation
- shadow-only scored output

Initial output:

```json
{
  "quality_score": 0,
  "probability_tp_before_sl": 0.0,
  "expected_value_r": 0.0,
  "model_version": "rules-v1",
  "reason_codes": []
}
```

Acceptance:

- the score cannot alter execution
- features use information available at signal time only
- outcome labels include P&L in R, slippage, MFE and MAE
- evaluation reports Brier score, calibration and walk-forward results
- model version and feature snapshot are reproducible for every score

## Post-MVP gate

Live trading requires a separately approved milestone. It must include legal/provider review, explicit production credentials, tighter operational limits, independent reconciliation, canary sizing and a deliberate code change. Environment variables alone may not unlock it.
