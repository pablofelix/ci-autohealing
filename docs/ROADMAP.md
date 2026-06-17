# IC Tool — Roadmap

Phased plan for CI AutoHealing (`ic`) improvements. Each phase builds on the previous one.

---

## Phase 1: Incident Response (done)

Features added after the June 4-5 Slack incident analysis. Goal: reduce the time from "something broke" to "here's the root cause, owner, and fix PR."

| Feature | CLI | MCP | API | Status |
|---------|-----|-----|-----|--------|
| Enriched Slack export (repo, branch, PRs, duration, contacts) | `ic export <comp> slack` | `export_slack()` | `POST /exports` | done |
| Image/digest lookup | `ic lookup <image>` | `lookup_image()` | `GET /lookup?image=` | done |
| Nightly build status + blockers | `ic nightly [app]` | `get_nightly_status()` | `GET /applications/{app}/nightly` | done |
| Open PR enrichment in AI analysis | — | — | — | done |
| Multi-app cron scanning | `check_sync_status.py` | — | — | done |

---

## Phase 1b: Triage Automation (done)

Improvements to triage tracking after the initial incident response workflow. Goal: reduce manual bookkeeping — auto-resolve, multi-tracking, Jira sync.

| Feature | CLI | MCP | API | Status |
|---------|-----|-----|-----|--------|
| Multi-Slack URLs (append, never overwrite) | `ic triage update <id> --slack URL` | `update_triage_item()` | `PUT /triage/{id}` | done |
| Auto-resolve triage items when all builds pass | StatusSynchronizer cascade | — | — | done |
| Resolve by ID, component, or Jira key | `ic triage resolve <target>` | — | `POST /triage/{id}/resolve` | done |
| Jira comment + transition on resolve | `ic triage resolve <id>` (interactive) | — | — | done |
| Open Jiras section in triage dashboard | `ic triage show` | — | — | done |
| Resolution build lookup from cluster | `_find_resolution_build()` | — | — | done |

---

## Phase 2: API Independence & Deployment (done)

Goal: deploy the API layer on an OpenShift cluster, independent of the CLI.

- [x] Audit all API routes — verify none import from `cli.*` (some currently import `cli.config`)
- [x] Extract shared config (APPLICATION_NAME, NAMESPACE, KONFLUX_UI_BASE, KUBEARCHIVE_URL) to `src/shared_config.py`
- [x] Containerize the API (Dockerfile, health check endpoint, readiness probe)
- [x] OpenShift deployment: PostgreSQL StatefulSet, MinIO, API (2 replicas), Worker, Watcher
- [x] GitOps: Kustomize manifests in separate deploy repo (gitlab.cee.redhat.com/pestevez/ci-autohealing-deploy)
- [x] Secrets from Vault (rhoai-monitoring SA token, rhods-ci-bot GitHub, devtestops-jira-bot)
- [x] Image on Quay.io (quay.io/pestevez_rhoai-dev/ci-autohealing)
- [x] NetworkPolicy for OpenShift ingress
- [x] PodDisruptionBudget for API server
- [x] CLI mode switching: `ic config use-cluster` / `ic config use-local`
- [x] Document deployment in `~/ci-autohealing-access.md`

---

## Phase 3: Nightly Build Monitoring & Commit Staleness

Goal: full visibility into the nightly build chain, and proactive detection of components building from stale code.

**Commit staleness detection:**
- [x] Compare `lastBuiltCommit` (from cluster Component CR) against branch HEAD (via GitHub API) for each component
- [x] Flag components where the branch has newer commits that never triggered a build — "stale by N commits / N days"
- [x] Surface stale components in `ic get alerts` and `ic nightly` as a new warning type (`commit_staleness`)
- [x] Diagnose *why* the build wasn't triggered: missing webhook, nudge misconfiguration, build quota
- [x] New command: `ic stale [app]` — list all components with untriggered commits
- [x] TTL disk cache for GitHub ref SHAs (`~/.ic/cache/ref-shas.json`, 5 min default)

**Nightly build chain tracking:**
- [x] Add `ic nightly-history [app]` — timeline of recent nightly outcomes
- [x] Alerting: surface broken nightly in `ic get alerts` and MCP `list_alerts()`
- [ ] Monitor GitHub Actions trigger in `rhods-devops-infra` (nightly cron workflow status)
- [ ] Track the full chain: GHA trigger → validator tests → operator build → FBC fragment
- [ ] Parse `#rhoai-build-notifications` Slack messages for build success/failure signals

---

## Phase 4: Skill Execution & Composition (done)

Goal: run skills as automated workflows, not just lookup/reference.

