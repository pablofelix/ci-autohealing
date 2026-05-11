# ADR-006: Conforma Fix Strategy

**Date:** 2026-05-11
**Status:** Accepted

## Context

Conforma (Enterprise Contract) violations require a different resolution strategy than build failures. Build failures are caused by code defects or infrastructure problems — the fix is a code change, and the right tool is an LLM that reads the logs and produces a diff. Conforma violations are caused by policy non-compliance — most fixes are mechanical and well-specified (update a digest, add a label, change a parameter value), and many require human decisions that no LLM can make (policy exceptions from ProdSec, CPE values from Release Engineering, legal agreements with vendors).

Three design decisions were made to handle this difference:

1. **Separate fix code paths** — conforma fixers are distinct from the build failure fixer
2. **Jira-first flow** — creating a ticket precedes any automated PR, not the other way around
3. **Deterministic fixers for known violations** — LLM is reserved for build failures; conforma fixers are rule-specific, mechanical, and LLM-free

---

## Decision

### 1. Separate fix code paths

Build failures and Conforma violations share the same entry point (`fix_generator.py`) and PR infrastructure (`_push_pr_and_record`, `GitHubClient`), but diverge completely at the strategy level:

| | Build failures | Conforma violations |
|---|---|---|
| Fix source | LLM generates JSON spec | Deterministic per-category function |
| Fix target | Any file Claude recommends | Category-specific (`.tekton/`, Containerfile, etc.) |
| Entry point | `run_pr_mode()` | `run_conforma_*_mode()` |
| Category routing | Not needed | `main()` peeks at DB category to dispatch |
| LLM call | Required | None |

The category routing in `main()` is a simple if-chain: read `ai_analysis.failure_category` from DB, dispatch to the matching `run_conforma_*_mode()` function. Unknown categories fall through to `run_conforma_pr_mode()` (the deprecated-task fixer) as a generic fallback.

This separation was chosen over a unified fixer because:
- Conforma fix logic is deterministic and category-specific — no LLM prompt needed
- Mixing LLM-based and deterministic paths in one function produces confused abstraction
- Each conforma mode function can be tested independently with known inputs

### 2. Jira-first flow

In `ic fix <conforma-component>`, the interactive menu creates a Jira ticket *before* offering to generate a PR. The sequence is:

```
ic fix <component>   →   [show conforma analysis]
                     →   1) Draft Jira ticket → edit → optional POST
                     →   After Jira: "Also generate a PR fix?" (if can_auto_fix=true)
                     →   PR dry-run → push
                     →   Slack notification
```

Compare to the build failure flow, where PR is the primary action and Jira is secondary.

The Jira-first order was chosen because:
- Most Conforma violations, even auto-fixable ones, benefit from a Jira trail: timestamps, stakeholder visibility, and audit evidence for compliance discussions
- A Jira ticket is meaningful standalone even if no PR is generated (e.g., when the fix is a policy exception rather than a code change)
- PRs that fix Conforma violations often block the release of multiple components — the Jira provides a coordination point
- Starting with Jira gives the operator a chance to add context before the PR is raised, so reviewers have the full picture

The PR step remains optional — if the violation requires a policy exception or a human decision, the operator creates the Jira ticket and skips the PR.

Jira creation requires `--execute` to actually POST (dry-run by default), following the same confirmation pattern used by GitHub PR creation.

### 3. Deterministic fixers per violation type

Each auto-fixable category has its own function with a fixed algorithm. No LLM is involved:

