# Current State

## Authority

`control/runs/` is the source of truth. This document is a human-readable projection and design checkpoint.

## Increment status

- Increment A: implemented on feature branch for review in this pull request; not authoritative until merged to `refs/heads/main`.
- Increment B: not started.
- Increment C: not started.
- Increment D: not started.

## Available guarantees after Increment A

- Versioned control envelopes can be parsed, validated, rendered, and reloaded offline.
- Append-only storage rejects duplicate message IDs and preserves collision-safe filenames.
- Run events can rebuild task projections after restart.
- The reducer enforces attempt limits, stale-head checks, explicit pause or complete states, and the `SECOND_TASK_CREATED` stop boundary.

## Not yet implemented

- Webhook or Actions event intake.
- GitHub claim or compare-and-swap execution.
- Remote Copilot task dispatch.
- OpenAI API calls.
- Live CI reconciliation against GitHub checks.
- Notification delivery.