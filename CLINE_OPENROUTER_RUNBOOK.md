# Cline + OpenRouter development automation

This increment replaces the live coding and review provider path while preserving GitHub as the
control plane and the existing append-only task state machine.

## Runtime path

1. A human creates a GitHub issue.
2. The dispatcher creates the task state and starts one Cline CLI process.
3. Cline reads the task instruction, edits the checkout, runs checks, pushes the exact task branch,
   and opens a draft pull request.
4. The pull-request event records the coding result.
5. Existing CI runs on the pull-request head.
6. OpenRouter performs the structured review and returns one bounded decision.
7. `NEXT_TASK` may create one managed follow-up issue; the existing hard stop prevents automatic
   execution of that second task.

The historical `COPILOT_*` and `OPENAI_*` state names remain unchanged in schema version 1. They
are compatibility names, not statements about the selected provider.

## Required repository secrets

- `OPENROUTER_API_KEY`: OpenRouter key with an account-level or key-level spend limit.
- `CLINE_GITHUB_TOKEN`: fine-grained GitHub token that can push branches and create pull requests.
  During migration, the workflow falls back to `COPILOT_AGENT_TOKEN` if this secret is absent.

`GITHUB_CONTROL_TOKEN` remains the short-lived Actions token used only for control-branch
persistence. The Cline GitHub token must be a user/app token because events created by the default
Actions token do not start another workflow run.

## Cost controls

- Coding and review default to `openrouter/auto`.
- OpenRouter Auto Router's cost/quality tradeoff is `7` for direct review calls.
- Cline receives the OpenRouter account's Auto Router defaults for coding requests.
- One Cline process, no teams or sub-agents, at most three consecutive retries, and a 30-minute
  wall-clock timeout.
- OpenRouter's key spend limit is the hard monetary ceiling. The dispatcher does not attempt to
  infer dollar cost from token counts.
- The second-task-created stop boundary remains the hard workflow ceiling for this proof cycle.

## Activation

1. Add `OPENROUTER_API_KEY` and preferably `CLINE_GITHUB_TOKEN` as GitHub Actions secrets.
2. Set an OpenRouter key limit suitable for the experiment (for example, USD 10).
3. Merge the dispatcher workflow to the repository default branch. GitHub runs event workflows
   from the default branch, so a workflow changed only inside a draft pull request is not a live
   proof.
4. Open one narrowly scoped issue. Include an explicit branch instruction if a specific branch is
   required, for example: `Use branch feat/example`.
5. Observe the dispatcher, generated draft pull request, CI, control branch, and OpenRouter usage.

Do not call the migration complete until one real issue reaches the configured terminal decision
and the OpenRouter dashboard shows the corresponding paid requests.
