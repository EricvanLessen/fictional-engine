# Increment C: Real GitHub and OpenAI Integration

## Status

Increment C introduces:
- **Bootstrap system** for creating the first task with proper PR/commit binding
- **Full event-driven workflow** from PR events through OpenAI review to next task creation
- **Explicit safeguards** against duplicate reviews and duplicate task creation

## Architecture Decision: Bootstrap Requirement

The dispatcher expects tasks to already exist in the control state before processing PR events.
This breaks the "chicken-and-egg" problem:

- **Without bootstrap**: PR event arrives → dispatcher looks for task → task does not exist → NOOP
- **With bootstrap**: Admin bootstraps task → PR event arrives → dispatcher finds task → dispatch proceeds

The bootstrap is **intentional**:
1. It forces explicit intent: "We are starting work on PR #N"
2. It prevents accidental dispatch on stale PRs
3. It enables repeatable, auditable task creation

## Bootstrap Process

### Step 1: Create Task via CLI

```bash
python -m development_automation.cli bootstrap \
  --control-root control \
  --task-id task-0001 \
  --pr-number 10 \
  --head-sha "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef" \
  --branch "feat/development-automation-dispatcher"
```

Output:
```
✓ Bootstrap complete
  task_id: task-0001
  message_id: msg-bootstrap-...
  event_path: control/runs/...
  expected_head_sha: deadbeefdeadbeef...

Next step: send PR event to dispatcher for task_id=task-0001
```

**Effect**: A `TASK_CREATED` event is persisted in `control/runs/`. The task is in `READY_FOR_COPILOT` state.

### Step 2: PR Event Triggers Dispatch

When a PR event arrives (webhook or Actions):

```yaml
pull_request:
  types: [opened, synchronize]
```

The dispatcher:
1. Validates it matches the bootstrapped task's PR number and branch
2. Routes it to `_handle_dispatch_intent()`
3. Calls `MockCodingAgent` (or real Copilot integration in future)
4. Transitions task to `WAITING_FOR_CI`
5. Persists `COPILOT_DISPATCHED` event

**Requirement**: The dispatcher workflow must be on the `main` branch so GitHub's webhook delivery can find it. This is a GitHub Actions limitation.

### Step 3: CI Events Confirm

When CI checks complete:

```yaml
check_run:
  types: [completed]
workflow_run:
  types: [completed]
```

The dispatcher:
1. Validates the result's head SHA matches the task's `expected_head_sha`
2. Validates all required checks passed
3. Transitions task to ready for review
4. Persists `CI_EVIDENCE_RECORDED` event

### Step 4: OpenAI Review (Mocked in MVP)

In Increment C, OpenAI review is mocked (`MockAgentReviewResult`).
In production Increment D, this becomes real.

When review is complete:
1. Task state transitions to `READY_FOR_NEXT_TASK`
2. `OPENAI_REVIEW_RECORDED` event is persisted
3. If review accepted: `NEXT_TASK_CREATED` event creates task-0002
4. After `NEXT_TASK_CREATED`: stop boundary `SECOND_TASK_CREATED` becomes active
5. All further PR events are rejected with NOOP

## Event Flow Diagram

```
[READY_FOR_COPILOT]
    ↓
    PR opened/sync event → MockCodingAgent → COPILOT_DISPATCHED
    ↓
[WAITING_FOR_CI]
    ↓
    CI checks pass → validate exact head SHA → CI_EVIDENCE_RECORDED
    ↓
[WAITING_FOR_REVIEW]
    ↓
    MockOpenAI review → OPENAI_REVIEW_RECORDED
    ↓
    If ACCEPT → NEXT_TASK_CREATED → task-0002 created
    ↓
[SECOND_TASK_CREATED boundary]
    ↓
    All new PR events → NOOP (stop boundary reached)
```

## Safety Boundaries

### Deduplication

