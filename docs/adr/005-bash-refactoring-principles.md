# ADR-005: Bash CLI Refactoring Principles

**Date:** 2026-05-06
**Status:** Accepted

## Context

The `ic` CLI grew organically from a thin psql wrapper to a 5400-line monolithic bash script. An antipattern audit identified 7 systemic issues:

- 14 inline Python heredoc blocks (~2000 lines of untestable, unlintable Python inside bash)
- 138 scattered SQL queries with 9x duplication of the `latest_builds` CTE
- 6 god functions exceeding 200 lines each (largest: `cmd_get_alerts` at 393 lines)
- Copy-pasted logic across 3 alert command variants and 3 number-resolution blocks
- Hard-coded credentials (`PGPASSWORD="admin"`), URLs, and version strings in function bodies
- 173 `2>/dev/null` silent error suppressions with no `set -o pipefail`
- No formatting helpers despite 35+ repeated section header banners and 40+ xargs trim patterns

ADR-001 established that collectors should be Python, but noted the `ic` CLI "remains bash because it's a thin query layer." That assumption no longer holds — the CLI now contains substantial data transformation, AI analysis orchestration, and business logic that violates the "bash is glue" principle from STYLE.md.

## Decision

Refactor the `ic` CLI incrementally, following these principles:

1. **Extract Python into standalone scripts** — Move all `python3 -c "..."` blocks into `lib/*.py` files. Each script reads stdin/args, writes stdout. Bash calls `python3 "$SCRIPT_DIR/lib/some_helper.py"`.

2. **Consolidate SQL into named query functions** — Create `lib/queries.sh` with functions like `sql_latest_failing_builds()`, `sql_count_unresolved()`. Each CTE is defined once. All bash files source this.

3. **Extract formatting helpers** — Create `lib/format.sh` with `section_header()`, `trim()`, `color()`. Source from `ic`.

4. **Decompose god functions** — Break functions >80 lines into fetch/display/logic sub-functions. The three alert variants (`cmd_get_alerts`, `cmd_get_alerts_on_date`, `cmd_get_alerts_range`) share a common implementation parameterized by date filter.

5. **Centralize configuration** — Move all hard-coded values to `lib/config.sh` with `${VAR:=default}` pattern. Credentials load from `.env` only.

6. **Fix error handling** — Add `set -o pipefail`. Replace blanket `2>/dev/null` with proper error checks. Use `return 1` (not `exit 1`) in functions.

## Consequences

- **Positive:** Python blocks get linting, testing, IDE support
- **Positive:** SQL changes happen in one place instead of 9
- **Positive:** Functions become small enough to read and modify safely
- **Positive:** New contributors can find and understand query logic
- **Negative:** More files to navigate (but each is focused and greppable)
- **Negative:** Migration is incremental — mixed old/new patterns during transition
- **Constraint:** Every phase must leave `ic` fully functional (no big-bang rewrite)
