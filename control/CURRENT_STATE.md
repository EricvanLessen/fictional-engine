# Current State

## Authority

`control/runs/` is the source of truth. This document is a human-readable projection and design checkpoint.

## Increment status

- Increment A: implemented on feature branch for review in this pull request; not authoritative until merged to `refs/heads/main`.
- Increment B: implemented on `feat/development-automation-dispatcher` for review in this pull request; not authoritative until merged to `refs/heads/main`.
- Increment C: not started.
- Increment D: not started.

## Available guarantees after Increment A

- Versioned control envelopes can be parsed, validated, rendered, and reloaded offline.
- Append-only storage rejects duplicate message IDs and preserves collision-safe filenames.
- Run events can rebuild task projections after restart.
- The reducer enforces attempt limits, stale-head checks, explicit pause or complete states, and the `SECOND_TASK_CREATED` stop boundary.

## Not yet implemented

- Real GitHub provider calls for Copilot dispatch or review remain deferred to Increment C.
- OpenAI API reviewer integration remains deferred to Increment C.
- Notification delivery remains deferred to Increment D.

## Available guarantees after Increment B

- Deterministic GitHub event dispatcher policy for allowlisted repositories, actors, and supported event types.
- Webhook HMAC verification, body size guard, and duplicate delivery-ID suppression.
- Actions transport trust checks for authenticated invocation and workflow-ref allowlists.
- Durable append-only dispatcher claim/intents with replay-based recovery of unfinished work.
- Duplicate concurrent deliveries converge to one logical dispatch intent.
- CI gate approval requires configured check names, trusted workflow identities, completed status, success conclusion, and exact head SHA match.
- `SECOND_TASK_CREATED` stop boundary remains active and blocks new dispatch attempts.