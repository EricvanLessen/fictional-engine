# Increment C Real Cycle Activation Plan

**Current State**: Bootstrap implemented and tested. Real cycle ready for deployment.

**What Works**:
- ✓ Bootstrap creates first task (verified locally)
- ✓ Real PortableDispatcherEntrypoint exists
- ✓ Live adapters (Copilot, OpenAI) implemented
- ✓ GitHub persistence configured
- ✓ Dispatcher workflow defined (.github/workflows/development-automation-dispatcher.yml)
- ✓ GitHub Secrets configured (COPILOT_AGENT_TOKEN, OPENAI_API_KEY)
- ✓ All 101 tests pass

**Activation Requirements**:

## Phase 1: Task Bootstrap (Ready)
**Trigger**: GitHub issues event (opened)
**Flow**: 
1. Create GitHub issue #1 with task description
2. GitHub webhook fires issues.opened event
3. Dispatcher workflow runs (development-automation-dispatcher.yml)
4. PortableDispatcherEntrypoint.handle_event(event_name=ISSUES, ...)
5. _create_task_from_issue() creates task-0001
6. Task state: READY_FOR_COPILOT
7. Control branch synced (if DEVELOPMENT_AUTOMATION_GITHUB_TOKEN configured)

**Local Prerequisite**: Cannot test locally without:
- Real GitHub token for control branch write
- But: Can call handle_event() with simulated issue payload

## Phase 2: Copilot Dispatch (Requires Secret)
**Trigger**: GitHub push event on feat/increment-c-* branch
**Flow**:
1. Commit pushed to feat/increment-c-* branch
2. GitHub webhook fires push event
3. Dispatcher workflow runs
4. PortableDispatcherEntrypoint.handle_event(event_name=PUSH, ...)
5. GitHubCopilotCodingAgent.run() called (REAL with COPILOT_AGENT_TOKEN secret)
6. Copilot makes commits to branch
7. New head SHA established
8. Task state: COPILOT_RUNNING → WAITING_FOR_CI

**Required**: 
- ✓ COPILOT_AGENT_TOKEN secret (CONFIGURED)
- Real commit pushed to feat/increment-c-* branch
- GitHub Actions runner available

**Known Issue**: Cannot test locally - requires real GitHub Actions execution

## Phase 3: CI Validation (Workflow Dependent)
**Trigger**: GitHub workflow_run event (CI workflow completes) or check_run event
**Flow**:
1. CI workflow (ci.yml) runs and completes
2. Dispatcher records CHECK_RUN evidence
3. CI validation gate (ci_gate.py) checks exact head SHA match
4. If all checks pass: Task state → WAITING_FOR_OPENAI_REVIEW

**Required**: 
- CI workflow must complete
- Check names must match: checks, docker, gitleaks
- Dispatcher must observe workflow_run events

**Known Issue**: Cannot test locally - requires real CI execution on branch

## Phase 4: OpenAI Review (Real API Call)
**Trigger**: After CI checks pass
**Flow**:
1. Entrypoint calls OpenAIReviewAdapter.review()
2. ReviewContext includes task ID, branch, PR diff, CI summary
3. Real OpenAI API call (gpt-4-mini or configured model)
4. Decision recorded: ACCEPT | FIX_REQUIRED | NEXT_TASK | BLOCKED
5. Task state: COMPLETED or PAUSED or NEXT_TASK recorded

**Required**:
- ✓ OPENAI_API_KEY secret (CONFIGURED)
- Real API call (costs money)
- Control branch must be writable

**Known Issue**: Real API call - cannot mock in production cycle

## Phase 5: Follow-up Task (if NEXT_TASK)
**Trigger**: OpenAI decision = NEXT_TASK
**Flow**:
1. New GitHub issue created for task-0002
2. Control state records NEXT_TASK with task details
3. task-0002 in READY_FOR_COPILOT (awaits activation)
4. **STOP**: task-0002 must NOT automatically run
5. Operator must manually review and trigger next cycle

**Required**:
- Manual operator review
- GitHub issue creation permissions
- No automatic dispatch of task-0002

---

## How to Run Real Cycle End-to-End

### Prerequisites (One-time Setup)
```bash
# Verify local code is on PR #10 branch
git branch -vv
# Expected: copilot/increment-c-live-openai-coordinator

# Verify secrets exist in repo
gh secret list
# Expected: COPILOT_AGENT_TOKEN, OPENAI_API_KEY both present

# Verify dispatcher workflow exists
gh workflow list
# Expected: development-automation-dispatcher workflow present
```

