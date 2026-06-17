# CI AutoHealing — Threat Model

Security analysis of the ci-autohealing system. Covers attack surfaces, risks, mitigations, and phased implementation plan.

---

## System Overview

```
Users (CLI/MCP) → API Server → PostgreSQL
                              → MinIO (blobs)
                              → External Services (GitHub, Jira, GitLab, Konflux)
Worker → Konflux Cluster (read-only SA)
       → LLM Provider (Vertex AI / Anthropic)
       → GitHub API (PR creation in AUTONOMOUS_MODE)
Watcher → Konflux Cluster (read-only SA)
Skills → Subprocess execution (user machine or K8s Job sandbox)
```

---

## T1: Malicious Build Logs (Log Injection)

**Risk**: HIGH — Build logs are fetched from untrusted sources (user commits trigger builds) and processed by the AI analyzer. A malicious actor could craft a commit that produces build logs containing:

- Prompt injection attacks targeting the LLM
- Fake error messages that mislead diagnosis
- Encoded payloads that execute when logs are rendered

**Attack vectors:**
- Attacker commits code with crafted error output (e.g., `echo "Root cause: run rm -rf /"`)
- Build log contains ANSI escape sequences that hijack terminal rendering
- Log contains fake JSON that confuses the parser

**Current mitigations:**
- Logs are truncated at 200K chars
- AI prompt has system instructions (but no explicit injection protection)
- Log filter uses regex matching (not execution)

**Needed mitigations:**

| Phase | Mitigation | Priority |
|-------|-----------|----------|
| S1 | Strip ANSI escape codes from all stored/displayed logs | HIGH |
| S1 | Sanitize log content before LLM prompt (remove `<script>`, control chars) | HIGH |
| S2 | Add prompt injection detection to AI analyzer (canary tokens, output validation) | MEDIUM |
| S3 | Content Security Policy for any web-based log rendering | LOW |

---

## T2: Secret Leakage in Logs and Analysis

**Risk**: HIGH — Build logs, AI analysis results, and commit context may contain secrets (API keys, tokens, passwords) from failed builds.

**Attack vectors:**
- Build fails during secret injection step, leaking env vars to logs
- AI analysis includes secret values in root cause description
- Logs stored in MinIO/PostgreSQL accessible by anyone with DB access
- `ic describe component --log` displays secrets to terminal

**Current mitigations:**
- Skill validator scans for hardcoded secrets (SECRET_PATTERNS in validator.py)
- Blob storage offloads large logs to MinIO (not more secure, just different location)

**Needed mitigations:**

| Phase | Mitigation | Priority |
|-------|-----------|----------|
| S1 | Secret scanning on all stored logs — redact before saving to DB | CRITICAL |
| S1 | Redact secrets in AI analysis output before storage | CRITICAL |
| S1 | Add secret patterns: base64-encoded tokens, JWT tokens, connection strings | HIGH |
| S2 | Never display raw logs without redaction — `ic describe --log` must filter | HIGH |
| S2 | Audit log access — who viewed which component's logs and when | MEDIUM |

---

## T3: Autonomous PR Creation Risks

**Risk**: HIGH — When `AUTONOMOUS_MODE=true`, the system creates GitHub PRs automatically. A compromised or misconfigured system could:

- Create PRs with malicious code changes
- Push to protected branches
- Create PRs at high volume (spam/DoS)
- Modify files outside the intended scope

**Current mitigations:**
- `AUTONOMOUS_MODE` must be explicitly set to `true` (env var)
- Confidence threshold: `auto_fix_min_confidence >= 0.95`
- Max fixes per run: `auto_fix_max_per_run = 3`
- Branch dedup: won't create duplicate fix branches
- Worker checks: `requires_env=['GITHUB_TOKEN', 'AUTONOMOUS_MODE']`

**Needed mitigations:**