- [x] `ic skills run <name>` with dry-run, risk classification (low/medium/high), and confirmation
- [x] Risk assessment with detailed reasons (write tools, secret env vars, security findings)
- [x] MCP tool `run_skill()` — Claude can execute skills (dry_run=True by default)
- [x] API endpoint `POST /skills/{name}/run` + `GET /skills/runs` history
- [x] Container sandbox: high-risk skills execute in ephemeral K8s Jobs
- [x] Execution history tracked in `skill_runs` table
- [x] Worker pipeline auto-execution: skills tagged `auto-heal` run hourly
- [x] Error pattern → skill linking: `ic patterns link-skill <pattern> <skill>`
- [x] Code block extraction from SKILL.md with `# ic:skip` / `# example` markers
- [x] 12 unit tests for executor, risk classification, code block parsing

---

## Phase 5: Self-Healing Loop

Goal: close the loop from detection → diagnosis → fix → verification automatically for high-confidence failures.

- [ ] Auto-retrigger builds for transient failures (network timeouts, flaky infra)
- [ ] Auto-generate fix PRs for high-confidence AI diagnoses (dependency bumps, base image updates)
- [ ] Post-fix verification: monitor the next build after a fix PR merges
- [ ] Confidence thresholds: only auto-act above configurable confidence (e.g., 90%+)
- [ ] Audit trail: log every automated action with reasoning for human review

---

## Phase 6: Observability & Metrics

Goal: dashboards and trends, not just point-in-time snapshots.

- [ ] Build health trends over time (success rate charts per component)
- [ ] Mean time to resolution (MTTR) tracking per failure category
- [ ] AI analysis accuracy tracking (was the diagnosis correct?)
- [ ] Failure pattern frequency trends (are certain patterns increasing?)
- [ ] Grafana dashboard or equivalent for oncall visibility

---

## Phase 7: CLI Cluster Mode Parity (mostly done)

Goal: all CLI commands work identically in local and cluster mode.

- [x] `ic get alerts` — enriched with violation counts, policy links, release countdown
- [x] `ic get apps` — works via API
- [x] `ic get component` / `ic describe component` — full detail + build history via API
- [x] `ic get conforma` / `ic describe conforma` — violations with counts via API
- [x] `ic get pipelineruns` / `ic get pipelinerun` — via API
- [x] `ic get releases` — release schedule via API
- [x] `ic get jira` — jira links via API
- [x] `ic get fixes` — fix attempts via API
- [x] `ic export` — slack/jira export via API
- [x] `ic triage` — dashboard via API
- [x] `ic stats` — overview stats via API
- [x] `ic conforma report` — via API
- [x] `ic config watch add/list` — runtime config via API + DB
- [ ] Remaining 33 bash-only commands (see Phase 9c)

---

## Phase 8: CI/CD Pipeline

Goal: automated image builds and deployments.

- [ ] GitHub Actions or Tekton pipeline: build + push image on merge to develop
- [ ] ArgoCD auto-sync from deploy repo
- [ ] Kustomize overlays for dev/staging/prod
- [ ] Vault integration: replace manual secrets with VaultStaticSecret CRs

---

## Phase 9: ic as Pure API Client

Goal: eliminate all direct DB access from CLI. `ic` becomes a pure REST API client. Local mode = API on localhost:8000, cluster mode = API on remote URL.

**9a — Error handling (done)**
- [x] Structured API errors with error code, detail, suggestion
- [x] CLI renders suggestions from API error responses
- [x] FastAPI exception handler for ICError

**9b — Input validation (done)**
- [x] Validators for app names, component names, jira keys, skill names
- [x] Rejects empty/invalid input with clear suggestions

**9c — Eliminate bash fallback (33 commands)**
- [ ] Migrate conforma report/categories/csv/scenarios/snapshot commands
- [ ] Migrate jira create/link/status/inbox commands
- [ ] Migrate ai analyze/batch/stats commands
- [ ] Migrate release status/verify/diff commands
- [ ] Migrate fix (interactive) command
- [ ] Migrate report build/conforma commands
- [ ] Local mode = `api_url: http://localhost:8000` (user runs `task serve`)
- [ ] Remove `_bash_fallback()` function entirely

**9d — MCP-CLI parity**
- [ ] Every CLI command has an equivalent MCP tool
- [ ] All MCP tools call the same API endpoints as CLI
- [ ] Add missing: conforma_report(), triage_track/resolve(), release_readiness()

**9e — Input sanitization and UX**
- [ ] Fuzzy matching for component/app names ("did you mean X?")
- [ ] Autocomplete suggestions from DB for invalid names
- [ ] Consistent error formatting across all commands
- [ ] Progress indicators for long operations
- [ ] Automated DB migrations on deploy (init container or Job)

---

## Principles

- **Three layers stay aligned**: every feature ships in CLI + MCP + API
- **Business logic in shared services**: repositories, HealthMonitor, analyzers — never in interface layers
- **API is independently deployable**: no CLI dependency, suitable for OpenShift
- **Upstream-first fixes**: don't fix component deps directly on rhoai-* branches
- **Human approval for actions**: never auto-merge PRs or auto-send Jira/Slack without confirmation
