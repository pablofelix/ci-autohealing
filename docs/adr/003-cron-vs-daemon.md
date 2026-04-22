# ADR-003: Cron Schedule vs Daemon Process

**Date:** 2026-04-21
**Status:** Accepted

## Context

We need to periodically collect failure data from KubeArchive and the live cluster. Options: a long-running daemon process, or a cron job.

## Decision

Use cron (`*/20 * * * *`) running `collect-comprehensive.sh`, which orchestrates 5 Python collectors sequentially. The cron script sets `PATH` to include `~/.local/bin` for `oc`.

## Why 20 minutes

- Grafana's monitoring check runs hourly — 20min keeps us fresher than Grafana
- The full collection run takes ~2-3 minutes — 20min gives ample margin
- KubeArchive data doesn't change faster than builds complete (~10-30min per build)
- No benefit to polling more frequently

## Consequences

- **Positive:** Simple, no process management, survives reboots via crontab
- **Positive:** Each run is independent — a crash doesn't affect the next run
- **Negative:** Up to 20min staleness between checks
- **Negative:** Cron environment has minimal PATH — must explicitly set it