### Execution Steps

#### Step 1: Create GitHub Issue (Task Bootstrap)
```bash
# Create issue via GitHub API or Web UI
# Title: "[Task 0001] Increment C Real Cycle Test"
# Body: "Complete real cycle: Copilot → CI → OpenAI → Stop before Task 2"
# Expected: Dispatcher workflow automatically triggers
#           Control state recorded
#           Task created in READY_FOR_COPILOT
```

#### Step 2: Push Commit to Feature Branch (Copilot Dispatch)
```bash
# Create and push commit on feat/increment-c-real-test
git checkout -b feat/increment-c-real-test
echo "# Real Cycle Test" >> README.md
git add README.md
git commit -m "Real cycle test commit"
git push origin feat/increment-c-real-test

# Expected: Dispatcher workflow triggers on push
#           GitHubCopilotCodingAgent.run() called
#           Copilot makes commits
#           Control state updated: COPILOT_RUNNING
```

#### Step 3: Wait for CI Workflow (CI Validation)
```bash
# CI workflow runs automatically on push
# Monitor: GitHub Actions → CI workflow
# Expected: Check runs created: checks, docker, gitleaks
#           Dispatcher records evidence
#           Control state: WAITING_FOR_OPENAI_REVIEW
```

#### Step 4: OpenAI Review (Real API)
```bash
# Dispatcher automatically calls OpenAIReviewAdapter
# OpenAI API processes ReviewContext
# Expected: Review decision recorded
#           Control state updated based on decision
#           If ACCEPT: Task completed
#           If FIX_REQUIRED: Task paused
#           If NEXT_TASK: task-0002 created (manual trigger required)
```

#### Step 5: Stop - No Auto-Start of Task 2
```bash
# If decision was NEXT_TASK:
# - task-0002 issue created
# - task-0002 in READY_FOR_COPILOT
# - task-0002 **NOT** automatically dispatched
# - Operator must review and manually trigger

# Verify stop rule:
git log --oneline | grep "task-0002"
# Expected: Only creates task, no automatic dispatch
```

---

## Verification Points

| Phase | Verify With | Expected |
|-------|-------------|----------|
| Bootstrap | `git log --all \| grep task-0001` | Task created |
| Copilot | Control branch: `copilot/development-automation-control` | Commits recorded |
| CI | GitHub Actions logs | Checks passed |
| OpenAI | Control branch documents | Decision recorded |
| Stop | task-0002 state | In READY_FOR_COPILOT, not running |

---

## Blockers / Not Available Locally

| Blocker | Reason | Solution |
|---------|--------|----------|
| Real GitHub webhook delivery | Requires public URL | Runs on GitHub Actions automatically |
| Real Copilot API (Phase 2) | Requires COPILOT_AGENT_TOKEN secret | Secret exists, runs via Actions |
| Real OpenAI API (Phase 4) | Costs money, real API call | Secret exists, necessary for real cycle |
| Local simulator cannot test Phases 2-5 | These require GitHub Actions runtime | Deploy to GitHub (already configured) |

---

## Merge Requirement

**Current State**: PR #10 is DRAFT

**To Activate Real Cycle**:
The dispatcher workflow automatically runs when events occur. However, for control branch persistence and some dispatcher features to work correctly, the default branch permissions may need review.

**No merge required** for bootstrap testing (Phases 1 only).

**If Phases 2-5 need production deployment**: Merge PR #10 → main (decision outside this scope).

---

## Conclusion

**Bootstrap Integration**: ✓ COMPLETE
- Real PortableDispatcherEntrypoint integrated
- Mocks only for tests
- All 101 tests pass

**Real Cycle Potential**: ✓ READY
- GitHub Secrets configured
- Dispatcher workflow prepared
- Live adapters in place

**Next Step**: Create real GitHub issue #1 to trigger Phase 1 (bootstrap).

**Expected Outcome After Full Cycle**:
- task-0001 progresses through phases
- task-0002 created (NEXT_TASK decision)
- task-0002 **stops** at READY_FOR_COPILOT (no auto-dispatch)
- Control branch updated with events
- No tasks auto-executed
- No live trading enabled
