# ADR-004: Conforma Collection as Separate Pipeline

**Date:** 2026-04-21
**Status:** Accepted

## Context

Conforma (Enterprise Contract) tests are fundamentally different from builds: they are policy compliance checks that run after successful builds. They produce structured violation data (JSON with specific policy names, severity, solutions) rather than build logs.

## Decision

Keep Conforma collection separate from build failure collection with dedicated modules:
- `check_conforma_status.py` — discovers failing components (cached in `sync_status.conforma_components`)
- `collect_conforma.py` — fetches violation details from the `verify` TaskRun's pod logs

Both share the same `KubeArchiveClient` and query the same PipelineRun API, but filter by `pipelines.appstudio.openshift.io/type=test` and `test.appstudio.openshift.io/scenario=conforma-*`.

## Data Model

Conforma results stored in `conforma_results` table with:
- Counts (violations, warnings, successes)
- `violation_summary` (human-readable from `step-detailed-report`)
- `violation_details` (full JSON from `step-report-json`)

## Consequences

- **Positive:** Conforma logic doesn't complicate the build collector
- **Positive:** Can evolve Conforma fix strategies independently (policy fixes vs code fixes)
- **Positive:** Separate display in CLI (`ic get conforma`, `ic describe conforma`)
- **Negative:** Two sets of KubeArchive queries per cron run (slightly more API load)
