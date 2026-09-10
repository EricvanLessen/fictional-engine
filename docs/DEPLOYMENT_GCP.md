# GCP deployment

## Deployment decision

The Telegram listener is a continuously connected, stateful worker. For the MVP, deploy one Docker container on a small Compute Engine VM with supervised restart. This is simpler to reason about than a request-oriented service and keeps the Telegram session lifecycle explicit.

The application itself remains portable. A later move to another always-on GCP compute product must not change domain or application code.

## GCP components

- Compute Engine VM for the worker
- Artifact Registry for container images
- Secret Manager for Telegram and TradeLocker secrets
- Cloud SQL for PostgreSQL
- Cloud Logging and Monitoring
- dedicated service account with least privilege
- optional private networking/Cloud SQL connector according to the chosen setup

## Environments

Maintain separate configurations:

- local replay
- local shadow
- GCP shadow
- GCP demo

Live is not an available deployment environment in the MVP.

## Secrets

Local development may use an ignored `.env` file. GCP must inject secrets from Secret Manager.

Never place these values in Git, image layers, startup-script output or logs:

- Telegram API hash
- phone/login code or 2FA password
- Telegram serialized session
- TradeLocker username/password, API secret or access/refresh token
- database password

Use secret versions and document rotation. A Telegram session is a credential and must be treated like a password.

## Runtime requirements

- Docker container runs as non-root
- read-only root filesystem where practical
- writable locations restricted to explicit temporary/session needs
- UTC process timezone
- graceful SIGTERM handling
- automatic restart with bounded failure backoff
- startup validation of environment, database and execution mode
- ingestion may start only after persistence is healthy
- broker writes require verified `demo` account metadata
- one active executor lease prevents two replicas from trading the same account

## Deployment flow

1. CI runs lint, types, unit and integration tests.
2. Build an immutable image tagged with commit SHA.
3. Push to Artifact Registry.
4. Update the shadow VM deployment.
5. Verify health and Telegram intake.
6. Run replay smoke tests.
7. Promote the same image to demo configuration.
8. Run a minimum-size demo smoke test only with explicit operator approval.
9. Roll back to the previous image on failure.

Do not deploy from an uncommitted local working tree.

## Observability

Every log record should include applicable fields:

- environment
- execution mode
- service version/commit SHA
- channel ID
- Telegram message ID/version
- session ID
- local order ID
- broker order/position ID
- event/command ID
- correlation ID
- outcome and reason code

Redact message content from normal logs. Raw content belongs in controlled persistence.

Alert on:

- listener disconnected beyond threshold
- database unavailable
- outbox backlog
- manual-review backlog
- broker authentication failure
- reconciliation mismatch
- unexpected open order/position
- executor lease conflict
- kill switch activation
- repeated process crash

## Recovery rules

- On restart, acquire the single-executor lease.
- Load unfinished outbox commands.
- Reconcile broker state before retrying uncertain commands.
- Resume Telegram updates from the persisted checkpoint.
- Never recreate an order solely because local state says submission timed out.
- Pause execution automatically when reconciliation cannot establish truth.

## Backup and rollback

- enable automated Cloud SQL backups and point-in-time recovery where available
- test restoration into a non-production database
- retain immutable container tags
- document the last known-good commit and schema revision
- database rollback must not discard broker IDs, raw messages or audit events
