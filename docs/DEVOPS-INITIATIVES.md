# DevOps Initiatives — RHOAI CI/CD Infrastructure

Opportunities identified from the system map analysis (Phase 1) and operational experience.

## 1. Test Cluster / Sandbox Environment

**Impact**: High | **Effort**: Medium

- Dedicated namespace or sandbox cluster for testing infrastructure changes before production
- "Canary" application (`rhoai-v3-5-test`) with 3-4 representative components instead of 100+
- Test EC policy changes, build pipeline modifications, nudge chain flows safely
- Prevents incidents like Go 1.26 upgrade hitting wrong branch, SA missing issues
- Could use the existing Konflux stone-prod with a separate tenant config

## 2. Observability

**Impact**: High | **Effort**: Medium

Current blind spots:
- No alerting on stale components — webhooks can silently fail unnoticed until release readiness
- No build duration tracking — degrading build times undetected until timeout
- Nightly build dashboard is manual (Grafana board requires manual checking)

Improvements:
- Prometheus/Grafana stack scraping IC metrics + Konflux PipelineRun durations
- SLOs: build success rate >95%, time-to-fix <24h, nudge propagation <2h
- Automated alerts on stale components (>24h without build after push)
- Build duration regression detection (p95 exceeding baseline by >2x)

## 3. Security Hardening

**Impact**: High | **Effort**: Low-Medium

- **FIPS compliance automation** — pre-build FIPS validation step to catch failures before EC policy (v3.5-ea.1 had systemic fips-check failures)
- **Secret rotation** — several components use static SA tokens; rotate via CronJob or Vault
- **Image signing verification** — faster pre-release gate to reduce feedback loop (currently only EC policy)
- **Supply chain attestation** — SLSA Level 3 achievable with Tekton Chains (already in Konflux), needs per-component config
- **Dependency scanning** — automated CVE alerts on base image updates

## 4. Build Performance

**Impact**: Medium | **Effort**: Low

- **Build cache optimization** — many components rebuild from scratch; proper layer caching could cut times 50%+
- **Parallel integration tests** — ITS scenarios run sequentially where parallel is possible
- **PCC cache auto-regeneration** — currently manual after releases, causing false conforma violations
- **Resource quota tuning** — identify under/over-provisioned build pipelines

## 5. Auto-Remediation

**Impact**: High | **Effort**: High (Phase 2+ of system map)

- IC detects build failure → analyzes root cause → auto-triggers rebuild for known transient patterns (timeout, flaky)
- Nudge chain breaks → auto-creates missing nudge PR
- Conforma violations covered by exceptions → auto-marks "not blocking" in release readiness
- Webhook failure recovery → auto-re-registers PaC webhooks when stale detected

## 6. Release Process Automation

**Impact**: Medium | **Effort**: Medium

- Automated release readiness reports (daily digest to Slack)
- Release candidate tagging automation
- Post-release PCC cache regeneration trigger
- FBC fragment freshness monitoring with auto-rebuild
- Rollback automation for failed releases

## 7. Documentation as Code

**Impact**: Medium | **Effort**: Low

- Auto-generated architecture docs from system map (Neo4j → Markdown)
- Drift detection between docs and live state (Phase 5 of system map)
- Runbook generation from resolved incident patterns
- Onboarding tutorials backed by system map topology (Phase 6)

## 8. Developer Experience

**Impact**: Medium | **Effort**: Low-Medium

- `ic` CLI improvements for common developer workflows
- Pre-push validation (does my component's pipeline config match the template?)
- Local build simulation (run Tekton pipeline locally before pushing)
- Component onboarding wizard (guided setup via `ic onboard`)
