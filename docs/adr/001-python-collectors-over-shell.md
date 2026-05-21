# ADR-001: Python Collectors Over Shell Scripts

**Date:** 2026-04-17
**Status:** Accepted

## Context

The initial implementation used bash shell scripts for collecting PipelineRun data from Kubernetes. As complexity grew (pagination, multi-API fallback, JSON parsing, database operations), shell scripts became hard to maintain and debug.

## Decision

Replace all shell-based collectors with Python modules under `src/`. Shell scripts in `collectors/` are deprecated and removed.

## Consequences

- **Positive:** Structured error handling, testable code, reusable clients (KubeArchiveClient, KubernetesClient), type hints
- **Positive:** Database operations via psycopg2 instead of piping through docker exec psql
- **Negative:** Requires Python 3.6+ and pip dependencies on the host
- **Note:** The `ic` CLI has since evolved into a hybrid: a bash wrapper (`ic`) delegates to Python (`ic.py` → `src/cli/main.py`) for complex operations, keeping bash only for simple queries and formatting. See ADR-005.
