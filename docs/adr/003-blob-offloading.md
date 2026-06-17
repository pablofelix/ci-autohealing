# ADR-003: Blob Offloading — Large Data to MinIO at 50KB Threshold

**Date:** 2026-06-17
**Status:** Accepted

## Decision

Build logs and commit context >50KB are stored in MinIO (S3-compatible) instead of PostgreSQL. A `blob_refs` JSONB column stores the key. Resolution is transparent — callers see inline data.

## Context

Build logs average 100-200KB. Storing all of them in PostgreSQL would grow the DB to several GB quickly. MinIO provides cheap blob storage with S3 API.

## Rationale

- 50KB threshold keeps small logs queryable in PostgreSQL (`WHERE build_logs LIKE '%error%'`)
- MinIO is the same image for dev (docker-compose) and prod (OpenShift pod)
- Local filesystem backend for dev without MinIO (`BLOB_STORE=local`)
- `resolve_blob_fields()` makes offloading invisible to callers

## When to Revisit

- **Need full-text search across all logs** — add Elasticsearch or PostgreSQL FTS
- **Storage cost** — archive old blobs to cheaper tier (S3 Glacier)
- **Performance** — if blob resolution adds latency to hot paths
