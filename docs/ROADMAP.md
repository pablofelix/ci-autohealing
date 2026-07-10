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

## Phase 4b: Agent Execution Mode

Goal: skills with multi-step workflows (like `conforma-analyze`) execute via an AI agent that follows SKILL.md instructions step-by-step, with sandboxed command execution.

**4b-1 — Foundation (Tools + Sandbox)**
- [ ] `agent_config.py`: frozen dataclass with max_turns, max_cost, timeout, sandbox_timeout
- [ ] `agent_tools.py`: ToolRegistry with handler pattern (bash_execute, python_execute, read_file, write_file, ask_user)
- [ ] `agent_sandbox.py`: ToolSandbox ABC + LocalSandbox (subprocess with `get_safe_env()` filtering)
- [ ] `agent_state.py`: conversation history, variables, turn count, token/cost tracking

**4b-2 — Multi-turn LLM + Agent Executor**
- [ ] Extend `LLMProvider` with `create_conversation()` for multi-turn tool_use
- [ ] Implement in `AnthropicDirectProvider` and `VertexAIProvider`
- [ ] `agent_executor.py`: agent loop — read SKILL.md as prompt, iterate with tool_use, execute via sandbox
- [ ] Streaming output: agent text + command results to stdout in real-time
- [ ] Safety limits: max iterations, total timeout, cost ceiling, output redaction

**4b-3 — CLI Integration + Auto-detection**
- [ ] `type_detector.py`: detect skill type from SKILL.md content (routing/workflow/script/hybrid)
- [ ] `execution_mode` field in SkillMetadata frontmatter
- [ ] `--agent` flag on `ic skills run` (or auto-detect from metadata/content)
- [ ] `--interactive` flag: agent pauses for user confirmation before tool calls
- [ ] `--max-turns N`, `--max-cost DOLLARS` safety flags

**4b-4 — Interaction Modes**
- [ ] Autonomous: pre-configured params, no user interaction (default)
- [ ] Interactive: `--interactive` pauses for confirmation before tool calls
- [ ] Semi-autonomous: `--param key=value` pre-fills known answers

**4b-5 — K8s Sandbox (future)**
- [ ] `K8sSandbox` implementing `ToolSandbox` — execute in ephemeral K8s pod
- [ ] Route high-risk agent skills to K8s sandbox based on risk assessment
- [ ] `IcMetadata.sandbox` dict configuration (image, namespace, resources)

---

## Phase 5: Self-Healing Loop

Goal: close the loop from detection → diagnosis → fix → verification automatically for high-confidence failures.

**5a — Fixer Sandbox**
- [ ] Fixer runs in isolated environment (container or git worktree)
- [ ] File allowlist: only modify permitted file types per repo
- [ ] Diff size limits: max lines changed, max files
- [ ] Never-modify list: `.github/workflows/`, `OWNERS`, `LICENSE`, CI config
- [ ] Branch naming enforcement: `ci-autohealing/fix-*`

**5b — Verification Agent**
- [ ] Pre-merge checks: diff scope validation, file allowlist, size limits
- [ ] Safety scoring (0-100): files scope + diff size + pattern match + CI status
- [ ] Post-merge verification: monitor next build, detect regressions
- [ ] Auto-revert: if next build fails AND the fix PR is the cause, revert automatically
- [ ] Confidence calibration: adjust AI confidence based on actual fix outcomes

**5c — Autonomous Mode (requires 5a + 5b)**
- [ ] Auto-retrigger builds for transient failures (network timeouts, flaky infra)
- [ ] Auto-generate fix PRs for high-confidence diagnoses (dependency bumps, base image updates)
- [ ] PRs created as drafts by default — auto-merge only if safety score > 90
- [ ] Confidence thresholds: only auto-act above configurable confidence (e.g., 95%+)
- [ ] Rate limits: max N PRs per hour, per repo
- [ ] Repo allowlist: only repos explicitly enabled for auto-fix
- [ ] Audit trail: log every automated action with reasoning for human review
- [ ] Time-limited activation: AUTONOMOUS_MODE auto-deactivates after N hours

---

## Phase 6: Observability & Metrics

Goal: dashboards and trends, not just point-in-time snapshots.

**6a — AI Quality Metrics (see `docs/design/ai-quality-metrics.md`)**
- [x] Human feedback loop: auto-verdict via VerdictCorrelator when build passes post-analysis
- [x] `human_verdict` column in ai_analysis table
- [x] classification_accuracy, auto_fix_accuracy — via `ic ai regression`
- [x] avg_cost_per_analysis — via `ic dashboard` (cost summary)
- [x] API endpoint: `/api/v1/metrics/ai-quality`
- [x] `ic ai quality` command — accuracy from verdicts + by_category breakdown
- [x] Confidence calibration analysis — `ic ai quality --calibration` + CalibrationService
- [x] Training data export — Phase 17a: `ic ai label-training-data --backfill`
- [ ] cost_per_correct_diagnosis metric (cost_usd filtered by correct verdicts)
- [ ] Weekly accuracy trend report — show how accuracy changes over time

**6a2 — Confidence Calibration & Scientific Error Analysis (requires 6a)**
- [ ] Calibration curve: when AI says 0.90, how often is it correct? Plot predicted vs actual
- [ ] Bayesian priors per failure category from historical verdicts
- [ ] Feature-based scoring: replace subjective LLM float with calculated score from boolean features (has_build_history, sha_mismatch_confirmed, nudging_checked, etc.)
- [ ] Confidence cap refinement: tune penalties based on actual miss rates per missing source
- [x] Calibration dashboard in `ic ai quality --calibration` (`_show_calibration_dashboard`)
- [ ] Differential diagnosis: prompt LLM to generate 2-3 competing hypotheses ranked by evidence strength before selecting root cause
- [ ] Evidence hierarchy classification: weight evidence sources (commit diff > build log > config > error message) and score evidence coverage
- [ ] Fault Tree Analysis: structured decomposition of complex failures into contributing factors (multi-cause failures)
- [x] Context-aware confidence cap: penalize confidence when enrichment sources unavailable (`_apply_confidence_cap`)
- [x] Fix verification: prompt instructions + post-analysis annotation when fixes are already in progress

**6b — Operational Metrics**
- [ ] Build health trends over time (success rate charts per component)
- [ ] Mean time to resolution (MTTR) tracking per failure category
- [ ] Failure pattern frequency trends (are certain patterns increasing?)
- [ ] Grafana dashboard or equivalent for oncall visibility

**6c — Authentication (when >5 users)**
- [ ] Per-user API keys generated via `ic config create-key <user>`
- [ ] Read-only vs admin roles
- [ ] API request logging with user identity
- [ ] Later: OIDC with Red Hat SSO (when >20 users)

