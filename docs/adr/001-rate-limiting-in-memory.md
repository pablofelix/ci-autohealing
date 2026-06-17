# ADR-001: In-Memory Rate Limiting

**Date:** 2026-06-17
**Status:** Accepted
**Decision:** Use in-memory token bucket rate limiter instead of shared state (Redis/PostgreSQL).

## Context

The API server runs with 2 replicas on OpenShift. We need rate limiting to prevent abuse and runaway MCP agents from overwhelming the server.

Options considered:
1. **In-memory token bucket** (chosen) — each pod maintains its own counter
2. **Redis-based** — shared counter across pods, requires Redis StatefulSet
3. **PostgreSQL-based** — shared counter via existing DB, adds 1 query per request

## Decision

In-memory rate limiter with 100 req/min for reads and 10 req/min for writes per API key.

## Rationale

- **<10 users** — current user base is the DevTestOps team (3-5 people)
- **2 replicas** — effective limit is 2x configured (200 read, 20 write) which is acceptable
- **No new infrastructure** — Redis would add another pod, PVC, and maintenance burden
- **PostgreSQL option adds latency** — 1 DB query per request is wasteful at this scale
- **Monitoring in place** — rate limit hits are logged with key, method, and path (`api.rate_limit` logger)

## Consequences

- Each pod has independent counters — a user could theoretically make 200 req/min by hitting both pods
- Pod restart resets counters — brief window of no limiting after deploy
- Not suitable for >4 replicas or >20 concurrent users

## When to Revisit

Migrate to shared state (PostgreSQL counter or Redis) when ANY of these conditions are true:

- **API replicas > 2** — effective limit multiplies too much
- **Users > 10** — more contention, higher risk of legitimate blocking
- **Sustained 429 responses in logs** — `oc logs deployment/ci-autohealing-api | grep "Rate limit hit"` shows real users being blocked, not just abuse
- **Multi-tenant deployment** — different teams need different rate limits
- **External access** — API exposed beyond VPN

Check command:
```bash
oc logs deployment/ci-autohealing-api -n rhai-devtestops--ci-autohealing --since=24h | grep -c "Rate limit hit"
```

If count is >50/day from legitimate keys, it's time to migrate.

## Implementation

File: `src/api/rate_limit.py`
Pattern: Token bucket with sliding window (60 seconds)
Tests: Covered by integration tests (429 response validation)
