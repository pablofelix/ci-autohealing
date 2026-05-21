# ADR-005: Bash CLI Refactoring Principles

**Date:** 2026-05-06
**Status:** Accepted

## Context

The `ic` CLI grew organically from a thin psql wrapper to a large bash script (currently ~8700 lines). An antipattern audit identified 7 systemic issues:

- Inline Python heredoc blocks (originally 14, now reduced to ~9 by extracting to `src/cli/`)
- 138 scattered SQL queries with 9x duplication of the `latest_builds` CTE
- 6 god functions exceeding 200 lines each (largest: `cmd_get_alerts` at 393 lines)
- Copy-pasted logic across 3 alert command variants and 3 number-resolution blocks
- Hard-coded credentials (`PGPASSWORD="admin"`), URLs, and version strings in function bodies
- 173 `2>/dev/null` silent error suppressions with no `set -o pipefail`
- No formatting helpers despite 35+ repeated section header banners and 40+ xargs trim patterns

ADR-001 established that collectors should be Python, but noted the `ic` CLI "remains bash because it's a thin query layer." That assumption no longer holds — the CLI now contains substantial data transformation, AI analysis orchestration, and business logic that violates the "bash is glue" principle from STYLE.md.

## Decision

Refactor the `ic` CLI incrementally, following these principles:

1. **Extract Python into standalone modules** — Move `python3 -c "..."` blocks into `src/cli/` modules. Complex operations delegate from bash to Python via `ic.py` → `src/cli/main.py`.

2. **Consolidate SQL into named query functions** — Create `ic-queries.sh` with functions like `sql_latest_failing_builds()`, `sql_count_unresolved()`. Each CTE is defined once. All bash files source this.

3. **Extract formatting helpers** — Create `ic-format.sh` with `section_header()`, `trim()`, `clipboard_or_print()`. Source from `ic`.

4. **Decompose god functions** — Break functions >80 lines into fetch/display/logic sub-functions. The three alert variants (`cmd_get_alerts`, `cmd_get_alerts_on_date`, `cmd_get_alerts_range`) share a common implementation parameterized by date filter.

5. **Centralize configuration** — Move all hard-coded values to `ic-config.sh` with `${VAR:=default}` pattern. Credentials load from `.env` only. No generic `lib/` directory — files are named with `ic-` prefix alongside the main script, following the STYLE.md rule against generic container names.

6. **Fix error handling** — Add `set -o pipefail`. Replace blanket `2>/dev/null` with proper error checks. Use `return 1` (not `exit 1`) in functions.

## Consequences

- **Positive:** Python blocks get linting, testing, IDE support
- **Positive:** SQL changes happen in one place instead of 9
- **Positive:** Functions become small enough to read and modify safely
- **Positive:** New contributors can find and understand query logic
- **Negative:** More files to navigate (but each is focused and greppable)
- **Negative:** Migration is incremental — mixed old/new patterns during transition
- **Constraint:** Every phase must leave `ic` fully functional (no big-bang rewrite)
