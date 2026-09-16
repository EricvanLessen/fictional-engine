# Development Automation Decisions

## 2026-09-15

- Use `refs/heads/main` as the sole authoritative control ref for both agents.
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