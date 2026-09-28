# Development Automation Architecture

## Scope

The accepted architecture has exactly three components:

1. Message protocol stored in GitHub-controlled files.
2. Deterministic event dispatcher that validates policy and executes a narrow GitHub action set.
3. OpenAI coordinator or reviewer that produces bounded structured decisions.

Increment A implements only the first component and the pure state reducer used by later components.

## Storage model

- `control/messages/` stores append-only agent correspondence.
- `control/runs/` stores append-only run events.
- `control/CURRENT_STATE.md` is a projection summary for humans; immutable run files remain authoritative.
- Git history preserves updates to summary documents.
- Correspondence and run files are never overwritten or deleted.

## Protocol format

Each correspondence or run file is Markdown with YAML front matter. The front matter contains:

- `schema_version`
- `message_id`
- `task_id`
- `from`
- `to`
- `type`
- `status`
- `branch`
- `created_at`
- `in_reply_to`
- `attempt`
- `expected_head_sha`
- PR or commit references when applicable

Increment A adds `control-message.v1` and `control-run.v1` schemas and validates them with Pydantic.

## Workflow model

The reducer projects task state from immutable run events with the lifecycle:

- `READY_FOR_COPILOT`
- `COPILOT_RUNNING`
- `WAITING_FOR_CI`
- `WAITING_FOR_OPENAI_REVIEW`
- `PAUSED`
- `COMPLETED`

Reviewer decisions are:

- `ACCEPT`
- `FIX_REQUIRED`
- `NEXT_TASK`
- `BLOCKED`
- `HUMAN_DECISION_REQUIRED`

Acceptance and next-task creation are separate auditable events. The reducer persists attempts, no-progress count, head SHA, CI evidence, provider run IDs, and stop reasons. It rejects malformed envelopes, duplicate message IDs at append time, stale heads, path traversal, and unknown transitions.

## Current increment boundary

- Increment B delivered the deterministic dispatcher, GitHub transport validation, duplicate-delivery control, and CI gating.
- Increment C adds the live GitHub/Copilot provider boundary, the OpenAI Responses API review loop, and the bounded real proof cycle.
- Increment D remains follow-up work for continued execution after proof, notifications, and optional merge automation under existing branch protections.

Increment C must continue to enforce the hard stop after `SECOND_TASK_CREATED`.