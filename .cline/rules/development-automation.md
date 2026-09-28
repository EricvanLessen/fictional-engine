# Fictional Engine autonomous coding rules

- Exactly one coding agent may be active. Do not spawn sub-agents or teams.
- Treat the newest `TASK_INSTRUCTION` for the active task under `control/messages` as the
  authoritative task request.
- Treat `control/GOAL.md`, `control/ARCHITECTURE.md`, `control/CURRENT_STATE.md`, and
  `control/DECISIONS.md` as read-only architecture context.
- Never modify or commit generated files under `control/messages`, `control/runs`, or lock files.
- Never read, print, persist, or expose environment variables, tokens, or credentials.
- Do not deploy, merge, trade, force-push, rewrite history, or weaken safety checks.
- Keep changes scoped to the active task and preserve unrelated work.
- Use Python 3.12 and the tools declared in `pyproject.toml`.
- Before publishing, run relevant pytest tests, Ruff, and mypy.
- Commit to the exact task branch, push it, and open one draft pull request against `main`.
- If requirements are materially ambiguous or an unsafe action is required, stop and report the
  blocker instead of guessing.
