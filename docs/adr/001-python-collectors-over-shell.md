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
- **Note:** The `ic` CLI remains bash because it's a thin query layer over psql — moving it to Python would add startup latency for no benefit
