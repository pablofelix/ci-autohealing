# ADR-002: KubeArchive as Primary Data Source

**Date:** 2026-04-17
**Status:** Accepted

## Context

Kubernetes only retains PipelineRuns for a limited time (~48h). Logs from completed pods are deleted even faster. We need a source for historical PipelineRun data.

## Decision

Use KubeArchive API as the primary data source for PipelineRun metadata. Supplement with live cluster queries (`oc get`) for very recent data not yet archived, and `oc logs` for pods still running. Authentication uses `oc whoami -t` to get an OpenShift token.

## Multi-API Fallback Strategy

1. **KubeArchive** (primary) — archived PipelineRuns, paginated API
2. **Live cluster** (`oc get`) — recent PipelineRuns still in etcd
3. **Active pods** (`oc logs`) — logs from still-running pods

The `UnifiedCollector` class tries each source in order.

## Consequences

- **Positive:** Access to full PipelineRun history beyond cluster retention
- **Positive:** Pod logs captured from KubeArchive when available
- **Negative:** KubeArchive API can be slow (~2-3s per page of 500 items)
- **Negative:** Requires active `oc` session for auth token
