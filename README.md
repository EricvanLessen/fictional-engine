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

## M0 quickstart

1. Install Python 3.12.
2. Create a local env file from `.env.example`.
3. Install development dependencies:

```bash
python3 -m pip install -e .[dev]
```

4. Run all repository checks with one command:

```bash
make check
```

Optional local guardrails:

```bash
pre-commit install
```

## M1 replay

M1 adds immutable raw Telegram message storage, message versioning, fixture replay, and an Alembic migration for the raw-message table.

Replay the sanitized sample fixtures into a local SQLite database:

```bash
fictional-engine-replay fixtures/replay/september-9-m1.json --database-url sqlite:///./data/replay.sqlite3
```

The replay command applies database migrations, sorts fixture inputs by occurrence timestamp and message order, stores exact duplicates idempotently, and creates linked immutable versions for edited messages.

## M3 state machine

M3 adds persistent session, order, position, processed-event, manual-review, and command-outbox tables. The stateful processing path resolves context-dependent parser events such as replacement orders with missing instruments and `TP.` status messages against the persisted session state.

For the September 9 fixture sequence, the state machine:

- creates the initial pending orders
- marks the triggered sell order as an active position
- cancels only the matching pending buy order
- resolves the replacement buy order from session context
- records `TP.` as a state transition without creating a close-position command
- ends the session without implicitly closing positions or canceling unspecified orders

The transactional outbox persists broker intents atomically with state mutations and processed-event markers so replay and restart do not create duplicate commands.

## M4 Telegram adapter

M4 adds a read-only Telegram MTProto user-session adapter and runtime processing service.

- only the configured Telegram channel is accepted
- new messages and edits are converted into immutable raw-message versions
- only newly inserted versions are parsed and applied to the M3 state machine
- exact duplicates are logged and skipped for parsing/state application
- adapter reconnect uses bounded backoff and resumes catch-up from the last persisted channel position
- structured JSON logs include connection lifecycle, ingestion, parsing, state application, manual-review creation, and outbox creation

M4 does not call TradeLocker and remains shadow-only (`EXECUTION_MODE=shadow`).

## Container

- Build and run with Docker Compose:

```bash
docker compose up --build
```

- The container runs as a non-root user (`app`).