**Duplicate webhook deliveries**: Same delivery_id → NOOP
- GitHub retries webhook deliveries; we detect and skip
- File-based dedup store in `dispatcher_store.py`

**Duplicate dispatch on same attempt**: Same semantic_key → NOOP
- Two different `pull_request` events for the same commit → one dispatch
- Intent store ensures only first wins

### Stop Boundary (SECOND_TASK_CREATED)

After creating task-0002, a permanent stop reason is set:

```yaml
stop_reason: "STOP_REASON_SECOND_TASK_CREATED"
```

All subsequent `process_event()` calls check this and return NOOP.
Recovery tasks also respect this boundary.

### Head SHA Binding

- Bootstrap fixes `expected_head_sha` from the initial commit
- CI events must match this exactly
- Prevents stale events on abandoned branches

## Testing Increment C

### Unit Tests (Simulated)

In `tests/development_automation/test_bootstrap.py`:

- ✓ `test_bootstrap_generates_unique_task_ids`
- ✓ `test_bootstrap_task_creates_task_created_event`
- ✓ `test_bootstrap_produces_idempotent_result`
- ✓ `test_pr_event_finds_bootstrapped_task`
- ✓ `test_increment_c_cycle_task_bootstrap_dispatch_ci`
- ✓ `test_increment_c_blocks_duplicate_dispatch_on_same_attempt`
- ✓ `test_increment_c_stops_at_second_task_created`
- ✓ `test_increment_c_pr_events_trigger_dispatch_from_bootstrap`

These tests are **simulated**: they mock GitHub events and Copilot, running entirely in-process.

### Integration Test (Real GitHub Events)

For the full Increment C cycle with real GitHub events:

1. **Bootstrap on main**: Dispatcher workflow must be on `refs/heads/main`
   - GitHub only triggers webhooks for workflows on the default branch

2. **Create PR #10** on the feature branch

3. **Use manual workflow dispatch** or check that:
   ```
   - PR opened event arrives
   - Dispatcher processes it
   - CI runs
   - MockOpenAI review completes
   - task-0002 created
   - Stop boundary active
   ```

4. **Read the control state**:
   ```bash
   ls -la control/runs/
   cat control/runs/*.md
   ```

### Verification Checklist

After a complete Increment C cycle:

- [ ] `control/runs/` contains exactly 3+ events: TASK_CREATED, COPILOT_DISPATCHED, CI_EVIDENCE_RECORDED, OPENAI_REVIEW_RECORDED, NEXT_TASK_CREATED
- [ ] task-0001 → `READY_FOR_COPILOT` → `WAITING_FOR_CI` → `WAITING_FOR_REVIEW` → terminal state
- [ ] task-0002 exists and is `READY_FOR_COPILOT`
- [ ] Projection shows `stop_reason = "STOP_REASON_SECOND_TASK_CREATED"`
- [ ] New PR events are rejected with NOOP
- [ ] No duplicate dispatches (intent ID remains stable)

## Known Limitations & Future Work

### Limitation: Workflow Must Be on main

GitHub only exports and executes workflows on the default branch.
To enable dispatcher webhook handling:

1. Merge a branch containing `.github/workflows/dispatch.yml` to main
2. GitHub will then make the workflow runnable

This is a blocker for real GitHub integration if not planned.

### Limitation: Mock Agents Only

Copilot and OpenAI agents are mocked.
Production implementation (Increment D) will require:

- Actual Copilot API integration
- OAuth token handling
- OpenAI API calls
- Error recovery for failed reviews

### Limitation: PR #10 Remains as Draft

Per requirements, PR #10 should remain as draft during MVP.
Reasons:
1. No live deployment
2. Increments D+ require production review gates
3. Bootstrap example is for demonstration only

## Next Steps (Increment D+)

- Real Copilot integration via Actions
- Real OpenAI integration for review
- Notification delivery (Slack, email, etc.)
- Distributed recovery across multiple workers
- Metrics and observability dashboards