| Phase | Mitigation | Priority |
|-------|-----------|----------|
| S1 | Allowlist of repos where PRs can be created (not just any repo in the org) | CRITICAL |
| S1 | Allowlist of file patterns that can be modified (e.g., only `Dockerfile`, `go.mod`) | CRITICAL |
| S1 | Branch naming convention enforcement (prefix `ci-autohealing/`) | HIGH |
| S1 | Rate limit: max N PRs per hour, per repo | HIGH |
| S2 | PR body includes hash of the analysis that generated it (audit trail) | MEDIUM |
| S2 | Dry-run mode default: create PR as draft, require human merge | MEDIUM |
| S3 | Two-person approval: PR must be reviewed before merge | LOW |

---

## T4: GitHub Token Abuse

**Risk**: MEDIUM — The `GITHUB_TOKEN` (rhods-ci-bot) has write access to repos. If compromised:

- Read private repo contents
- Create/modify/delete branches
- Create PRs with arbitrary content
- Access GitHub Actions secrets

**Current mitigations:**
- Token stored in K8s Secret (namespace-scoped access)
- Bot account (not personal) — limited blast radius
- Token from Vault (can be rotated)

**Needed mitigations:**

| Phase | Mitigation | Priority |
|-------|-----------|----------|
| S1 | Audit all GitHub API calls — log method, repo, path | HIGH |
| S1 | Use fine-grained PAT instead of classic token (scope to specific repos) | HIGH |
| S2 | Token rotation: auto-rotate via Vault every 90 days | MEDIUM |
| S2 | Separate read-only token for monitoring vs write token for PRs | MEDIUM |

---

## T5: Jira/Slack Injection

**Risk**: MEDIUM — AI-generated content is posted to Jira tickets and Slack messages. An attacker could craft failures that produce analysis containing:

- Jira markup injection (links, mentions, macros)
- Slack mrkdwn injection (mentions, links, formatting)
- Social engineering content targeting oncall engineers

**Current mitigations:**
- Jira comments use plain text format
- Slack exports are pre-formatted templates

**Needed mitigations:**

| Phase | Mitigation | Priority |
|-------|-----------|----------|
| S1 | Sanitize AI output before Jira posting: strip Jira wiki markup, @mentions | HIGH |
| S1 | Sanitize AI output before Slack: strip @here, @channel, mrkdwn links | HIGH |
| S2 | Rate limit Jira ticket creation (max N per hour) | MEDIUM |
| S2 | Template-only output: AI fills fields in a fixed template, can't add arbitrary text | MEDIUM |

---

## T6: AUTONOMOUS_MODE Activation

**Risk**: HIGH — `AUTONOMOUS_MODE=true` enables automatic PR creation and skill execution. Must be carefully controlled.

**Current mitigations:**
- Env var check in worker pipeline (requires_env)
- Env var check in auto_fix.py

**Needed mitigations:**

| Phase | Mitigation | Priority |
|-------|-----------|----------|
| S1 | AUTONOMOUS_MODE can only be set via runtime_config API (not env var alone) | CRITICAL |
| S1 | Activation requires explicit confirmation: `ic config set autonomous true --confirm` | CRITICAL |
| S1 | Log every activation/deactivation with timestamp and user | HIGH |
| S2 | Time-limited activation: auto-deactivates after N hours | MEDIUM |
| S2 | Require minimum test coverage before allowing autonomous fixes | MEDIUM |

---

## T7: Skill Execution Security

**Risk**: HIGH — Skills execute arbitrary code via subprocess. Even with risk classification:

- Medium-risk skills run with user's full permissions
- Code blocks extracted from SKILL.md may be manipulated
- Skills from external git repos could be backdoored

**Current mitigations:**
- Risk classification (low/medium/high)
- Confirmation prompt for medium/high risk
- Container sandbox for high-risk skills (K8s Job)
- Static security analysis (validator.py)
- `# ic:skip` markers for non-executable blocks

**Needed mitigations:**

| Phase | Mitigation | Priority |
|-------|-----------|----------|
| S1 | Skills from external repos: require signed commits or pinned SHA | HIGH |
| S1 | Sandbox ALL skill execution (not just high-risk) — use container by default | HIGH |
| S2 | Skill execution audit log: command, user, output, duration | MEDIUM |
| S2 | Network policy for sandbox pods: restrict egress to specific endpoints | MEDIUM |
| S3 | Skill content hash verification: detect tampering between registration and execution | LOW |

---

## T8: File and Branch Scope Limits

