# Development Automation Goal

## Approved goal

Remove Eric from the ChatGPT to VS Code Copilot relay for bounded implementation cycles. GitHub is the persistent control plane and audit trail. Copilot remains the code implementer. An OpenAI API coordinator reviews evidence and proposes bounded next tasks. Deterministic repository policy validates allowed transitions and GitHub-side actions.

## Authoritative control ref

Both agents read and write the control-plane protocol against `refs/heads/main`. Feature branches may stage changes for review, but `main` remains the single authoritative GitHub control ref.

## Increment boundary

This repository change implements Increment C only:

- live GitHub/Copilot task dispatch with append-only intent reconciliation
- live OpenAI Responses API review with schema-validated bounded output
- portable dispatcher entrypoint for GitHub Actions and local replay
- one auditable proof cycle from task creation through CI review to one follow-up task

This increment must still stop after persisting creation of the second task. Task two may exist as an auditable follow-up artifact, but it must not be dispatched in this milestone.

## Stop rule

Increment A must stop after persisting creation of the second task. The persisted stop reason is `SECOND_TASK_CREATED`. Task two may exist as an auditable follow-up artifact, but it must not be dispatched in this milestone.

## Safety boundary

- No arbitrary shell execution is authorized by the reducer or protocol layer.
- No trading runtime, broker credentials, Telegram credentials, or account operations are coupled to this package.
- Secrets and environment dumps are never stored in control files. Redactions must use the explicit marker `[REDACTED_CREDENTIAL]`.