---

## Phase 7: CLI Cluster Mode Parity (done)

Goal: all CLI commands work identically in local and cluster mode.

- [x] `ic get alerts` — enriched with violation counts, policy links, release countdown
- [x] `ic get apps` / `ic get components` — via API
- [x] `ic get component` / `ic describe component` — full detail + build history + filtered logs via API
- [x] `ic get conforma` / `ic describe conforma` — violations with counts via API
- [x] `ic get pipelineruns` / `ic get pipelinerun` / `ic describe pipelinerun` — via API
- [x] `ic get releases` — release schedule with countdown via API
- [x] `ic get jira` / `ic jira status` — jira links via API
- [x] `ic get fixes` — fix attempts via API
- [x] `ic export` — slack/jira export via API
- [x] `ic triage` — dashboard via API
- [x] `ic stats` — overview stats via API
- [x] `ic conforma` default/report/categories/csv — via API
- [x] `ic ai` default/status — via API
- [x] `ic release` default/status — readiness via API
- [x] `ic report` build/conforma — via API
- [x] `ic patterns` default/shared/list — via API
- [x] `ic config watch add/list` — runtime config via API + DB
- [x] Smart log filtering in describe component (error pattern matching like local bash)

---

## Phase 8: CI/CD Pipeline

Goal: automated image builds and deployments + consistency enforcement.

**8a — Automated builds**
- [ ] GitHub Actions: build + push image on merge to develop
- [ ] ArgoCD auto-sync from deploy repo
- [ ] Kustomize overlays for dev/staging/prod
- [ ] Vault integration: replace manual secrets with VaultStaticSecret CRs
- [ ] Auto-publish to PyPI on version tag

**8b — Consistency enforcement (CI checks)**
- [ ] MCP-API-CLI parity check: every API endpoint has an MCP tool and CLI command
- [ ] Documentation sync: verify tool count, test count, env vars in README match reality
- [ ] STYLE.md compliance as CI check (not just pre-commit)
- [ ] Integration test suite runs against staging cluster on merge
- [ ] Auto-update README numbers (tools, tests) on CI pass

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

**9c — Eliminate bash fallback (done)**
- [x] All commands migrated to API path in cluster mode
- [x] ai analyze/batch — local LLM proxy with cluster storage
- [x] jira create/inbox — via API or export fallback
- [x] release diff/verify — clear message when cluster-only access needed
- [x] fix — menu of available actions via API
- [x] get exceptions/bindings — via external service proxy API
- [x] Local mode = auto-detect `localhost:8000` API
- [x] `_bash_fallback()` retained only as legacy fallback when no API available

**9d — MCP-CLI parity (done)**
- [x] 52 MCP tools matching all CLI read + write commands
- [x] resolve_triage_item(), get_conforma_categories() added
- [x] All MCP tools use same API endpoints as CLI

**9e — Input sanitization and UX (done)**
- [x] Fuzzy matching for component names (Levenshtein distance)
- [x] "Did you mean X?" suggestions on invalid names
- [x] STYLE.md pre-commit hook (frozen dataclass, no bare except, no print in lib)
- [x] cli/mode.py: ensure_cluster() eliminates 31 repeated mode-check blocks (DRY)
- [x] cli/log_filter.py: smart error filtering for build logs (error patterns + context)
- [x] Autocomplete suggestions from DB for invalid names — `cli/completions.py`
- [x] Progress indicators for long operations — `cli/progress.py` (animated spinner)

---

## Phase S1: Critical Security Hardening

Goal: prevent the most damaging attacks. See `docs/THREAT_MODEL.md` for full analysis.

**S1a — Log and output security (done)**
- [x] Secret scanning/redaction in stored logs and AI output (T2) — `security/redact.py`, 12 regex patterns
- [x] Sanitize AI-generated content before Jira/Slack posting (T5) — `_sanitize_for_jira()`, `_sanitize_for_slack()`
- [x] Strip ANSI escape codes from stored logs (T1) — `strip_ansi()`, `sanitize_for_storage()`
- [x] LLM prompt injection protection: canary tokens + output validation (T15) — `security/llm_guard.py`

**S1b — Skill execution security (partial)**
- [x] Command audit log: wrap subprocess to log every command before execution (T7) — `executor.py` logs command, args, exit code
- [x] Secret masking: only pass env vars declared in requires-env, strip undeclared tokens (T7) — `get_safe_env()`
- [x] Execution budget: timeout + max output size per skill (T7) — `subprocess.TimeoutExpired` handling
- [ ] Pin external skills to SHA — require approval before first run (T7)
- [ ] Sandbox ALL skill execution by default (not just high-risk) (T7)

**S1c — Autonomous action limits (partial)**
- [x] AUTONOMOUS_MODE activation controls with confirmation (T6) — `security/autonomous.py`, 24h time limit
- [ ] Autonomous PR scope limits: repo allowlist, file allowlist, branch naming (T3, T8)
- [ ] GitHub API call audit logging (T4)
- [ ] Never-modify file list for auto-fix PRs (T8)

**S1d — API hardening (done)**
- [x] Basic rate limiting: 100 req/min read, 10 req/min write (T11) — `api/rate_limit.py`
- [x] Config file permissions: ~/.ic/config.json mode 600 on creation (T12) — `ic_config.py`

## Phase S2: Defense in Depth

**S2a — Access control**
- [ ] Per-user API keys with RBAC (T9)
- [ ] MCP tool-level permissions: read vs write tools (T16)
- [ ] Token rotation via Vault (T4)

**S2b — Skill isolation**
- [ ] Network sandbox for skill pods: restrict egress to declared endpoints (T7)
- [ ] Read-only filesystem for skills: can only write to /tmp (T7)
- [ ] Skill approval workflow: `ic skills approve` for external sources (T7)
- [ ] Cost tracking: capture LLM API calls made by skills (T7)

**S2c — Operational safety**
- [ ] Rate limits on Jira/PR creation (T3, T5)
- [ ] Time-limited AUTONOMOUS_MODE (T6)
- [ ] PR dry-run mode — draft PRs by default (T3)
- [ ] Worker/watcher health alerts: notify if no heartbeat in 30 min (T13)
- [ ] Stale data detection: alert if no scans in 2 hours (T13)
- [ ] `ic health` command: system component status (T13)

**S2d — Data management**
- [ ] Retention policy: archive >90 days, purge >180 days (T14)
- [ ] PostgreSQL backup CronJob to MinIO (T14)
- [ ] `ic export-data` for backup/audit (T17)

## Phase S3: Long-term Hardening

