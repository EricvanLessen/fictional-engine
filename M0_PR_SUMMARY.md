# M0 PR: Repository Foundation

## Milestone & Acceptance

Completes [MILESTONES.md](docs/MILESTONES.md) **M0 — Repository foundation**.

### Acceptance Criteria

- [x] One documented command runs all checks
- [x] Container runs as non-root
- [x] Missing required configuration fails without printing secrets
- [x] `.env` and Telegram sessions are ignored

## Deliverables

### Python 3.12 Project Metadata
- `pyproject.toml` with exact Python 3.12 requirement
- All dependencies pinned for reproducibility (Pydantic v2, pytest, mypy, ruff, pre-commit)

### Package Structure (Copilot Instructions)
```
src/fictional_engine/
  config/                    # Settings and environment
  domain/                    # Framework-free domain models
  application/               # Orchestration
  adapters/                  # Infrastructure implementations
    telegram/
    tradelocker/
    persistence/
  scoring/                   # Advisory-only quality scoring
  observability/             # JSON logging and monitoring
```

Dependency direction: `adapters -> application -> domain`

### Configuration Model
- `EngineSettings` with safety defaults:
  - `EXECUTION_MODE=shadow` (never live)
  - `ALLOW_LIVE_TRADING=false` (enforced at validation)
- Live trading gate rejects `ALLOW_LIVE_TRADING=true` with clear error
- All secret fields use `pydantic.SecretStr` and are redacted
- Startup fails clearly if required config is missing, without printing secrets

### Ruff, Mypy, Pytest Configuration
- `ruff check` with strict lint rules (E, F, I, B, UP, ASYNC, RUF)
- `mypy` in strict mode with pydantic plugin
- `pytest` with async fixture support

### Pre-commit Hooks
- `ruff` check and format
- `mypy` type check
- Standard git safety (merge conflict, YAML, trailing whitespace)
- `detect-secrets` with baseline

### Dockerfile & Compose
- Runs as non-root user (`app`)
- Minimal Python 3.12 slim image
- `.dockerignore` to reduce layer size
- `docker-compose.yml` for local dev (respects `.env`)

### CI Workflows
- `.github/workflows/ci.yml`: lint, type check, tests on PR/push
- `.github/workflows/secret-scan.yml`: Gitleaks scanning
- Both jobs run on `ubuntu-latest` with Python 3.12

### One-Command Verification
```bash
make check
```
Runs in sequence: `ruff check` → `mypy` → `pytest`.

All three tools pass:
- ✓ 0 lint issues
- ✓ 14 source files, 0 type issues
- ✓ 4 tests pass

### Secret Safety
- `.env` in `.gitignore`
- Telegram session files excluded (`*.session`, `telegram-session*`)
- No secrets in Git history
- `detect-secrets` baseline configured with standard detectors
- All environment variables redacted in logs and error messages

## Testing

### Test Coverage
- [x] Default configuration is safe (shadow mode, live trading disabled)
- [x] ALLOW_LIVE_TRADING=true is rejected at startup
- [x] Missing configuration fails without leaking secrets
- [x] Initialization values override environment duplicates

### Commands Run & Results

```bash
# Install dev environment (Python 3.12 venv)
python3.12 -m venv venv
source venv/bin/activate
pip install -e '.[dev]'

# Run all checks
make check

# Results:
# ruff check .
# All checks passed!
# 
# mypy
# Success: no issues found in 14 source files
#
# pytest
# ....                                                       [100%]
# 4 passed in 0.13s
```

### Safety Verification

Live trading rejection test (all required fields provided):
```python
os.environ["ALLOW_LIVE_TRADING"] = "true"
EngineSettings()  # → ValidationError: "ALLOW_LIVE_TRADING=true is rejected..."
```

Secret non-leakage in errors:
```python
os.environ["TELEGRAM_API_HASH"] = "my-very-secret-value"
EngineSettings()  # → ValidationError without secret in message
```

## Documentation Updates

- [README.md](README.md): M0 quickstart, install instructions, container workflow
- Copilot instructions already present in repository

## Migration Notes

No database or schema changes in M0.

## Notable Decisions

1. **Strict Safety by Default**: Live trading is *rejected*, not optional. The gate is in code, not environment wrangling.
2. **Pydantic SecretStr**: All credentials stored internally as SecretStr and redacted in logs and error messages.
3. **One Verification Command**: The `Makefile` orchestrates all checks. CI mirrors this locally.
4. **Test Isolation**: Each test clears environment variables to prevent test pollution.
5. **Config Before Any Domain Logic**: Settings load first at startup; if invalid, the entire process fails clearly.

## Next Milestone

M1 requires raw-message immutable store, JSON fixture loader, and replay CLI. This PR is independent and makes no behavioral changes beyond repository foundation.
