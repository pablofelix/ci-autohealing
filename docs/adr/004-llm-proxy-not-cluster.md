# ADR-004: LLM Runs on User's Machine, Not in Cluster

**Date:** 2026-06-17
**Status:** Accepted

## Decision

AI analysis calls Vertex AI / Anthropic API using the user's local credentials. The cluster does not have LLM credentials. Results are uploaded to the cluster via `POST /analyses`.

## Context

The cluster runs on OpenShift without GPU nodes. Vertex AI requires GCP credentials (ADC) that expire and need refresh. Putting a GCP SA key in the cluster is possible but adds a secret to manage.

## Rationale

- No GCP credentials on the cluster — reduced attack surface
- Users already have Vertex AI access via bashrc (`ANTHROPIC_VERTEX_PROJECT_ID`)
- Analysis quality is the same — same model, same prompt
- When the cluster gets its own API key, the worker does it automatically (no code change)
- `ic ai analyze` works the same in local and cluster mode — transparent

## When to Revisit

- **GCP SA key available** — set `ANTHROPIC_API_KEY` or `ANTHROPIC_VERTEX_PROJECT_ID` in cluster secret, worker auto-analyzes
- **Latency matters** — user's machine adds network hop
- **Compliance** — data must not leave the cluster
