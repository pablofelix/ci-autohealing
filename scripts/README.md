# Scripts

## install-ic.sh

One-line installer for the standalone `ic` CLI tool:

```bash
curl -sSL https://raw.githubusercontent.com/pablofelix/ci-autohealing/develop/scripts/install-ic.sh | bash
```

Creates a Python venv, installs `ic-tool` from GitHub, and adds a wrapper script to `~/.local/bin/ic`.

## check-style.sh

Pre-commit hook for STYLE.md compliance. Runs automatically on `git commit`. Checks:

- No mutable dataclass configs (must use `frozen=True`)
- No bare `except:` (catch specific exceptions)
- No `print()` in library code (use `logging`)
- No `import *` (explicit imports only)
- No duplicated env var defaults (DRY)

## setup/

Legacy setup scripts (may be outdated):

- `setup_tracking.sh` — Langfuse observability setup