- [ ] OIDC integration for API auth (T9)
- [ ] Skill content hash verification: detect tampering (T7)
- [ ] DB column encryption for sensitive data (T10)
- [ ] Full API audit trail with user identity (T9)
- [ ] Backup encryption + off-cluster storage (T14)
- [ ] Multi-tenant data isolation (T18)
- [ ] Per-agent MCP tool allowlists (T16)
- [ ] `ic import-data` for migration between instances (T17)

---

## Phase 10: Conforma Reporter Integration

Goal: seamless access to stage/nightly conforma violations from the reporter repo, without leaving ic.

| Feature | CLI | MCP | API | Status |
|---------|-----|-----|-----|--------|
| `--stage` flag for stage policy violations | `ic get conforma --stage` | `get_conforma_report(reporter_env='stage')` | `?reporter_env=stage` | done |
| `--nightly` flag for nightly build violations | `ic get conforma --nightly` | `get_conforma_report(reporter_build_type='nightly')` | `?reporter_build_type=nightly` | done |
| Combined `--stage --nightly` | `ic get conforma --stage --nightly` | Both params | Both params | done |
| Reporter CSV client with 1h file cache | — | — | — | done |
| Stage policies in category map | — | — | — | done |
| All conforma subcommands support flags | `report`, `categories`, `csv` | `get_conforma_categories()` | `/violations`, `/violations/report` | done |
| Group violations by rule + solution | `ic conforma rules` | `get_conforma_rules()` | `/violations/rules` | done |

---

## Phase 11: AI Evidence References & Release Visibility

Goal: AI analyses cite their sources with structured evidence links, and release verify-conforma failures show full violation details.

| Feature | Surface | Status |
|---------|---------|--------|
| Show violated rule names in `ic describe release --logs` | CLI | done |
| Wire `ReleaseFailureAnalyzer` display into `ic describe release` | CLI | done |
| `EvidenceReference` model (type, url, description) | Analyzers | done |
| `evidence_references` in all 3 tool schemas (build, conforma, release) | Analyzers | done |
| Prompt instructions + known doc URLs for evidence generation | Prompts | done |
| Evidence references display in `display_ai_analysis()` | CLI | done |
| Policy URL with line anchors via `conforma_utils.py` | Conforma analyzer | done |
| Neo4j knowledge graph context in all 3 analyzers | `graph_context.py` | done |
| Tekton file name resolution from GitHub API | `conforma_analyzer._get_tekton_files()` | done |
| Source transparency (sources used, unavailable, limitations) | All 3 analyzers + CLI + MCP | done |

---

## Phase 12: Enriched Release Analysis

Goal: the release AI analyzer has the same context as a human engineer doing triage — not just pipeline logs, but build history, SHA tracing, nudging state, application health, and active triage items.

| Feature | Surface | Status |
|---------|---------|--------|
| Violation-driven enrichment: build history + SHA per violated component | Analyzer | done |
| SHA tracing: compare violation SHA vs green build SHA, flag mismatch | Analyzer | done |
| Build failure details for failed components (failed TaskRun + error) | Analyzer | done |
| operator-nudging.yaml fetch and display (configurable repo/path) | Analyzer | done |
| Nudge PR lookup for violated components | Analyzer | done |
| Application health: working/failing counts, active build failures | Analyzer | done |
| Active conforma violations context | Analyzer | done |
| Active triage items with Jira keys and root causes | Analyzer | done |
| Nightly build history (last 7 days) | Analyzer | done |
| Configurable repos: GITHUB_OPERATOR_OWNER/REPO, OPERATOR_NUDGING_PATH | Config | done |
| Context-aware confidence cap: penalize when enrichment sources unavailable | Analyzer | done |
| System prompt: enriched context instructions + confidence scoring update | Prompt | done |
| Enriched context sections in build_analysis_prompt() | Prompt builder | done |
| TriageRepository exported from repositories package | Repositories | done |

---

## Phase 13: Triage Skills Completion

Goal: complete triage skill coverage for all failure types and make skills educational.

| Feature | Surface | Status |
|---------|---------|--------|
| `/triage-release` skill — 8-step release failure triage | Claude skill | done |
| `/triage` dispatcher routes to release triage | Claude skill | done |
| Release auto-detect in `/triage` (stage/prod name patterns) | Claude skill | done |
| Educational mode: explain Konflux concepts using docs MCP | All triage skills | done |

---

## Phase 14: Product Pages Integration

Goal: replace decommissioned Smartsheet with Product Pages for release schedule data. All RHOAI/RHAI releases are consolidated under Product Pages product `rhai` (id: 3184).

| Feature | Surface | Status |
|---------|---------|--------|
| Product Pages MCP discovery (rhai, rhai-3.5, rhai-3.6) | MCP | done |
| Fetch RHAI 3.5 EA1/EA2/GA schedule milestones | MCP | done |
| `sync_schedule_from_productpages()` — populate release_schedule DB | API | done |
| Replace `_release_fetch_smartsheet_dates()` in bash | CLI | done |
| Product Pages API client with OIDC/Kerberos auth | Client | planned |
| Scheduled sync via worker (daily refresh) | Worker | planned |

**Key data mapping:**
- Product Pages entity: `Red Hat AI` (shortname: `rhai`, id: 3184)
- Releases: `rhai-3.5` (id: 3358), `rhai-3.6` (id: 3365), `rhai-3.4` (id: 3354)
- RHOAI milestones: Code Freeze, Feature Freeze, Initial RC, Release Window, RHOAI Release

---

## Phase 15: Component Onboarding Automation (done)

Goal: track and assist component onboarding into RHOAI Konflux workspace, reducing manual steps and preventing tickets from being prematurely closed.

| Feature | Surface | Status |
|---------|---------|--------|
| Onboarding status tracker (two-phase: Konflux 9 steps + Automation 14 steps) | `ic onboard status` / `get_onboarding_status()` / API | done |
| Onboarding describe with progress bars + next action | `ic onboard describe <comp>` / `get_onboarding_describe()` / API | done |
| Parallel API queries (ThreadPoolExecutor, 8 workers, 30s timeout) | API | done |
| 14 automation steps (was 10): +auto_merge, product_listing, renovate, rkc | API | done |
| Bot error analysis (14 error patterns, timeline, stuck step detection) | API | done |
| Jira ticket cross-reference (RHOAI + ODH onboarding tickets) | API | done |
| Nudge PR tracking (dependency update PRs) | API | done |
| OnboardingAnalyzer — LLM diagnosis of blockers (9 categories) | `ic ai analyze onboarding <comp>` / API | done |
| MCP tool for AI agents | `analyze_onboarding()` | done |
| Triage skill for onboarding | `/triage-onboarding` | done |
| Regression tester against completed onboardings | `ic ai regression onboarding` | done |
| Heuristic blocker analysis + fix suggestions | API | done |

---

## Phase 16: Build & Release AI Quality (in progress)

