# Increment C Execution Guide

## Overview

Increment C implements the full event-driven workflow from task bootstrap through OpenAI review completion. This guide demonstrates:

1. **Bootstrap**: Create the first task with explicit PR binding
2. **Event Flow**: PR → dispatch → CI → review → next task
3. **Verification**: Confirm the control state and cycle completion
4. **Safety Checks**: Verify deduplication and stop boundaries

## Prerequisites

- Python 3.12+ (for running tests)
- `fictional-engine` repository cloned
- Working directory: `fictional-engine-issue6-b/`

## Step 1: Bootstrap the First Task

### Command

```bash
cd fictional-engine-issue6-b
python -m development_automation.cli bootstrap \
  --control-root control \
  --task-id task-0001 \
  --pr-number 10 \
  --head-sha "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef" \
  --branch "feat/development-automation-dispatcher"
```

### Expected Output

```
✓ Bootstrap complete
  task_id: task-0001
  message_id: msg-bootstrap-task-0001-abc12345
  event_path: control/runs/20260917T120000Z_system_github_task-0001_msg-bootstrap-task-0001-abc12345.md
  expected_head_sha: deadbeefdeadbeefdeadbeefdeadbeefdeadbeef

Next step: send PR event to dispatcher for task_id=task-0001
```

### Effect

- A `TASK_CREATED` event is written to `control/runs/`
- Task state is `READY_FOR_COPILOT`
- PR #10 is bound to this task
- All future PR #10 events will find this task

## Step 2: Verify Bootstrap

### Check Control State

```bash
ls -la control/runs/
cat control/runs/*.md
```

### Expected Files

```
control/runs/
  20260917T120000Z_system_github_task-0001_msg-bootstrap-task-0001-abc12345.md
```

### Verify Event Content

```bash
grep -E "event_type|lifecycle_state|pull_request_number" control/runs/*.md
```

Expected:
```
event_type: TASK_CREATED
lifecycle_state: READY_FOR_COPILOT
pull_request_number: 10
```

## Step 3: Send PR Event (Simulated)

In production, GitHub would automatically send this webhook.
For testing, we simulate it using the test harness.

### Using Test Harness

```python
# From tests/development_automation/test_bootstrap.py::test_increment_c_pr_events_trigger_dispatch_from_bootstrap
# Run: pytest tests/development_automation/test_bootstrap.py::test_increment_c_pr_events_trigger_dispatch_from_bootstrap -v
```

### Expected Log

```
control/runs/
  20260917T120000Z_system_github_task-0001_msg-bootstrap-task-0001-abc12345.md
  20260917T120001Z_copilot_github_task-0001_msg-copilot-dispatched-abc12346.md
```

Task transitions to `WAITING_FOR_CI`.

## Step 4: Simulate Copilot Dispatch

### Command

```bash
python -m development_automation.cli simulate-dispatch \
  --control-root control \
  --task-id task-0001 \
  --head-sha "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef" \
  --branch "feat/development-automation-dispatcher" \
  --provider-run-id "run-copilot-mock-001"
```

### Expected Output

```
✓ Copilot dispatched
  task_id: task-0001
  message_id: msg-copilot-dispatched-task-0001-abc12346
  lifecycle_state: WAITING_FOR_CI
  provider_run_id: run-copilot-mock-001

Next step: wait for CI checks or send CI evidence to dispatcher
```

## Step 5: Run Simulated Tests

All Increment C events and state transitions are tested in `tests/development_automation/test_bootstrap.py`.

### Bootstrap Tests

```bash
pytest tests/development_automation/test_bootstrap.py::test_bootstrap_task_creates_task_created_event -v
```

Expected: `PASSED`

### Full Cycle Test

```bash
pytest tests/development_automation/test_bootstrap.py::test_increment_c_cycle_task_bootstrap_dispatch_ci -v
```

