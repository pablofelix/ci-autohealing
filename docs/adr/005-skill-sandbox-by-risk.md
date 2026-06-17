# ADR-005: Skill Sandbox Based on Risk Level

**Date:** 2026-06-17
**Status:** Accepted

## Decision

Skills execute in subprocess (low/medium risk) or K8s Job container (high risk). Env vars are masked — only declared vars are passed.

## Context

Skills run arbitrary code from SKILL.md files. External repos can be added by anyone (`ic skills add https://repo.git`). Not all users understand the security implications.

## Rationale

- Subprocess for low-risk: 0.1s startup vs 30s for container — critical for interactive use
- Container for high-risk: file/network isolation prevents node compromise
- `get_safe_env()` blocks undeclared tokens — skill can't access GITHUB_TOKEN unless it declares `requires-env: [GITHUB_TOKEN]`
- Static validation at registration catches secrets and destructive patterns
- Risk auto-classified from required tools (rm → high, jq → low, git → medium)

## When to Revisit

- **Regulatory** — must containerize all execution
- **External untrusted skills** — add approval workflow + network sandbox (Phase S2)
- **Performance** — if container startup overhead is unacceptable for batch skill execution