Goal: apply the same regression testing and config analysis approach that improved the conforma AI analyzer (from 41% to target 80%+ accuracy) to build failures and release failures. Use existing AI triage tracker data as ground truth.

**Context from conforma work (Phase 11-12):**
- ConformaRegressionTester found 41% accuracy, 0.02 calibration, config_error catch-all at 0%
- ConfigAnalyzer found 9 actionable findings at 91% confidence (expired exceptions, disabled ITS scenarios)
- Pattern: resolved violations with AI analysis → compare predicted vs actual → identify weaknesses → improve prompts

### 16a — Build Failure Regression Tester

Test `BuildFailureAnalyzer` predictions against resolved build failures as ground truth.

| Feature | Surface | Status |
|---------|---------|--------|
| `BuildRegressionTester` — compare AI predictions vs actual resolutions | `src/analyzers/build_regression.py` | done |
| Query resolved failures with AI analysis (build_failures + ai_analysis JOIN) | Repository | done |
| Evaluate failure_category accuracy (AI predicted vs resolution pattern) | Analyzer | done |
| Evaluate can_auto_fix accuracy (was it actually auto-fixable?) | Analyzer | done |
| Compute calibration score (confidence vs correctness correlation) | Analyzer | done |
| Coverage gap detection (failure patterns with 0% AI coverage) | Analyzer | done |
| Improvement suggestions (catch-all overuse, low-confidence categories) | Analyzer | done |
| CLI: `ic ai regression build [--app APP] [--limit N]` | CLI | done |
| MCP: `get_regression_report(domain='build')` | MCP tool | done |

**Key metrics to compute:**
- Accuracy by failure_category (dependency_issue, build_config, test_failure, etc.)
- Auto-fix calibration (marked auto-fixable vs actually resolved by rebuild/retry)
- Confidence calibration curve per category
- Coverage: % of resolved failures that had AI analysis

**Ground truth mapping:**
- Resolved failures where `resolution_type = 'rebuild'` or `'retry'` → should have `can_auto_fix=True`
- Failures resolved by PR merge → check if `recommended_fix` mentioned the right fix type
- Failures with multiple AI analyses → compare oldest vs newest for improvement tracking

### 16b — Release Failure Regression Tester

Test `ReleaseFailureAnalyzer` predictions against resolved release failures.

| Feature | Surface | Status |
|---------|---------|--------|
| `ReleaseRegressionTester` — evaluate release analysis quality and completeness | `src/analyzers/release_regression.py` | done |
| Query release analyses from ai_analysis table | Repository | done |
| Evaluate category validity + completeness (affected_images, owner_team, fix_action) | Analyzer | done |
| Track actionable vs transient classification | Analyzer | done |
| Confidence calibration across release categories | Analyzer | done |
| CLI: `ic ai regression release [--limit N]` | CLI | done |
| MCP: `get_regression_report(domain='release')` | MCP tool | done |

**Release-specific metrics:**
- Was the release blocker correctly identified? (wrong component → wasted investigation time)
- SHA mismatch detection accuracy (AI flagged stale SHA that was actually the issue)
- Exception recommendation accuracy (AI suggested exception → was it the right call?)
- Enrichment source impact (analyses WITH build history vs WITHOUT — accuracy delta)

### 16c — Build/Release Config Analyzer

LLM-powered audit of build pipeline and release configuration, similar to `ConfigAnalyzer` for conforma.

| Feature | Surface | Status |
|---------|---------|--------|
| `BuildConfigAnalyzer` — audit build pipeline configuration | `src/analyzers/build_config_analyzer.py` | done |
| Detect stale components (no build triggered despite new commits) | Analyzer | done |
| Detect misconfigured nudging (component in nudging.yaml but builds failing) | Analyzer | done |
| Detect quota issues (build stuck in pending > 30 min) | Analyzer | done |
| Detect recurring transient failures (same component, same error, rebuild fixes) | Analyzer | done |
| Auto-rebuild candidates for builds (transient failures: OOM, network, registry) | Analyzer | done |
| `ReleaseConfigAnalyzer` — audit release configuration | `src/analyzers/release_config_analyzer.py` | done |
| Detect release blockers from conforma violations (map violations → release gate) | Analyzer | done |
| Detect PCC cache staleness (stale advisories blocking release) | Analyzer | done |
| Detect missing conforma exceptions before release window | Analyzer | done |
| CLI: `ic ai analyze-config --type build` / `--type release` | CLI | done |
| MCP: `get_build_config_analysis()` / `get_release_config_analysis()` | MCP tool | done |

**Build config findings categories:**
- `stale_component`: component building from old commits (nudge not working)
- `recurring_transient`: same failure > 3 times in 7 days, always resolved by rebuild
- `quota_bottleneck`: builds queuing > 30 min regularly
- `missing_webhook`: push events not triggering builds
- `build_chain_broken`: upstream component failing, blocking downstream

**Release config findings categories:**
- `conforma_blocker`: unresolved violations that will block the release gate
- `pcc_cache_stale`: PCC cache not regenerated after last release
- `missing_exception`: violation needs exception before release window
- `sha_drift`: release candidate SHA doesn't match latest green build
- `nightly_broken`: nightly builds failing, hiding regression

### 16d — Unified Regression CLI

Extend `ic ai regression` to support all failure types from a single command.

| Feature | Surface | Status |
|---------|---------|--------|
| `ic ai regression <domain>` supports conforma, build, release, onboarding | CLI | done |
| Combined quality dashboard: `ic ai quality` | CLI | planned |
| Weekly quality report with trends | CLI | planned |
| Export training data for model improvement | `ic ai export-training-data --type build` | planned |

**Implementation order:**
1. Build regression tester (most data available — hundreds of resolved failures)
2. Release regression tester (fewer data points but higher impact per analysis)
3. Build config analyzer (directly actionable — finds auto-rebuild candidates)
4. Release config analyzer (directly actionable — finds release blockers early)
5. Unified CLI and weekly quality report

---

## Phase 17: Custom Failure Classifier

Goal: train a lightweight classifier on RHOAI-specific historical data to pre-screen failures before sending them to the LLM. LLM becomes a fallback for novel/ambiguous cases only, reducing cost and improving accuracy on well-known patterns.

**Architecture (cascade classifier):**
```
Resolved failure (PR diff + error metadata)
    ↓
Feature extractor (boolean + numeric features from PR diff)
    ↓
sklearn RandomForest  ← handles known patterns cheaply (~80% of cases)
    ↓  confidence < 0.7?
    ↓  YES → Claude (LLM) as fallback for novel cases
    ↓  NO  → return classification directly
```

