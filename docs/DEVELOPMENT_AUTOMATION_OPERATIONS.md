# Development automation operations

## Required environment

- `COPILOT_AGENT_TOKEN`
- `GITHUB_CONTROL_TOKEN`
- `OPENAI_API_KEY`
- `DEVELOPMENT_AUTOMATION_ALLOWLISTED_ACTORS`
- `DEVELOPMENT_AUTOMATION_CONTROL_BRANCH` (defaults to `copilot/development-automation-control`)

Store these as GitHub Actions secrets or local environment variables. `GITHUB_CONTROL_TOKEN` should be scoped only to control-state persistence, while `COPILOT_AGENT_TOKEN` remains scoped to Copilot assignment calls. Never write them into `control/` documents, `.env`, logs, or committed fixtures.

## Entrypoint

Use the portable dispatcher entrypoint:

```bash
development-automation-dispatch \
  --event-name issues \
  --event-path /path/to/event.json \
  --control-root /absolute/path/to/control \
  --repository EricvanLessen/fictional-engine \
  --delivery-id delivery-123 \
  --source actions \
  --actions-workflow-ref EricvanLessen/fictional-engine/.github/workflows/development-automation-dispatcher.yml@refs/heads/main \
  --allowlisted-actors EricvanLessen ci-bot Copilot copilot-swe-agent
```

GitHub Actions should provide:

- `GITHUB_CONTROL_TOKEN=${{ github.token }}`
- `DEVELOPMENT_AUTOMATION_REQUIRED_CHECKS=checks,docker,gitleaks`
- `DEVELOPMENT_AUTOMATION_TRUSTED_WORKFLOW_REFS=EricvanLessen/fictional-engine/.github/workflows/ci.yml@refs/heads/main,EricvanLessen/fictional-engine/.github/workflows/secret-scan.yml@refs/heads/main`

## Event handling summary

- `issues` and `issue_comment` can create or redispatch the bounded task.
- `pull_request` records Copilot result evidence for the bound branch and active attempt.
- `workflow_run` and `check_run` record exact-head CI evidence and trigger the OpenAI review.
- `push` is currently audit-only.

## Increment C stop rule

When OpenAI returns `NEXT_TASK`, the dispatcher persists the review, creates exactly one follow-up GitHub task, records `NEXT_TASK_CREATED`, and stops. Task two must not be dispatched in Increment C.
