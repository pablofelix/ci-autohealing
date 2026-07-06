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
- [ ] Human feedback loop: verdict on triage resolve (correct/partial/incorrect)
- [ ] `human_verdict` column in ai_analysis table
- [ ] classification_accuracy, auto_fix_precision, unsafe_auto_fix_rate
- [ ] avg_cost_per_analysis, cost_per_correct_diagnosis
- [ ] API endpoint: `/api/v1/metrics/ai-quality`
- [ ] Weekly accuracy report command: `ic ai quality`
- [ ] Confidence calibration analysis
- [ ] Training data export: `ic ai export-training-data` (for future custom model)

**6a2 — Confidence Calibration & Scientific Error Analysis (requires 6a)**
- [ ] Calibration curve: when AI says 0.90, how often is it correct? Plot predicted vs actual
- [ ] Bayesian priors per failure category from historical verdicts
- [ ] Feature-based scoring: replace subjective LLM float with calculated score from boolean features (has_build_history, sha_mismatch_confirmed, nudging_checked, etc.)
- [ ] Confidence cap refinement: tune penalties based on actual miss rates per missing source
- [ ] Calibration dashboard in `ic ai quality --calibration`
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

## Phase 16: Build & Release AI Quality (next)

Goal: apply the same regression testing and config analysis approach that improved the conforma AI analyzer (from 41% to target 80%+ accuracy) to build failures and release failures. Use existing AI triage tracker data as ground truth.

**Context from conforma work (Phase 11-12):**
- ConformaRegressionTester found 41% accuracy, 0.02 calibration, config_error catch-all at 0%
- ConfigAnalyzer found 9 actionable findings at 91% confidence (expired exceptions, disabled ITS scenarios)
- Pattern: resolved violations with AI analysis → compare predicted vs actual → identify weaknesses → improve prompts

### 16a — Build Failure Regression Tester

Test `BuildFailureAnalyzer` predictions against resolved build failures as ground truth.

| Feature | Surface | Status |
|---------|---------|--------|
| `BuildRegressionTester` — compare AI predictions vs actual resolutions | `src/analyzers/build_regression.py` | planned |
| Query resolved failures with AI analysis (build_failures + ai_analysis JOIN) | Repository | planned |
| Evaluate failure_category accuracy (AI predicted vs resolution pattern) | Analyzer | planned |
| Evaluate can_auto_fix accuracy (was it actually auto-fixable?) | Analyzer | planned |
| Compute calibration score (confidence vs correctness correlation) | Analyzer | planned |
| Coverage gap detection (failure patterns with 0% AI coverage) | Analyzer | planned |
| Improvement suggestions (catch-all overuse, low-confidence categories) | Analyzer | planned |
| CLI: `ic ai regression --type build [--app APP] [--limit N]` | CLI | planned |
| MCP: `get_build_regression()` | MCP tool | planned |

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
| `ReleaseRegressionTester` — compare AI vs resolution for release failures | `src/analyzers/release_regression.py` | planned |
| Query release attempts with AI analysis that later succeeded | Repository | planned |
| Evaluate root cause accuracy (verify-conforma vs infra vs config) | Analyzer | planned |
| Track resolution path (which violations were actually fixed vs excepted) | Analyzer | planned |
| SHA tracing accuracy (did AI correctly identify stale SHAs?) | Analyzer | planned |
| CLI: `ic ai regression --type release [--app APP]` | CLI | planned |
| MCP: `get_release_regression()` | MCP tool | planned |

**Release-specific metrics:**
- Was the release blocker correctly identified? (wrong component → wasted investigation time)
- SHA mismatch detection accuracy (AI flagged stale SHA that was actually the issue)
- Exception recommendation accuracy (AI suggested exception → was it the right call?)
- Enrichment source impact (analyses WITH build history vs WITHOUT — accuracy delta)

### 16c — Build/Release Config Analyzer

LLM-powered audit of build pipeline and release configuration, similar to `ConfigAnalyzer` for conforma.

| Feature | Surface | Status |
|---------|---------|--------|
| `BuildConfigAnalyzer` — audit build pipeline configuration | `src/analyzers/build_config_analyzer.py` | planned |
| Detect stale components (no build triggered despite new commits) | Analyzer | planned |
| Detect misconfigured nudging (component in nudging.yaml but builds failing) | Analyzer | planned |
| Detect quota issues (build stuck in pending > 30 min) | Analyzer | planned |
| Detect recurring transient failures (same component, same error, rebuild fixes) | Analyzer | planned |
| Auto-rebuild candidates for builds (transient failures: OOM, network, registry) | Analyzer | planned |
| `ReleaseConfigAnalyzer` — audit release configuration | `src/analyzers/release_config_analyzer.py` | planned |
| Detect release blockers from conforma violations (map violations → release gate) | Analyzer | planned |
| Detect PCC cache staleness (stale advisories blocking release) | Analyzer | planned |
| Detect missing conforma exceptions before release window | Analyzer | planned |
| CLI: `ic ai analyze-config --type build` / `--type release` | CLI | planned |
| MCP: `get_build_config_analysis()` / `get_release_config_analysis()` | MCP tool | planned |

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
| `--type conforma\|build\|release\|all` flag | CLI | planned |
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

## Current Release Status (updated 2026-07-06)

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

**EA2 status (as of Jul 1):**
- RC1: Jun 23 ✓ — RC2: Jul 3 (PCC cache regen, not code change)
- 1 blocker: consume ray cuda (hardware dependency)
- Release window: Jul 14-15
- Israel testing started Jul 5

**3.5 GA status:**
- Code freeze: Jul 24
- 5 blockers (same as prior week)
- Same-day release coordinated with RHAI team

**Known issues:**
- PCC cache not auto-refreshing after releases (seen EA1+EA2, sustaining investigating)
- Konflux config validator blocking scheduled nightlies (Alex+Mhamad fix, deadline Jul 17)
- Onboarding automation prematurely closing Jira tickets

**People:**
- Ujjwal: new build IC, mentored by Moulali
- Jira dashboard: https://redhat.atlassian.net/jira/dashboards/23119

---

## Principles

- **Three layers stay aligned**: every feature ships in CLI + MCP + API
- **Business logic in shared services**: repositories, HealthMonitor, analyzers — never in interface layers
- **API is independently deployable**: no CLI dependency, suitable for OpenShift
- **Upstream-first fixes**: don't fix component deps directly on rhoai-* branches
- **Human approval for actions**: never auto-merge PRs or auto-send Jira/Slack without confirmation
