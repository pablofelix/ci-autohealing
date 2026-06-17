# ADR-002: No Message Queue — PostgreSQL as Implicit Job Queue

**Date:** 2026-06-17
**Status:** Accepted

## Decision

Use PostgreSQL polling (`WHERE ai_analyzed = FALSE`) as the job queue instead of Redis, RabbitMQ, or Kafka.

## Context

The worker pipeline has 10 sequential steps (collect → analyze → fix → verify). Each step queries the DB for pending work. At current scale (~70 components), this is fast enough.

## Rationale

- No additional infrastructure (broker, persistence, monitoring)
- Sequential pipeline avoids data races between steps
- PostgreSQL `FOR UPDATE SKIP LOCKED` available if we need multi-worker in future
- Current pending queue: <100 items, query takes <50ms

## When to Revisit

- **>500 components** — polling query becomes slow
- **Analysis latency matters** — need parallel analyzers (would use `SKIP LOCKED`)
- **Complex workflows** — branching/retry logic needs a proper queue
- **Multi-cluster** — need cross-cluster event coordination