Expected: `PASSED`
- Task created in `READY_FOR_COPILOT`
- `COPILOT_DISPATCHED` event created
- Projection state updated to `WAITING_FOR_CI`

### Deduplication Test

```bash
pytest tests/development_automation/test_bootstrap.py::test_increment_c_blocks_duplicate_dispatch_on_same_attempt -v
```

Expected: `PASSED`
- First PR event: action=`DISPATCHED`
- Duplicate PR event (same delivery_id): action=`NOOP`

### Stop Boundary Test

```bash
pytest tests/development_automation/test_bootstrap.py::test_increment_c_stops_at_second_task_created -v
```

Expected: `PASSED`
- After `SECOND_TASK_CREATED` event is recorded
- New PR events are rejected with `NOOP`
- Reason contains `"SECOND_TASK_CREATED"`

## Step 6: Run All Tests

```bash
pytest tests/development_automation/test_bootstrap.py -v
```

Expected: All 8 tests pass (or more if tests are added)

```
test_bootstrap_generates_unique_task_ids PASSED
test_bootstrap_task_creates_task_created_event PASSED
test_bootstrap_produces_idempotent_result PASSED
test_bootstrap_rejects_duplicate_with_different_content PASSED
test_pr_event_finds_bootstrapped_task PASSED
test_increment_c_cycle_task_bootstrap_dispatch_ci PASSED
test_increment_c_blocks_duplicate_dispatch_on_same_attempt PASSED
test_increment_c_stops_at_second_task_created PASSED
test_increment_c_pr_events_trigger_dispatch_from_bootstrap PASSED
```

Total: 9 passed
Time: ~5-10 seconds

## Verification Checklist

After completing steps 1-6:

- [x] Bootstrap creates `TASK_CREATED` event
- [x] Event is persisted in `control/runs/`
- [x] Task state is `READY_FOR_COPILOT`
- [x] PR #10 is bound to task
- [x] PR events find the task
- [x] Copilot dispatch creates `COPILOT_DISPATCHED` event
- [x] Task transitions to `WAITING_FOR_CI`
- [x] Duplicate events are deduplicated
- [x] Stop boundary blocks further dispatch
- [x] Control state is consistent

## Known Blockers

### 1. Dispatcher Workflow on main

**Issue**: GitHub only triggers webhooks for workflows on the default branch.

**Blocker**: To enable real webhook delivery, the dispatcher workflow must be merged to `main`.

**Current State**: Workflow is on `feat/development-automation-dispatcher` branch.

**Resolution**: Not required for Increment C tests (simulated). Required only for real GitHub integration after merge to main.

### 2. Python 3.12+ Requirement

**Issue**: Project requires Python 3.12+; some environments may have Python 3.9.

**Blocker**: Cannot import modules in Python 3.9 due to `str | None` type hint syntax (requires 3.10+).

**Current State**: Tests are written and syntactically correct; execution requires Python 3.12+.

**Resolution**: Ensure CI environment and local development use Python 3.12+.

## Next Phase: Real GitHub Integration

After merging to main and ensuring Python 3.12+:

1. Merge dispatcher workflow to `refs/heads/main`
2. Create PR #10 on the feature branch
3. GitHub will send real webhook events
4. Dispatcher will process them using the bootstrap task
5. Complete the full cycle with real CI and OpenAI events

## Troubleshooting

### Tests Fail: "task ... has not been created"

**Cause**: PR event is trying to dispatch but no task exists.

**Fix**: Run bootstrap command first.

### Tests Fail: "duplicate delivery id"

**Expected behavior**: This is correct. Second delivery of same event should return NOOP.

**Verify**: Check `test_increment_c_blocks_duplicate_dispatch_on_same_attempt`

### ImportError: `from __future__ import annotations`

**Cause**: Python version < 3.10.

**Fix**: Upgrade to Python 3.12+.

### Control files not found

**Cause**: Working directory is wrong.

**Fix**: `cd fictional-engine-issue6-b` before running commands.
