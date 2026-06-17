# ADR-006: Dual CLI Mode — Direct DB and API Client

**Date:** 2026-06-17
**Status:** Accepted

## Decision

`ic` CLI operates in two modes: local (direct psycopg2 to PostgreSQL) and cluster (REST API client). Mode persisted in `~/.ic/config.json`, overridable by `IC_MODE` env var. Local mode auto-detects API on localhost:8000.

## Context

Developers need `ic` during development (local DB, no server running) and in production (remote API, no local DB). Maintaining two separate tools is impractical.

## Rationale

- Dev workflow: `task db:start && ./ic get alerts` — no API server needed
- Production: `pip install ic-tool && ic config use-cluster URL` — no DB needed
- Standalone PyPI package only depends on `click` + `requests` (11 packages)
- `cli/data.py` abstracts the source — commands don't know which mode
- `cli/mode.py` provides `ensure_cluster()` — one-line check in each command
- Bash fallback preserved for commands not yet migrated (legacy compat)

## When to Revisit

- **API-only** — when all bash commands are migrated, remove direct DB path entirely
- **Local API mode** — if `task serve` becomes standard dev workflow, local = localhost:8000 always
- **Multi-cluster** — add cluster context switching (`ic config use-cluster prod` vs `staging`)