| Category | Fixer function | Algorithm |
|---|---|---|
| `policy_deprecated_task` | `run_conforma_pr_mode()` | Parse old→new `oci://...@sha256:` pairs from `violation_details.solution`; substitute in `.tekton/*.yaml` |
| `policy_hermetic_build` | `run_conforma_hermetic_mode()` | Regex-replace `hermetic: false` → `"true"` in `.tekton/*.yaml`; search `konflux-central` first, then component repo |
| `policy_sbom_vendor_label` | `run_conforma_sbom_vendor_label_mode()` | Insert `LABEL vendor="Red Hat, Inc."` into Containerfile after last existing LABEL line |
| `policy_rpm_repository` | `run_conforma_rpm_repo_id_mode()` | Regex-replace generic repo section IDs (`[ubi-N-foo-rpms]`) with arch-specific format (`[ubi-N-for-$basearch-foo-rpms]`) in `rpms.in.yaml` and `*.repo` files |
| `policy_unpinned_task` | `run_conforma_unpinned_task_mode()` | Find floating `quay.io/repo:tag` refs (no `@sha256:`); resolve current digest via quay.io v2 API (`GET /v2/{repo}/manifests/{tag}`, read `Docker-Content-Digest` header); append `@sha256:<digest>` |
| `policy_untrusted_image` | `run_conforma_untrusted_image_mode()` | Extract flagged pinned refs from `violation_details`; re-resolve tag to current digest via quay.io v2 API; substitute in `.tekton/*.yaml` and Containerfile candidates |

**Why no LLM for these?** Each fix is fully specified by the violation data and a known algorithm. The violation `attestation_task_bundle.trusted_task` tells you exactly which bundle refs are outdated; the fix is to update them to the current digest. The violation `image_labels.labels_required` tells you exactly which label is missing; the fix is to insert it. An LLM adds latency and cost without improving correctness for these mechanical transforms.

**Violations kept at Jira-only** (no auto-fixer, `can_auto_fix=false`):

| Category | Reason |
|---|---|
| `policy_signing_key` | Requires legal agreement with the software vendor or sourcing from Red Hat-signed alternative |
| `policy_package_source` | Requires vendoring or approved-source alternative — architectural decision |
| `policy_cpe_label` | CPE value is product-specific and must be confirmed with Release Engineering |
| `policy_source_image` | Requires adding a Tekton task and testing the pipeline change |
| `policy_fips_check` | Often a false positive on push builds; requires investigation before any change |
| `policy_version_label` | Fixed by rebuild, not code change — no PR needed |

The `can_auto_fix` flag in `ai_analysis` is the contract between the analyzer (which sets it) and the fixer (which checks it before offering the PR option in `ic fix`). The flag is set per-analysis based on the violation type; the fixer never hard-codes which categories are auto-fixable.

### Shared PR infrastructure

All six conforma mode functions use `_push_pr_and_record()` for the GitHub write path:

```
get branch SHA → create branch → push each file → create PR → record resolution_attempt
```

The only varying elements per fixer are:
- `changed_files` dict (path → (old_content, new_content))
- `make_commit_msg` callable (produces commit message per file path)
- `pr_title`, `pr_body`, `changes_description`

This eliminates ~40 lines of duplicated GitHub API calls that would otherwise appear in each mode function.

---

## Consequences

**Positive:**
- Each deterministic fixer can be unit-tested with static inputs (no LLM mock needed)
- New violation categories add a single function + one if-branch in `main()` — no changes to routing or infrastructure
- Jira-first flow ensures a paper trail for every conforma remediation, supporting future audits
- Operator retains full control: every external write (Jira POST, GitHub PR) requires explicit opt-in (`--execute`)
- Zero LLM cost for conforma PRs; latency is bounded by GitHub API calls and quay.io digest resolution

**Negative:**
- Deterministic fixers only work when `violation_details` contains the structured data the algorithm expects — if the Conforma violation format changes, fixers silently produce empty diffs rather than gracefully degrading
- The quay.io API-based fixers (`policy_unpinned_task`, `policy_untrusted_image`) fail silently when VPN access is not available or quay.io rate-limits the request; unresolvable refs are left in place with a warning in the PR body
- Jira-first flow adds a mandatory interactive step even for violations where the operator only wants the PR — there is no `ic fix <component> --pr-only` shortcut
- The category dispatch if-chain in `main()` must be kept in sync with `ConformaAnalysisResult.failure_category` in `models.py`; a mismatch causes the wrong fixer to run (or falls through to the deprecated-task fixer unexpectedly)
