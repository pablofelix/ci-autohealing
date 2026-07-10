# Feature Capabilities

Inventory of all user-facing capabilities across CLI (`ic`), MCP tools, and API endpoints.

---

## Build Failures

| Capability | CLI | MCP Tool | API |
|---|---|---|---|
| List failing components | `ic get alerts` | `list_alerts()` | `GET /api/v1/applications/{app}/alerts` |
| Get failure details | `ic describe component <comp>` | `get_failure()` | `GET /api/v1/applications/{app}/failures/{comp}` |
| Search failures | — | `search_failures()` | — |
| List working components | — | `get_working()` | `GET /api/v1/applications/{app}/working` |
| List resolved failures | — | `get_resolved()` | `GET /api/v1/applications/{app}/resolved` |
| Component build history | — | `get_component_history()` | `GET /api/v1/applications/{app}/failures/{comp}/history` |
| Trigger rebuild | `ic rebuild <comp>` | `trigger_rebuild()` | — |
| Fix propagation check | — | `check_fix_propagation()` | `GET /api/v1/applications/{app}/fix-propagation` |
| Blockers summary | — | `list_blockers()` | `GET /api/v1/applications/{app}/blockers` |
| Bouncing issues | — | `list_bouncing_issues()` | `GET /api/v1/applications/{app}/bouncing-issues` |
| Daily stats | `ic stats daily` | `get_daily_stats()` | `GET /api/v1/applications/{app}/stats/daily` |
| Component PR status | — | `get_component_prs()` | — |

## AI Analysis

| Capability | CLI | MCP Tool | API |
|---|---|---|---|
| Analyze build failure (single) | `ic ai analyze <comp>` | `get_analysis()` | `POST /api/v1/applications/{app}/analyses` |
| Batch analyze failures | `ic ai batch` | — | — |
| Conforma config audit | `ic ai analyze-config --type conforma` | `get_config_analysis()` | — |
| Build pipeline config audit | `ic ai analyze-config --type build` | `get_build_config_analysis()` | — |
| Release config audit | `ic ai analyze-config --type release` | `get_release_config_analysis()` | — |
| Regression testing | `ic ai regression` | `get_regression_report()` | — |
| AI quality metrics | `ic ai quality` | `get_ai_quality_metrics()` | `GET /api/v1/metrics/ai-quality` |
| AI stats (usage/costs) | `ic ai stats` | — | — |
| Review (resolved failures) | `ic ai review` | — | — |
| Analysis verdicts | — | — | `POST /api/v1/applications/{app}/analyses/{comp}/verdict` |

## Conforma (EC Policy Compliance)

| Capability | CLI | MCP Tool | API |
|---|---|---|---|
| List violations | `ic get conforma` | `get_violation()` | `GET /api/v1/applications/{app}/violations` |
| Describe violation | `ic describe conforma <comp>` | `get_violation()` | `GET /api/v1/applications/{app}/violations/{comp}` |
| Violation categories | — | `get_conforma_categories()` | — |
| Violation rules | — | `get_conforma_rules()` | — |
| EC policy summary | — | `get_ec_policy_summary()` | — |
| Scenario coverage | — | `get_scenario_coverage()` | — |
| Resolved patterns | — | `get_resolved_patterns()` | — |
| Conforma report | — | `get_conforma_report()` | — |
| Exception lifecycle | — | `get_exception_lifecycle()` | `GET /api/v1/exceptions/lifecycle` |
| List exceptions | `ic get exceptions` | — | `GET /api/v1/policies/exceptions` |
| Policy bindings | `ic get bindings` | — | `GET /api/v1/policies/bindings` |
| Policy gap analysis | `ic get policy-gap` | — | — |

## Release Engineering

| Capability | CLI | MCP Tool | API |
|---|---|---|---|
| Release status | `ic get releases` | `get_release_status()` | `GET /api/v1/applications/{app}/releases/{name}` |
| Release readiness | — | `get_release_readiness()` | `GET /api/v1/applications/{app}/readiness` |
| Release schedule | — | `get_release_schedule()` | `GET /api/v1/applications/{app}/schedule` |
| Release vulnerabilities | — | `get_release_vulnerabilities()` | — |
| Snapshot status | — | `get_snapshot_status()` | — |
| Snapshot freshness | — | `get_snapshot_freshness()` | — |
| Stale components | — | `get_stale_components()` | `GET /api/v1/applications/{app}/stale` |
| Freeze management | — | `list_freezes()` / `get_active_freeze()` | `GET /api/v1/freezes` |
| FBC fragment history | — | `get_fbc_history()` | `GET /api/v1/applications/{app}/fbc-history` |
| Nightly build status | — | `get_nightly_status()` | `GET /api/v1/applications/{app}/nightly` |
| Nightly build history | — | `get_nightly_history()` | `GET /api/v1/applications/{app}/nightly/history` |