**Why auto-labeling works here:** when a build failure is resolved, `resolution_commit_sha` is captured automatically. From that SHA, `get_pr_for_commit()` finds the GitHub PR. Changed files map directly to `failure_category` (go.mod → dependency_issue, Dockerfile → build_config, .tekton/ → config_error). No manual labeling needed.

**Log filter boundary — critical invariant:**
> `filter_error_lines()` in `log_filter.py` exists for ONE purpose: reduce LLM token cost before sending logs to Claude. The analyzer reads from MinIO (full log), filters, then sends to Claude. This function must NOT be changed or reused for ML purposes. The ML pipeline has its own separate extractor (`extract_root_cause_lines()`) that also reads from MinIO but applies a different, more aggressive strategy optimized for embedding/feature extraction, not LLM comprehension.

```
MinIO (full raw log — never touched)
    ├── build_failure_analyzer.py → filter_error_lines() → Claude    [LLM context, unchanged]
    ├── mcp_server/tools.py       → filter_error_lines() → MCP tool  [LLM context, unchanged]
    └── classifiers/log_extractor.py → extract_root_cause_lines()    [ML only, new, separate]
```

### 17a — Auto-Label Pipeline

Extend `verdict_correlator.py` + `github_client.py` to produce training labels from resolved PRs.

| Feature | Surface | Status |
|---------|---------|--------|
| `infer_label_from_pr(pr_files)` — map changed files to `failure_category` | `collectors/verdict_correlator.py` | planned |
| `label_confidence` score: high = single clear file type, low = mixed/unknown | `verdict_correlator.py` | planned |
| DB migration: add `ground_truth_category`, `label_source`, `label_confidence` to `ai_analysis` | migration | planned |
| Backfill: run auto-labeler on all resolved failures with `resolution_commit_sha` | `ic ai label-training-data --backfill` | planned |
| `ic ai export-training-data --type build [--min-confidence 0.7]` | CLI | planned |

**File → category mapping:**
- `go.mod`, `go.sum`, `requirements.txt`, `pyproject.toml`, `package.json` → `dependency_issue`
- `Dockerfile`/`Containerfile` with base image change → `base_image_update`
- `Dockerfile`/`Containerfile` other changes → `build_config`
- `.tekton/*.yaml` → `config_error`
- only `*_test.go`, `test_*.py` → `test_failure`
- source code only → `build_error`
- `hack/`, `Makefile`, build scripts → `build_error`
- mixed (config + code, or >2 categories) → `label_confidence=low`

### 17b — ML Log Extractor (separate from LLM filter)

A new extractor for the ML pipeline that reads full logs from MinIO. Must never be used in the analyzer or MCP tools — those use `filter_error_lines()` which stays unchanged.

| Feature | Surface | Status |
|---------|---------|--------|
| `extract_root_cause_lines(logs, max_lines=10)` — dedup, keep first unique error lines only | `src/classifiers/log_extractor.py` | planned |
| Strip cascading errors (same pattern repeated) and noise (timestamps, SHAs, Docker layers) | `log_extractor.py` | planned |
| `read_full_log_from_blob(failure)` — read from MinIO/BlobStore bypassing any filter | `log_extractor.py` | planned |
| Clearly documented: this module is ML-only, not for LLM context | docstring + comment | planned |

**Why a separate module:** `filter_error_lines()` keeps 20 lines of context per error match — right for Claude which needs surrounding context to understand the failure. For embeddings, context is noise; you want only the 5-10 unique root cause lines. Different consumers, different strategies, separate code.

### 17c — Regression Tester Metrics Improvements

Current regression testers report fuzzy accuracy only. Add proper classification metrics.

| Feature | Surface | Status |
|---------|---------|--------|
| F1 score per `failure_category` (precision + recall breakdown) | All regression testers | planned |
| Null model baseline: accuracy of majority-class classifier as lower bound | Regression testers | planned |
| `label_confidence` filter: evaluate only on high-confidence auto-labels | Regression testers | planned |
| Confusion matrix: `ic ai regression build --confusion` | CLI | planned |
| Delta vs baseline: "LLM alone: 77%, classifier+LLM: X%" comparison | `ic ai quality` | planned |

### 17d — Feature Extractor + sklearn Classifier

| Feature | Surface | Status |
|---------|---------|--------|
| `FailureFeatures` frozen dataclass: boolean + numeric features per failure | `src/classifiers/features.py` | planned |
| `extract_build_features(failure_dict)` → `FailureFeatures` (from PR diff, not log) | `src/classifiers/features.py` | planned |
| Key features: `has_go_mod_change`, `has_dockerfile_change`, `has_tekton_change`, `error_contains_oom`, `error_contains_network`, `build_history_pass_rate`, `times_rebuilt_same_error` | features | planned |
| `BuildClassifier`: sklearn RandomForest, `.joblib` serialization | `src/classifiers/build_classifier.py` | planned |
| `ic ai train-classifier --type build [--min-samples 50]` | CLI | planned |
| `ic ai classifier status` — model accuracy, training date, sample count | CLI | planned |
| Cascade integration in `BuildFailureAnalyzer`: run classifier first, LLM if confidence < 0.7 | `analyzers/build_failure_analyzer.py` | planned |

### 17e — Embeddings upgrade (only if 17d plateaus)

Only proceed if sklearn accuracy from PR-diff features plateaus below 80% and dataset has grown to N > 1000.

| Feature | Surface | Status |
|---------|---------|--------|
| Use `extract_root_cause_lines()` (from 17b) as text input | `log_extractor.py` | conditional |
| Embed filtered log lines via embeddings API (no PyTorch, no fine-tuning) | `src/classifiers/embeddings.py` | conditional |
| Train sklearn LogisticRegression on embeddings instead of boolean features | `build_classifier.py` | conditional |
| Benchmark: embeddings vs boolean features on same labeled dataset | `ic ai classifier benchmark` | conditional |

**Not PyTorch:** with N < 5000 (realistic ceiling for RHOAI), a fine-tuned transformer adds infrastructure cost and training complexity without accuracy gains over embeddings + sklearn. Revisit only if dataset reaches 10k+ examples.

**Implementation order:**
1. `infer_label_from_pr()` in `verdict_correlator.py` — no preconditions
2. DB migration + backfill + `ic ai export-training-data`
3. `extract_root_cause_lines()` in `src/classifiers/log_extractor.py` — separate from LLM filter
4. Regression tester metrics (F1, confusion matrix)
5. `FailureFeatures` + `BuildClassifier` + cascade integration
6. Embeddings upgrade only if step 5 is insufficient

---

## Phase 18: System Map (Phases 1-9 done, 10a-10d done, 10e-11 planned)

Goal: interactive visual map of RHOAI CI/CD infrastructure backed by Neo4j. See `docs/RHOAI-CICD-IMPROVEMENTS.md` §7.5 for full architecture and phased roadmap.

