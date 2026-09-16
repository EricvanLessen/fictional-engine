# Current State

## Authority

`control/runs/` is the source of truth. This document is a human-readable projection and design checkpoint.

## Increment status

- Increment A: implemented on feature branch for review in this pull request; not authoritative until merged to `refs/heads/main`.
- Increment B: implemented on `feat/development-automation-dispatcher` for review in this pull request; not authoritative until merged to `refs/heads/main`.
- Increment C: implemented on `feat/development-automation-live-adapters` for review; proof stops at `SECOND_TASK_CREATED`.
- Increment D: not started.

## Available guarantees after Increment A

- Versioned control envelopes can be parsed, validated, rendered, and reloaded offline.
- Append-only storage rejects duplicate message IDs and preserves collision-safe filenames.
- Run events can rebuild task projections after restart.
- The reducer enforces attempt limits, stale-head checks, explicit pause or complete states, and the `SECOND_TASK_CREATED` stop boundary.

## Available guarantees after Increment C

- A portable dispatcher entrypoint can normalize trusted GitHub issue, comment, pull request, workflow-run, check, and push event families.
- GitHub task dispatch persists append-only task, run, and dispatcher-intent evidence before and after live provider calls.
- Copilot task creation or assignment uses stable correlation markers so restart reconciliation does not create duplicate GitHub issues or duplicate assignments.
- OpenAI review requests use bounded repository/task/PR/CI/control-doc context and require schema-validated structured output with one decision.
- Retryable OpenAI provider failures do not persist review decisions, and the existing review intent is reset for a later exact-head retry.
- The live-capable proof path stops after persisting the second task and never dispatches task two.

## Not yet implemented

- Notification delivery remains deferred to Increment D.
- Continued autonomous execution past `SECOND_TASK_CREATED` remains forbidden.

## Available guarantees after Increment B

- Deterministic GitHub event dispatcher policy for allowlisted repositories, actors, and supported event types.
- Webhook HMAC verification, body size guard, and duplicate delivery-ID suppression.
- Actions transport trust checks for authenticated invocation and workflow-ref allowlists.
- Durable append-only dispatcher claim/intents with replay-based recovery of unfinished work.
- Duplicate concurrent deliveries converge to one logical dispatch intent.
- CI gate approval requires configured check names, trusted workflow identities, completed status, success conclusion, and exact head SHA match.
- `SECOND_TASK_CREATED` stop boundary remains active and blocks new dispatch attempts.