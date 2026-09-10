# fictional-engine

Stateful Telegram-to-TradeLocker trading engine.

The first release reads messages from an authorized Telegram user session, classifies and parses Trading Busters signals, maintains session/order state, and executes only against a TradeLocker demo account. Promotional and informational messages never create broker actions.

## Safety defaults

- `EXECUTION_MODE=shadow`
- `ALLOW_LIVE_TRADING=false`
- unresolved or ambiguous messages become `MANUAL_REVIEW`
- every broker command is idempotent and audited
- secrets belong in local `.env` files or GCP Secret Manager, never in Git
- live trading is outside the MVP and requires an explicit reviewed change

## Planned stack

- Python 3.12
- asyncio
- Telethon-compatible Telegram adapter
- Pydantic domain models
- HTTP client behind a TradeLocker adapter interface
- PostgreSQL in GCP; SQLite is allowed for local tests
- pytest, Ruff and mypy
- Docker deployment on GCP

## Documentation

- [Copilot instructions](.github/copilot-instructions.md)
- [Project specification](docs/PROJECT_SPEC.md)
- [Domain model and state machine](docs/DOMAIN_MODEL.md)
- [Milestones](docs/MILESTONES.md)
- [Test plan](docs/TEST_PLAN.md)
- [Representative messages](docs/SAMPLE_MESSAGES.md)
- [GCP deployment](docs/DEPLOYMENT_GCP.md)

## Intended processing flow

```text
Telegram update
  -> immutable raw-message store
  -> message classifier
  -> deterministic parser
  -> state-machine validation
  -> optional quality scorer
  -> risk gate
  -> TradeLocker adapter
  -> execution and reconciliation log
```

The future quality model is advisory-only until walk-forward and shadow-mode evidence supports any policy change.
