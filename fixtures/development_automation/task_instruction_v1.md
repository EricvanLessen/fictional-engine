---
schema_version: control-message.v1
message_id: msg-20260915-eric-copilot-0001
task_id: task-0042
from: eric
to: copilot
type: TASK_INSTRUCTION
status: PENDING
branch: feat/development-automation-protocol
created_at: 2026-09-15T12:45:00Z
in_reply_to: null
attempt: 1
expected_head_sha: null
pull_request_url: null
commit_sha: null
---

# Objective

Implement Increment A only.

## Acceptance criteria

- Add control documents and append-only directories.
- Add versioned schemas, validator, reducer, fixtures, and offline tests.
- Stop after creating exactly one follow-up task.

## Allowed scope

- control/
- src/development_automation/
- fixtures/development_automation/
- tests/development_automation/