**Phase 1 — Static Map (done):**
- [x] Neo4j schema: 8 node types (Repository, Workflow, Pipeline, TektonTask, ECPolicy, Automation, Application, Component)
- [x] `seed.py`: populate graph from static topology + IC API (44 static nodes, 87 relationships)
- [x] FastAPI backend: 8 endpoints (graph, nodes, edges, node detail, search, path, gaps, stats, health)
- [x] React Flow frontend: interactive graph with custom nodes, zoom, pan, minimap, type filters, search
- [x] Detail panel with neighbor connections
- [x] Gap detection (orphan nodes, missing relationships)
- [x] 16 unit tests + 19 E2E BDD tests (Gherkin)
- [x] Descriptions with "why" context for all nodes

**Phase 2 — Live Status Overlay (done):**
- [x] `ic_client.py`: HTTP client to IC API (alerts, health, readiness) with 10s timeout
- [x] `live_status_service.py`: status computation (healthy/degraded/failing) + 55s cache
- [x] Status overlay: green/yellow/red borders on Component and Application nodes
- [x] Status precedence: health scores → build failures override → blocking conforma override
- [x] Activity feed panel (collapsible, bottom-right) with severity-colored events
- [x] `useLiveStatus` hook with 60s polling interval + unmount cleanup
- [x] Graceful degradation: IC unavailable → nodes keep static colors, feed shows "offline"
- [x] Live/Offline indicator in toolbar
- [x] Enriched detail strings: per-status context (consecutive failures, health score, success rate, last build date)
- [x] `_build_health_detail()` in `live_status_service.py`: failing → "5 consecutive failures | last fail: 2026-07-09", degraded → "health 60/100 | 7d success: 85%", healthy → "last build: 2026-07-10"
- [x] 24 tests (ICClient, LiveStatusService, enriched detail, route)

**Phase 3 — MCP Server (done):**
- [x] `map/mcp_server/`: FastMCP server with 11 tools (same pattern as IC MCP server)
- [x] Tools: `get_map_graph`, `search_map_nodes`, `get_map_node`, `find_map_path`, `get_map_gaps`, `get_map_stats`, `get_map_live_status`, `get_map_impact`, `seed_map_from_cluster`, `get_map_drift`, `get_map_changes`
- [x] Registered in `.claude/settings.json` as `system-map` MCP server (stdio transport)
- [x] 14 unit tests covering all tools + error cases
- [x] Cypher injection fix in `graph.py:search_nodes` (parameterized queries)

**Phase 4 — Impact Analysis (done, frontend added 2026-07-10):**
- [x] `get_impact(node_id, max_depth, direction)` — trace downstream/upstream via variable-length Cypher paths
- [x] Blast radius: affected nodes grouped by depth and type with relationship chain
- [x] Direction support: downstream (what does this affect?) and upstream (what affects this?)
- [x] Depth-limited traversal (1-10 hops, default 5) with LIMIT 500 safety cap
- [x] MCP tool: `get_map_impact(node_id, max_depth, direction)`
- [x] API endpoint: `GET /api/map/impact/{node_id}?max_depth=5&direction=downstream`
- [x] 3 unit tests (downstream, upstream, node not found)
- [x] Frontend: "Downstream Impact" and "Upstream Deps" buttons in DetailPanel
- [x] Blast radius visualization: source node red glow, affected nodes orange glow, edges highlighted
- [x] Non-affected nodes dimmed to 12% opacity with "Clear" button to reset

**Phase 5 — Auto-seed from Cluster (done):**
- [x] `ClusterSeeder`: populates Neo4j from live IC API data (applications, components)
- [x] HTTP session pooling with `requests.Session` for connection reuse
- [x] Nudge chain inference from component naming conventions (comp → bundle → FBC fragment)
- [x] Drift detection: compare graph nodes vs cluster components (stale + missing)
- [x] MCP tools: `seed_map_from_cluster(application)`, `get_map_drift()`
- [x] API endpoints: `POST /api/map/seed/cluster`, `GET /api/map/drift`
- [x] Auto-seed on startup: `_auto_seed_from_ic()` in FastAPI lifespan — Neo4j populated with 230+ components on boot (no manual seed needed)
- [x] Drift endpoint accepts `?application=` parameter for multi-app drift detection
- [x] 15 unit tests (health, seed apps/components, string lists, nudge chains, drift, seed_all, auto-seed)

**Phase 6 — Change Detection Feed (done):**
- [x] `ChangeTracker`: records node/edge additions, removals, and property modifications
- [x] Snapshot-based diff: compare current graph state against stored snapshots
- [x] Timezone-safe datetime filtering with `datetime.fromisoformat()` comparison
- [x] Filter by entity_id (partial match), change_type (added/removed/modified), date range
- [x] Property diff: detects changed keys between old and new node properties
- [x] MCP tool: `get_map_changes(since, entity_id, change_type, limit)`
- [x] API endpoint: `GET /api/map/changes?since=...&entity_id=...&change_type=...`
- [x] Tests for snapshot diff, property change detection, filtering

**Phase 7 — Onboarding Tracker Visual (done):**
- [x] Overlay onboarding progress on Component nodes (score badge with badge_color)
- [x] Color-coded by onboarding status: complete=green, partial=yellow, incomplete=red
- [x] Full onboarding detail section in DetailPanel (check steps, score bar, Jira link)
- [x] Data flow: `useLiveStatus` → `onboardingMap` → `App.jsx` merges into node data → `MapNode.jsx` renders badge