## Health Monitoring

| Capability | CLI | MCP Tool | API |
|---|---|---|---|
| Application health | — | `get_health()` | `GET /api/v1/applications/{app}/health` |
| Health warnings | `ic components health` | `get_health_warnings()` | `GET /api/v1/applications/{app}/health/warnings` |
| Dashboard (unified) | — | `get_dashboard()` | `GET /api/v1/applications/{app}/dashboard` |
| Component status | — | `get_component_status()` | — |
| Jira token health | — | `check_jira_health()` | `GET /api/v1/jira/health` |

## Triage

| Capability | CLI | MCP Tool | API |
|---|---|---|---|
| Triage summary | `ic ai status` | `get_triage()` | `GET /api/v1/applications/{app}/triage-summary` |
| Triage report | — | `get_triage_report()` | `GET /api/v1/applications/{app}/triage` |

## Skills System

| Capability | CLI | MCP Tool | API |
|---|---|---|---|
| List skills | `ic skills list` | `list_skills()` | `GET /api/v1/skills` |
| Skill info | `ic skills info <name>` | `get_skill_info()` | `GET /api/v1/skills/{name}` |
| Run skill | `ic skills run <name>` | `run_skill()` | `POST /api/v1/skills/{name}/run` |
| Skill sources | `ic skills sources` | `list_skill_sources()` | `GET /api/v1/skills/sources` |
| Validate skill | `ic skills validate <name>` | — | `GET /api/v1/skills/{name}/validate` |
| Check prerequisites | — | `check_skill_prerequisites()` | `GET /api/v1/skills/{name}/prerequisites` |
| Skill runs | `ic skills runs` | — | `GET /api/v1/skills/runs` |
| Skill output | `ic skills output <id>` | — | — |
| Manage tags | `ic skills tag add/remove` | — | — |
| Skill doctor | `ic skills doctor` | — | — |

## Applications & Configuration

| Capability | CLI | MCP Tool | API |
|---|---|---|---|
| List applications | `ic get apps` | `list_applications()` | `GET /api/v1/applications` |
| App stats | — | `get_stats()` | `GET /api/v1/applications/{app}/stats` |
| Watched applications | — | `get_watched_applications()` | — |
| Watch config | `ic config watch list/add/remove` | — | — |
| Set application | `ic config set-app <app>` | — | `POST /api/v1/config/applications` |
| Test configuration | — | `get_test_configuration()` | — |

## Onboarding

| Capability | CLI | MCP Tool | API |
|---|---|---|---|
| Onboarding status | `ic onboard status` | `get_onboarding_status()` | `GET /api/v1/applications/{app}/onboarding` |
| Describe component | `ic onboard describe <comp>` | `get_onboarding_describe()` | `GET /api/v1/applications/{app}/onboarding/{comp}` |
| Analyze component | — | `analyze_onboarding()` | `POST /api/v1/applications/{app}/onboarding/{comp}/analyze` |

## Infrastructure

| Capability | CLI | MCP Tool | API |
|---|---|---|---|
| PipelineRuns | `ic get pipelineruns` | — | — |
| PipelineRun detail | `ic describe pipelinerun <id>` | — | — |
| Jira issues | `ic get jira` | — | `GET /api/v1/jira/{key}` |
| Describe Jira | `ic describe jira <key>` | — | — |
| Fix history | — | `get_fix_history()` | `GET /api/v1/fixes` |
| Error patterns | — | `list_patterns()` / `get_pattern()` | `GET /api/v1/patterns` |
| Export data | — | — | `GET /api/v1/applications/{app}/export/{comp}` |
| DB status | `ic db status` | — | — |
| DB query | `ic db query` | — | — |

---

## Capability Counts

| Surface | Count |
|---|---|
| CLI commands (visible) | ~55 |
| MCP tools | 72 |
| API endpoints | ~45 |
