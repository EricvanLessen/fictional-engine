# Development Automation Decisions

## 2026-09-15

- Keep Increment A in a separate top-level Python package named `development_automation` to avoid coupling with trading runtime code.
- Treat `control/CURRENT_STATE.md` as a projection only; append-only run events are authoritative.
- Use Markdown files with YAML front matter for both correspondence and run artifacts.
- Enforce the milestone stop rule in the reducer with persisted stop reason `SECOND_TASK_CREATED`.
- Represent task acceptance and follow-up task creation as separate auditable events.

## 2026-09-15 Increment B

- Implement the dispatcher as a local, deterministic Python service with dry-run mocked coding agent.
- Persist dispatcher deliveries and intents in append-only `control/runs/dispatcher-events.jsonl` with file locking for crash-safe CAS semantics.
- Gate CI completion on exact implementation head SHA and configured required checks from trusted workflows only.
- Keep `SECOND_TASK_CREATED` as a hard stop guard before any new dispatcher dispatch.

## 2026-09-16 Increment C

- Use the OpenAI Responses API directly through `httpx` with strict JSON-schema output validation and bounded control-plane review context.
- Map GitHub issue numbers to protocol task IDs using the `task-0009` convention and preserve explicit branch instructions when present in the issue body.
- Use hidden correlation markers in GitHub issue bodies so task creation, assignment, and restart reconciliation remain idempotent without persisting secrets.
- Persist append-only control files to `refs/heads/copilot/development-automation-control` with optimistic Git ref updates and append-only conflict reconciliation for restart-safe recovery.
- Persist OpenAI review correspondence before creating any follow-up task and keep review retries exact-head bound when provider failures are retryable.
- Treat the second task as record-only in this increment: create the follow-up GitHub task, persist `NEXT_TASK_CREATED`, and stop before any second dispatch.