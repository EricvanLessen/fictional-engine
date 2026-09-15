# Development Automation Goal

## Approved goal

Remove Eric from the ChatGPT to VS Code Copilot relay for bounded implementation cycles. GitHub is the persistent control plane and audit trail. Copilot remains the code implementer. An OpenAI API coordinator reviews evidence and proposes bounded next tasks. Deterministic repository policy validates allowed transitions and GitHub-side actions.

## Authoritative control ref

Both agents read and write the control-plane protocol against `refs/heads/main`. Feature branches may stage changes for review, but `main` remains the single authoritative GitHub control ref.

## Increment boundary

This repository change implements Increment A only:

- control documents and append-only directories
- versioned control schemas
- Markdown plus YAML front-matter validator
- pure reducer for deterministic workflow projection
- fixtures and offline tests

This increment does not implement the dispatcher, live provider integrations, or autonomous proof execution.

## Stop rule

Increment A must stop after persisting creation of the second task. The persisted stop reason is `SECOND_TASK_CREATED`. Task two may exist as an auditable follow-up artifact, but it must not be dispatched in this milestone.

## Safety boundary

- No arbitrary shell execution is authorized by the reducer or protocol layer.
- No trading runtime, broker credentials, Telegram credentials, or account operations are coupled to this package.
- Secrets and environment dumps are never stored in control files. Redactions must use the explicit marker `[REDACTED_CREDENTIAL]`.