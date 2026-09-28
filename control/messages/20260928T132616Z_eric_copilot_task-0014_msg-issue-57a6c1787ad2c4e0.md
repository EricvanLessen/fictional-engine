---
schema_version: control-message.v1
message_id: msg-issue-57a6c1787ad2c4e0
task_id: task-0014
from: eric
to: copilot
type: TASK_INSTRUCTION
status: PENDING
branch: feat/telegram-live-ingestion
created_at: '2026-09-28T13:26:16Z'
in_reply_to: null
attempt: 1
expected_head_sha: null
pull_request_url: null
commit_sha: null
---

# M4a: Add bounded Telethon ingestion adapter

## Goal

Implement the first bounded slice of M4: receive new and edited messages from exactly one configured Telegram channel and feed them into the existing immutable ingestion/stateful-processing boundary.

Use branch `feat/telegram-live-ingestion`.

## Scope

- add a compatible pinned Telethon dependency
- introduce a Telegram client/protocol boundary that can be tested without network access
- map new-message and edited-message updates into the existing `RawTelegramMessage` model while preserving source metadata
- reject updates from every channel except `EngineSettings.telegram_channel_id`
- pass accepted updates to `StatefulMessageProcessingService.process_raw_message`
- avoid logging Telegram credentials, session material, or full private payloads
- keep replay behavior unchanged
- add deterministic unit tests for:
  - accepted new message
  - accepted edited message creating a new immutable version
  - wrong-channel update ignored
  - duplicate update remaining idempotent
  - text, caption, and media-metadata mapping
- update the M4 documentation/runbook for this slice

## Explicitly out of scope

- real Telegram login during CI
- historical catch-up
- reconnect/flood-wait policy
- wiring the long-running listener into `main.py`
- TradeLocker calls
- risk sizing or demo execution
- partial close, multiple TP levels, or new signal grammar

## Acceptance

- all existing tests remain green
- new tests use a fake/mock Telegram client; no network calls
- Ruff and mypy pass
- implementation opens one focused draft pull request
- stop after this slice; do not begin the next M4 task automatically