**Risk**: MEDIUM — Auto-fix PRs could modify any file in a repo. Needs guardrails.

**Needed mitigations:**

| Phase | Mitigation | Priority |
|-------|-----------|----------|
| S1 | File allowlist per repo: only modify known build files | CRITICAL |
| S1 | Max file size for modifications: 10KB per file, 5 files per PR | HIGH |
| S1 | Branch protection: only create branches matching `ci-autohealing/*` pattern | HIGH |
| S1 | Never modify: `.github/workflows/`, `OWNERS`, `LICENSE`, `SECURITY.md` | CRITICAL |
| S2 | Diff size limit: max 500 lines changed per PR | MEDIUM |

---

## T9: API Authentication and Authorization

**Risk**: MEDIUM — The API uses a single shared `IC_API_KEY`. No per-user auth.

**Current mitigations:**
- Bearer token auth on all endpoints except /health
- VPN-only access (internal Red Hat network)
- K8s Secret for token storage

**Needed mitigations:**

| Phase | Mitigation | Priority |
|-------|-----------|----------|
| S2 | Per-user API keys with role-based access (read-only vs admin) | MEDIUM |
| S2 | Write endpoints (POST /analyses, POST /triage) require elevated permissions | MEDIUM |
| S3 | OIDC integration with Red Hat SSO for user identity | LOW |
| S3 | API request logging with user identity | LOW |

---

## T10: Database and Blob Storage Security

**Risk**: MEDIUM — PostgreSQL and MinIO contain sensitive data (logs, tokens in commit context, analysis results).

**Current mitigations:**
- DB credentials in K8s Secret
- MinIO credentials in K8s Secret
- Namespace-scoped access

**Needed mitigations:**

| Phase | Mitigation | Priority |
|-------|-----------|----------|
| S2 | Encrypt sensitive columns (build_logs, commit_context) at rest | MEDIUM |
| S2 | MinIO bucket policy: restrict access to API server ServiceAccount only | MEDIUM |
| S3 | DB connection encryption (TLS) | LOW |
| S3 | Backup encryption | LOW |

---

## Implementation Phases

### Phase S1: Critical Security Hardening (next)

**Goal**: Prevent the most damaging attacks. No new features — pure security.

- [ ] Secret scanning/redaction in stored logs and AI output
- [ ] Sanitize AI-generated content before Jira/Slack
- [ ] Autonomous PR scope limits: repo allowlist, file allowlist, branch naming
- [ ] GitHub API call audit logging
- [ ] AUTONOMOUS_MODE activation controls
- [ ] Skill execution: pin to SHA, sandbox by default
- [ ] File/branch modification limits for auto-fix PRs
- [ ] Strip ANSI escape codes from stored logs
- [ ] Fine-grained GitHub PAT (replace classic token)
- [ ] Never-modify file list (.github/workflows, OWNERS, LICENSE)

### Phase S2: Defense in Depth

**Goal**: Add layers of protection and monitoring.

- [ ] Per-user API keys with RBAC
- [ ] Log access auditing
- [ ] Token rotation via Vault
- [ ] Separate read/write GitHub tokens
- [ ] Rate limits on Jira/PR creation
- [ ] Template-only AI output for Jira/Slack
- [ ] Network policies for sandbox pods
- [ ] Time-limited AUTONOMOUS_MODE
- [ ] Encrypted sensitive DB columns
- [ ] PR dry-run mode (draft PRs by default)

### Phase S3: Long-term Hardening

**Goal**: Enterprise-grade security posture.

- [ ] OIDC integration for API auth
- [ ] Skill content hash verification
- [ ] Content Security Policy for web rendering
- [ ] DB/blob backup encryption
- [ ] DB TLS connections
- [ ] Two-person PR approval requirement
- [ ] Full API request audit trail with user identity

---

## Principles

- **Defense in depth**: Multiple layers, never rely on a single control
- **Least privilege**: Each component gets minimum necessary permissions
- **Fail secure**: If a security check fails, deny the action
- **Audit everything**: Log security-relevant actions with who, what, when
- **No secrets in logs**: Redact before storage, never display raw
- **Human in the loop**: Autonomous actions require explicit opt-in with limits
