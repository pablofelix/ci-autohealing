# CI Auto-Healing — Project Instructions

## Test Results

At session start, check `.test-results/last-run.txt` and `.test-results/coverage.json`
for the latest test results. If tests are failing, prioritize fixing them before
starting new work.

Run tests with: `python3 -m pytest src/tests/ -x -q --tb=short`

## Quality Rules

- All new code must have tests. Write tests before implementation (TDD).
- All bash commands in `ic` must go through the API (`curl` to endpoints), not `oc` directly.
- MCP tools must use API endpoints, not import Python modules directly.
- Never default to EC policy exceptions — fix root cause instead.
- Always use feature branches + PRs, never push directly to main.
- Use `ic` to test; if `ic` can't do something, improve `ic`.

## Test Infrastructure

- **Lefthook hooks**: pre-commit (lint + unit tests), post-commit (coverage, background), post-merge (full suite), pre-push (doc check + oc guard)
- **Taskfile targets**: `task test-unit`, `task test-full`, `task coverage`, `task coverage-check`
- **Watchdog**: `task test-watchdog` starts continuous testing on file changes
- **Attestation**: Post-commit writes `.commit-attestations/{hash}.json` with quality metadata

## Running Tests

```bash
task test-unit          # Fast unit tests (<30s)
task test-security      # Security-focused tests
task test-integration   # Tests needing DB/API
task test-e2e           # Gherkin BDD scenarios
task test-full          # All tests + coverage report
task coverage           # Coverage summary
task coverage-check     # Fail if coverage < 40%
```

## Style

See `STYLE.md` for coding conventions (SOLID, naming, patterns).