**Phase 8 — IC CLI Link (done 2026-07-10):**
- [x] `SYSTEM_MAP_URL` env var in `shared_config.py` (default `http://localhost:3001`)
- [x] `make_system_map_url(component_name)` helper function
- [x] `ic describe component <comp>` prints `System Map:` deep-link URL
- [x] Frontend parses `?highlight=comp-<name>` URL param on mount
- [x] Auto-selects node, opens DetailPanel, dims non-matching nodes to 0.35 opacity
- [x] One-shot highlight via `useRef` gate (doesn't re-trigger on live-status merges)
- [x] 7 unit tests covering URL generation edge cases

**Phase 9 — PR & Resolution Event Feed (done 2026-07-10):**
- [x] New `get_recent_for_application(app, days, limit)` in `ResolutionAttemptRepository`
- [x] New IC API endpoint: `GET /applications/{app}/pr-activity` (aggregated, not N+1)
- [x] New `ICClient.get_pr_activity()` method in Map backend
- [x] `LiveStatusService._build_pr_events()` builds PR events from IC data
- [x] PR events merged into main activity feed, sorted by timestamp, capped at 30
- [x] Three new severity types with distinct colors:
  - `pr_open` — blue (#2563eb) — PR opened, fix in progress
  - `pr_merged` — green (#059669) — PR merged (and optionally verified)
  - `pr_stale` — amber (#d97706) — PR merged but fix verification failed
- [x] `ActivityFeed.jsx` updated with new severity styles
- [x] 6 unit tests covering PR event severity logic and integration

**Phase 10 — Conversational Agent (10a done 2026-07-10):**

Goal: embedded AI assistant in the Map UI that answers questions, diagnoses problems, teaches concepts, and suggests improvements — using the graph topology + IC data together.

*10a — Graph Explainer (done 2026-07-10):*
- [x] `ChatPanel.jsx` — collapsible floating panel (bottom-left), message history, loading state
- [x] `chat_service.py` — ChatService class using existing `LLMProvider` abstraction (Vertex AI / Anthropic)
- [x] `POST /api/map/chat` endpoint — receives message + optional node_id, builds graph context
- [x] Context includes: selected node props, neighbors (capped at 15), gaps, impact analysis, graph stats
- [x] System prompt tuned for RHOAI CI/CD domain, 2-4 sentence answers
- [x] Node-aware placeholder: "Ask about comp-X..." when a node is selected
- [x] Error display for LLM unavailability (503 when no provider configured)
- [x] 11 unit tests covering context building, chat service, and route

*10b — Contextual Diagnostics (done 2026-07-10):*
- [x] "Why is this node red?" → fetches IC `get_failure()` + AI analysis + graph context
- [x] "What's blocking the release?" → combines readiness checks + blockers + triage
- [x] "Is anyone working on this?" → triage items filtered by component with Jira keys
- [x] ICClient expanded: `get_failure()`, `get_analysis()`, `get_violation()`, `get_triage()`, `get_blockers()`
- [x] `_fetch_ic_context()` routes by node type: comp-* → failure/analysis/violation/triage, app-* → readiness/blockers/triage
- [x] `_build_ic_context()` formats IC data into structured sections for LLM: [Build Failure], [AI Analysis], [Conforma Violation], [Active Triage], [Release Readiness], [Jira Blockers]
- [x] System prompt updated for diagnostic queries (cite errors, suggest next steps)
- [x] IC data always fetched when node_id provided (best-effort, degrades gracefully)
- [x] 25 tests covering IC context building, fetch routing, and integration

*10c — Panoramic View (done 2026-07-10):*
- [x] App-wide IC context when no node selected: alerts, triage summary, daily stats, resolved, readiness, nightly, schedule, health warnings, stale components
- [x] `_fetch_panoramic_context()` — 9 parallel IC calls via ThreadPoolExecutor
- [x] `_build_panoramic_context()` — formats into [Current State], [Failure Trend], [Recent Resolutions], [Release Status], [Nightly Build Health], [Health Warnings], [Stale Components]
- [x] System prompt updated for panoramic queries (trends, deadlines, state summaries)
- [x] Contextual suggestion chips: panoramic set when no node, node-specific when selected
- [x] 8 new ICClient methods: get_stats, get_daily_stats, get_resolved, get_nightly, get_schedule, get_health_warnings, get_stale, get_triage_summary
- [x] 37 tests (25 existing + 12 new for panoramic context, fetch, and integration)

*10d — Interactive Onboarding (teaching mode) — done:*
- [x] Guided tour of RHOAI CI/CD: highlights nodes while explaining concepts
- [x] "Explain Conforma" → points to ECPolicy nodes, traces validation path to release
- [x] "How does nudging work?" → highlights NUDGES edges, walks through the flow
- [x] 5 concept paths: Conforma, Nudging, Build Pipeline, Release Lifecycle, Dependency Management
- [x] Concept detection via regex trigger matching in chat messages
- [x] Chat response returns `highlight` field with node IDs, edge IDs, glow color, dim flag
- [x] Frontend applies concept highlights using same pattern as impact analysis
- [x] "Show on map" button on assistant messages for re-applying concept highlights
- [x] Dynamic edge expansion for NUDGES (includes all component nudge chains from live graph)
- [x] Beginner-friendly narratives: technical but every term explained inline
- [ ] Adaptive: detects what user already knows from questions asked (deferred to future iteration)

*10e — Guided Actions + Improvement Suggestions:*
- [ ] Suggest rebuilds for transient failures with "do it?" confirmation → `trigger_rebuild`
- [ ] Suggest triage/Jira creation with pre-filled context from graph + IC diagnosis
- [ ] **Config improvement suggestions** from graph + IC cross-analysis:
  - Graph gaps → "This component has no pipeline linked, want me to fix it?"
  - Recurring failures → "This fails 5x/week by the same cause — suggest auto-rebuild rule"
  - Config drift → "nudging.yaml references 3 components that no longer exist"
  - Release prep → "4 days to freeze, 3 exceptions expiring — here's a prioritized action plan"
  - Pipeline migration → "8 components still on deprecated docker-build, here's the migration"
  - Onboarding gaps → "These components always stall on the same steps: webhook + renovate"
- [ ] All actions require user confirmation — never auto-execute

**Phase 11 — Resolution Visualization (planned):**
- [ ] Resolution timeline component showing IC fix workflow steps
- [ ] SSE/WebSocket event stream from IC
- [ ] Path highlighting for affected nodes during resolution

---

## Phase 19: FBC & Chart Introspection

Goal: extract and expose the contents of FBC (File-Based Catalog) fragment images and Helm charts so that IC can answer "what ships in this release?" precisely, and the System Map can visualize component→bundle→channel relationships.

**19a — FBC Fragment Inspection (IC):**
- [ ] `ic fbc inspect [app]` — extract channels, operator versions, related images from FBC fragment
- [ ] Parse FBC JSON from `opm render` or registry manifest inspection
- [ ] Show which component images are referenced in each channel (fast/stable/candidate)
- [ ] Detect stale images: component has newer build but FBC still references old SHA
- [ ] `ic fbc diff <sha1> <sha2>` — compare two FBC fragments (new images, channel updates, removed entries)
- [ ] MCP tool: `get_fbc_contents(application)` for agent access
- [ ] Cache extracted FBC data (changes only on rebuild)

**19b — Helm Chart Inspection (IC):**
- [ ] `ic chart inspect <chart-name>` — extract values, dependencies, image references from Helm charts
- [ ] List all container images referenced in chart templates
- [ ] Detect version mismatches between chart values and actual built images
- [ ] Compare chart versions across releases (EA1 vs EA2 vs GA)
- [ ] MCP tool: `get_chart_contents(chart_name)` for agent access

**19c — System Map Integration:**
- [ ] Add FBC fragment as a node type with edges to all component images it references
- [ ] Add Helm chart nodes with edges to referenced images and config
- [ ] Channel membership visualization: which components are in which OLM channels
- [ ] "What ships together?" view — click FBC node to see full bundle contents

---

## Phase 20: Proactive Konflux Configuration Advisor

Goal: shift from reactive failure detection to proactive infrastructure auditing. IC currently reacts to build failures and Conforma violations — this phase adds intelligence that audits Konflux configuration and suggests improvements *before* problems occur, based on features available in the installed Konflux version.

**20a — Build Optimization Audit:**
- [ ] Detect components without hermetic builds (required for SLSA L3 compliance)
- [ ] Detect components without caching proxy enabled (slower builds than necessary)
- [ ] Audit `on-cel-expression` in `.tekton/` PipelineRun YAMLs — find redundant rebuilds (CEL too broad) or missed builds (CEL too restrictive)
- [ ] Detect components that need pipeline migration (deprecated Tekton tasks not yet updated by MintMaker)
- [ ] Detect components without trusted artifacts (insecure pipeline data sharing)
- [ ] Suggest `overriding-compute-resources` when builds OOM recurrently (pattern: same component, OOMKilled, >3 times)
- [ ] Track build duration trends — flag components where build time increased >50% vs 30-day average
- [ ] MCP: `get_build_optimization_audit(application)` / CLI: `ic advisor builds [app]`

**20b — Testing Gap Detection:**
- [ ] Detect applications without periodic integration tests (Konflux supports CronJob + snapshot labeling)
- [ ] Detect ITS with wrong contexts (should run on push but only configured for PR, or vice versa)
- [ ] Detect ITS pointing to stale Git refs (resolver bundle hasn't changed in >90 days)
- [ ] Detect if group snapshots are configured for coordinated cross-component PRs
- [ ] Surface which ITS scenarios are disabled and why
- [ ] MCP: `get_testing_gap_audit(application)` / CLI: `ic advisor tests [app]`

**20c — Supply Chain Compliance Audit:**
- [ ] SLSA provenance attestation gaps — verify all component images have complete attestations
- [ ] SBOM completeness audit — not just CVE counts, verify SBOM contains all declared dependencies
- [ ] Artifact scan results freshness — detect outdated SARIF scans (>7 days since last scan)
- [ ] Verify attestation signatures match expected signing keys
- [ ] MCP: `get_supply_chain_audit(application)` / CLI: `ic advisor compliance [app]`

**20d — Dependency Management Health (MintMaker):**
- [ ] Detect components without MintMaker/Renovate configured
- [ ] Security update PRs ignored (open >7 days without review) — correlate with CVE severity
- [ ] RPM lockfile refresh not configured where applicable
- [ ] Compare renovate.json5 with MintMaker default config to find coverage gaps
- [ ] Track auto-merge health: nudge PRs that should auto-merge but don't
- [ ] MCP: `get_dependency_health(application)` / CLI: `ic advisor deps [app]`

**20e — Configuration Drift Detection:**
- [ ] CaC repo (Kustomize overlays) vs actual cluster state — drift detection
- [ ] Component CR spec vs `.tekton/` config mismatch
- [ ] ImageRepository credentials rotation status (approaching expiry)
- [ ] ReleasePlan / ReleasePlanAdmission freshness (last updated >90 days ago)
- [ ] EC policy stage vs prod gap — components that pass stage but would fail prod
- [ ] MCP: `get_config_drift(application)` / CLI: `ic advisor drift [app]`

**20f — Unified Advisor Dashboard:**
- [ ] `ic advisor [app]` — run all audits, present unified report with priority ranking
- [ ] Severity scoring: Critical (will block release), Warning (toil/risk), Info (improvement opportunity)
- [ ] Track advisor findings over time — show which were addressed, which are new
- [ ] Weekly digest: "3 new findings, 2 resolved since last week"
- [ ] Integration with Map agent (Phase 10e) — advisor findings feed into improvement suggestions

---

## Current Release Status (updated 2026-07-10, audit gaps resolved)

**RHAI 3.5 schedule (from Product Pages):**

| Milestone | EA1 | EA2 | GA |
|-----------|-----|-----|-----|
| RHOAI Planning Freeze | May 1 | May 15 | Jun 24 |
| RHOAI Feature Freeze | — | — | Jul 17 |
| RHOAI Code Freeze | May 15 | Jun 19 | Jul 24 |
| RHOAI Initial RC | May 21 | Jun 25 | Jul 30 |
| RHOAI Release Window | Jun 16-17 | Jul 14-15 | Aug 18-19 |
| RHOAI Release | Jun 18 | Jul 16 | Aug 20 |
| RHAI 3.5 GA | — | — | Aug 20 |

**EA2 status (as of Jul 10):**
- RC1: Jun 23 ✓ — RC2: Jul 3 ✓ (PCC cache regen)
- Release window: Jul 14-15 (4 days away)
- Israel testing started Jul 5

**3.5 GA status:**
- Feature freeze: Jul 17 (7 days)
- Code freeze: Jul 24 (14 days)
- Deprecated Tekton tasks EOL: Aug 20-30 (must update before GA)
- EC volatile exclusions expire: Aug 1 (must fix root cause or renew)

**Known issues:**
- PCC cache not auto-refreshing after releases (seen EA1+EA2, sustaining investigating)
- Konflux config validator blocking scheduled nightlies (Alex+Mhamad fix, deadline Jul 17)
- ~~`ic get conforma` ignores positional app arg~~ (fixed: argument wired as `application`)
- ~~Release-readiness false blockers~~ (fixed: exception-aware `compute_blocks()` in readiness endpoint)
- ~~System Map nodes show no detail beyond status color~~ (fixed: enriched detail strings per status type)
- ~~System Map requires manual seed after every restart~~ (fixed: auto-seed from IC API on startup)
- ~~Drift endpoint only checks default application~~ (fixed: `?application=` query parameter)

**People:**
- Ujjwal: new build IC, mentored by Moulali
- Jira dashboard: https://redhat.atlassian.net/jira/dashboards/23119

**Konflux upstream activity (as of Jul 10):**
- release-service: managed pipeline retry with mitigations (RELEASE-2120)
- build-definitions: task migration tool improvements, step-count optimization guidance
- mintmaker: replaced deprecated buildah image
- integration-service: added PipelineRun link in status, RBAC fixes for SA/RoleBindings

---

## Principles

- **Three layers stay aligned**: every feature ships in CLI + MCP + API
- **Business logic in shared services**: repositories, HealthMonitor, analyzers — never in interface layers
- **API is independently deployable**: no CLI dependency, suitable for OpenShift
- **Upstream-first fixes**: don't fix component deps directly on rhoai-* branches
- **Human approval for actions**: never auto-merge PRs or auto-send Jira/Slack without confirmation
