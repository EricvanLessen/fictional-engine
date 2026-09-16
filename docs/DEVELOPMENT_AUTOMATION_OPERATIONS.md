# Development automation operations

## Required environment

- `COPILOT_AGENT_TOKEN`
- `OPENAI_API_KEY`
- `DEVELOPMENT_AUTOMATION_ALLOWLISTED_ACTORS`

Store both as GitHub Actions secrets or local environment variables. Never write them into `control/` documents, `.env`, logs, or committed fixtures.

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
  --actions-workflow-ref trusted/dispatcher.yml@refs/heads/main \
  --allowlisted-actors EricvanLessen ci-bot Copilot copilot-swe-agent
```

## Event handling summary

- `issues` and `issue_comment` can create or redispatch the bounded task.
- `pull_request` records Copilot result evidence for the bound branch and active attempt.
- `workflow_run` and `check_run` record exact-head CI evidence and trigger the OpenAI review.
- `push` is currently audit-only.

## Increment C stop rule

When OpenAI returns `NEXT_TASK`, the dispatcher persists the review, creates exactly one follow-up GitHub task, records `NEXT_TASK_CREATED`, and stops. Task two must not be dispatched in Increment C.
