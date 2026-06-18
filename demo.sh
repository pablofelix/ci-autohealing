#!/bin/bash
# demo.sh — Self-contained demo of the ic CLI tool
# All output is pre-recorded — no database, cluster, or environment needed.
#
# Usage:
#   ./demo.sh          Interactive — press Enter to advance
#   ./demo.sh --auto   Auto-play — runs with timed delays (for recording)

set -euo pipefail

# ─── Mode ────────────────────────────────────────────────────────────
AUTO=false
[[ "${1:-}" == "--auto" ]] && AUTO=true

# ─── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'
WHITE='\033[1;37m'
HEADER='\033[1;38;5;110m'
MAGENTA='\033[0;35m'
BG_RED='\033[41m'
BG_GREEN='\033[42m'
BG_CYAN='\033[46m'

# ─── Helpers ─────────────────────────────────────────────────────────

type_cmd() {
    local cmd="$1"
    echo -ne "  ${DIM}\$${NC} ${GREEN}"
    for (( i=0; i<${#cmd}; i++ )); do
        echo -n "${cmd:$i:1}"
        local delay=$(( RANDOM % 40 + 20 ))
        sleep "0.0${delay}"
    done
    echo -e "${NC}"
    sleep 0.3
}

show_output() {
    echo ""
    echo -e "$1"
    echo ""
}

advance() {
    local delay="${1:-5}"
    if [ "$AUTO" = true ]; then
        sleep "$delay"
    else
        echo ""
        echo -ne "  ${DIM}Press Enter to continue...${NC}"
        read -r
    fi
}

section_banner() {
    local num="$1" title="$2"
    clear
    echo ""
    echo -e "${HEADER}  ╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${HEADER}  ║${NC}  ${WHITE}ic${NC} ${DIM}— CI/CD Pipeline Intelligence${NC}                             ${HEADER}║${NC}"
    echo -e "${HEADER}  ╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${HEADER}${num}. ${title}${NC}"
    echo ""
}

narrate() {
    echo -e "  ${DIM}$1${NC}"
}

highlight() {
    echo -e "  ${BOLD}$1${NC}"
}

callout() {
    echo ""
    echo -e "  ${MAGENTA}▌${NC} ${BOLD}$1${NC}"
    echo ""
}

# ─── Pre-recorded outputs ───────────────────────────────────────────

OUTPUT_HELP=$(cat <<ENDOFOUTPUT
  ${BOLD}IC${NC} — CI/CD Pipeline Intelligence
  kubectl-style interface for Konflux CI/CD monitoring

  ${BOLD}USAGE:${NC}
      ic get <resource> [name] [options]
      ic describe <resource> <name>
      ic <number>                              Shortcut: describe Nth alert from list

  ${BOLD}RESOURCES:${NC}

    ${CYAN}components${NC}           Components with build failures
    ${CYAN}alerts${NC}               Unified view: build failures + Conforma violations
    ${CYAN}exceptions${NC}           Policy exceptions expiring soon or already expired
    ${CYAN}conforma${NC}             Conforma (Enterprise Contract) test failures
    ${CYAN}pipelineruns, pr${NC}     PipelineRun failures
    ${CYAN}apps${NC}                 Available applications (versions)
    ${CYAN}releases${NC}             Release CRs (push to stage/prod status)

  ${BOLD}COMMANDS:${NC}

    ${CYAN}get${NC} <resource>       List or filter resources
    ${CYAN}describe${NC} <resource>  Full details for a specific resource
    ${CYAN}report${NC} <resource>    Daily standup reports (build, conforma)
    ${CYAN}release${NC} <action>     Release pipeline management
    ${CYAN}ai${NC} <action>          AI-powered analysis and fixes
    ${CYAN}export${NC} <N> <format>  Export failure as Jira/Slack/markdown/json
    ${CYAN}fix${NC} <N>              Interactive triage: analyze → PR, Jira, or Slack
    ${CYAN}dashboard${NC}            Operational dashboard with metrics
    ${CYAN}triage${NC} <action>       Persistent triage tracking across sessions
    ${CYAN}skills${NC} <action>       Skill execution with sandbox isolation
    ${CYAN}config${NC} <action>       Mode switching (local/cluster), watched apps
ENDOFOUTPUT
)

OUTPUT_APPS=$(cat <<ENDOFOUTPUT
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}Available Applications${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

      acme-v2-0 — 263 records
      acme-v2-0-ea-1 — 3 records
    ${GREEN}✓${NC} ${BOLD}acme-v2-1-ea-1${NC} (current) — 148 records
      acme-v2-1-ea-2 — 16 records

  Total: ${BOLD}30 applications${NC}

  Switch: ${CYAN}ic config set-app <app-name>${NC}
ENDOFOUTPUT
)

OUTPUT_ALERTS=$(cat <<ENDOFOUTPUT
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}Alerts — acme-v2-1-ea-1${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Last sync: 2026-05-25 13:22:25, 12m ago

  ${RED}❄ PIPELINE FROZEN${NC} until May 25 — Memorial Day

  ${BOLD}Build Failures (2):${NC}
   # │                   Component                   │   Status    │ Failed │ Builds │ JIRA
  ───┼───────────────────────────────────────────────┼─────────────┼────────┼────────┼──────
   ${WHITE}1${NC} │ acme-api-registry-sync-v3-5-ea-1 │ ${RED}Push Failed${NC} │ 25 May │      7 │ -
   ${WHITE}2${NC} │ acme-inference-operator-v3-5-ea-1        │ ${RED}Push Failed${NC} │ 20 May │      2 │ -

  ${BOLD}Conforma Failures (5):${NC}
    #    Component                                Policy      Viol   Warn Exception              Since  JIRA
    ───  ──────────────────────────────────────── ───────── ────── ────── ────────────────────── ────── ────────
    ${WHITE}3${NC}    acme-fbc-fragment-v3-5-ea-1             ${RED}reg-prod${NC}      22     30 -                      25 May -
    ${WHITE}4${NC}    acme-openshift-chart-v3-5-ea-1        ${RED}reg-prod${NC}      16     12 -                      21 May -
    ${WHITE}5${NC}    acme-xks-chart-v3-5-ea-1              ${RED}reg-prod${NC}      16     10 -                      25 May -
    ${WHITE}6${NC}    acme-fbc-fragment-v3-5-ea-1             ${RED}fbc-prod${NC}      10      5 -                      25 May -
    ${WHITE}7${NC}    acme-core-engine-v3-5-ea-1                   ${RED}reg-prod${NC}       4     28 -                      18 May -

  ${BOLD}Release Failures (2):${NC}
    #    Release                                      Type           Target  Status
    ───  ──────────────────────────────────────────── ────────────── ──────  ──────────
    ${WHITE}8${NC}    acme-fbc-addon-ocp-419-stage-1779415746     FBC Addon      Stage   ${RED}✗ Failed${NC}
    ${WHITE}9${NC}    acme-v2-1-ea-1-stage-1779397395-1-2-3       Components     Stage   ${RED}✗ Failed${NC} (verify-conforma)

  ${BOLD}In Progress (3):${NC}
    Component                                Type       Started            Duration
    ──────────────────────────────────────── ────────── ────────────────── ──────────
    acme-automl-v3-5-ea-1                     Build      25 May 14:53       42m
    acme-autorag-v3-5-ea-1                    Build      25 May 14:53       42m
    acme-operator-v3-5-ea-1                   Build      25 May 15:12       22m

  Total alerts: ${RED}${BOLD}9${NC} (2 builds, 5 conforma, 2 releases)
ENDOFOUTPUT
)

OUTPUT_ALERTS_GROUP=$(cat <<ENDOFOUTPUT
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}Alerts by Root Cause — acme-v2-1-ea-1${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ${BOLD}Build Failures:${NC}

    ${RED}build-images / Task Failure${NC} (1 component)
      ${DIM}"step-build exited with code 1: Error"${NC}
      acme-api-registry-sync-v3-5-ea-1

    ${RED}acme-init / Task Failure${NC} (1 component)
      ${DIM}"failed to create task run pod"${NC}
      acme-inference-operator-v3-5-ea-1

  ${BOLD}Conforma Failures:${NC}

    ${RED}conforma-registry-acme-prod-v3-5-ea-1${NC} (4 components, ${BOLD}58 total violations${NC})
      acme-core-engine(4), acme-openshift-chart(16),
      acme-xks-chart(16), acme-fbc-fragment(22)

    ${RED}conforma-fbc-acme-prod-v3-5-ea-1${NC} (1 component, 10 total violations)
      acme-fbc-fragment(10)
ENDOFOUTPUT
)

OUTPUT_DESCRIBE_1=$(cat <<ENDOFOUTPUT
  ${DIM}Build failure #1 of 9 alerts${NC}

  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}Component: acme-api-registry-sync-v3-5-ea-1${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ${BOLD}Summary:${NC}
    Total Failures: ${RED}7${NC}
    Failures with Logs: 5 / 7
    First Failure: 2026-05-19 15:21:06
    Latest Failure: 2026-05-25 06:16:06

  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}Latest PipelineRun${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  PipelineRun: acme-api-registry-sync-v3-5-ea-1-on-push-hj9zn
  Status: ${RED}Failed${NC}
  Repository: https://github.com/acme-org/api-registry
  Branch: acme-2.1-ea.1
  Commit:      ${CYAN}eca2830b${NC}
  Author:      jsmith
  Message:     Merge pull request #1305 — Fix missing dependency-signing package

  ${BOLD}Failure Info:${NC}
    Failed Step: ${RED}build-images${NC}
    Error Type:  Task Failure
    Error:       step-build exited with code 1: Error
    Tasks:       Completed: 5 (Failed: 1, Cancelled 0), Skipped: 18

  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}Build History (Tekton Results)${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  2026-05-21 21:42  ${RED}✗ failed${NC}     (build-images, eca2830b)
  2026-05-21 18:05  ${GREEN}✓ succeeded${NC}  (e646264a)
  2026-05-21 13:27  ${GREEN}✓ succeeded${NC}  (2687c6f9)
  2026-05-21 00:27  ${GREEN}✓ succeeded${NC}  (ec809b0b)
  2026-05-20 22:13  ${RED}✗ failed${NC}     (send-slack-notification)
  2026-05-20 17:30  ${RED}✗ failed${NC}     (send-slack-notification)

  Status: ${RED}FAILING${NC} (latest build: Failed)

  ${YELLOW}[!] Run: ic ai analyze acme-api-registry-sync-v3-5-ea-1${NC}
ENDOFOUTPUT
)

OUTPUT_AI_ANALYZE=$(cat <<ENDOFOUTPUT
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}AI Analysis — acme-api-registry-sync-v3-5-ea-1${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ${DIM}Reading 200KB of build logs...${NC}
  ${DIM}Analyzing failure context (commit diff + Dockerfile + .tekton)...${NC}
  ${DIM}Matching against 30 known error patterns...${NC}

  ${GREEN}✓ Analysis complete${NC}

  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}Results${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ${BOLD}Category:${NC}     dependency_issue
  ${BOLD}Confidence:${NC}   ${GREEN}78%${NC}
  ${BOLD}Can Auto-Fix:${NC} ${RED}No${NC} (requires manual cache regeneration)

  ${BOLD}Root Cause:${NC}
    The build is failing because the prefetched RPM dependencies in the
    cachi2 cache are mismatched with versions in the UBI 9 appstream repo.
    The commit bumped the base image digest, but the cachi2 prefetch was
    locked to the previous base image's package versions.

  ${BOLD}Recommended Fix:${NC}
    Re-run the prefetch-dependencies task to regenerate the cachi2 cache
    matching the new base image digest.

  ${BOLD}Pattern Match:${NC}
    ${CYAN}cachi2_cache_mismatch${NC} — seen 12 times across 5 applications
    Avg confidence: 73%  |  Avg resolution: 2.1 days

  Cost: ${DIM}\$0.06 (1 API call)${NC}
ENDOFOUTPUT
)

OUTPUT_AI_STATUS=$(cat <<ENDOFOUTPUT
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}AI Analysis Status${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ${BOLD}Build Failures:${NC}
    Pending:        ${YELLOW}2${NC} awaiting analysis
    Analyzed:       ${GREEN}3${NC} (last 30 days)
      High conf (>=70%): 2
      Low conf  (<70%):  1  ${DIM}<- needs human review${NC}
    Auto-fixable:   0 (0%)

  ${BOLD}Conforma Violations:${NC}
    Pending:        ${YELLOW}4${NC} awaiting analysis
    Analyzed:       ${GREEN}9${NC} (last 30 days)
      High conf (>=70%): 6
      Low conf  (<70%):  3  ${DIM}<- needs human review${NC}
    Auto-fixable:   0 (0%)

  Total cost:   ${BOLD}\$0.73${NC} (last 30 days)

   #  │ Component                                     │ Type     │ Category               │ Conf │ Fix
  ────┼─────────────────────────────────────────────────┼──────────┼────────────────────────┼──────┼────
   1  │ acme-fbc-fragment-v3-5-ea-1                  │ Conforma │ ${CYAN}config_error${NC}           │ ${GREEN}82%${NC}  │ ✗
   2  │ acme-api-registry-sync-v3-5-ea-1 │ Build    │ ${CYAN}dependency_issue${NC}       │ ${GREEN}78%${NC}  │ ✗
   3  │ acme-fbc-fragment-v3-5-ea-1                  │ Conforma │ ${YELLOW}policy_fips_check${NC}      │ ${YELLOW}65%${NC}  │ ✗
   4  │ acme-automl-v3-5-ea-1                          │ Build    │ ${CYAN}infrastructure${NC}         │ ${GREEN}82%${NC}  │ ✗
   5  │ acme-xks-chart-v3-5-ea-1                   │ Conforma │ ${CYAN}config_error${NC}           │ ${GREEN}82%${NC}  │ ✗
   6  │ acme-fbc-fragment-v3-5-ea-1                  │ Conforma │ ${CYAN}policy_untrusted_image${NC} │ ${GREEN}82%${NC}  │ ✗
   7  │ acme-operator-v3-5-ea-1                        │ Build    │ ${YELLOW}build_error${NC}            │ ${YELLOW}30%${NC}  │ ✗
  (10 rows)

  ${CYAN}Run 'ic ai analyze --all' to analyze all pending failures${NC}
ENDOFOUTPUT
)

OUTPUT_FIX=$(cat <<ENDOFOUTPUT
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}Fix Triage — acme-api-registry-sync-v3-5-ea-1${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ${BOLD}Type:${NC}    build
  ${BOLD}Error:${NC}   Task Failure at step build-images
  ${BOLD}Age:${NC}     6 days (7 occurrences)

  ${BOLD}AI Analysis:${NC}
    Category:   dependency_issue
    Confidence: 78%
    Can fix:    no
    Fix hint:   Re-run the prefetch-dependencies task to regenerate the cachi2 cache...

  ${YELLOW}Recommendation: [2] Create Jira ticket  (needs human review)${NC}

  ${BOLD}What do you want to do?${NC}
    [1] Generate PR fix (dry-run — shows diff, no push)
    [2] Create Jira ticket (review + edit before posting)
    [3] Send Slack notification
    [4] Skip

  ${DIM}Choice [1-4]:${NC} ${GREEN}2${NC}

  ${GREEN}✓ Jira ticket created: PROJECT-39812${NC}
    Summary: Build failure: acme-api-registry-sync-v3-5-ea-1
    Priority: Major
    Assignee: auto-assigned to component owner (jsmith)
    Labels: ci-auto-healing, build-failure, dependency_issue

  ${DIM}Send Slack notification? [y/N]:${NC} ${GREEN}y${NC}

  ${GREEN}✓ Slack message sent to #acme-ci-alerts${NC}
    Includes: error details, AI root cause, Jira link PROJECT-39812

  ${GREEN}✓ Triage item #12 created${NC}
    Component: acme-api-registry-sync-v3-5-ea-1
    Group: dependency_issue
    Jira: PROJECT-39812
    Slack: #acme-ci-alerts thread
ENDOFOUTPUT
)

OUTPUT_DESCRIBE_CONFORMA=$(cat <<ENDOFOUTPUT
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}Conforma Results: acme-core-engine-v3-5-ea-1${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Scenario:    conforma-registry-acme-prod-v3-5-ea-1-single-component
  Result:      ${RED}FAILURE${NC} (4 violations, 28 warnings, 508 successes)
  Repository:  https://github.com/acme-org/core-engine
  Commit:      ${CYAN}8f2d9d17${NC}
  Image:       quay.io/acme/core-engine-rhel9@sha256:c10be78...

  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}Violations${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Components:
  - acme-core-engine-v3-5-ea-1 (${BOLD}arm64, amd64, ppc64le${NC} + index)
    Violations: 1 each, Warnings: 7 each, Successes: 127 each

  ${RED}✕ [Violation] hermetic_task.hermetic${NC}
    Task '${CYAN}buildah-remote-oci-ta${NC}' was not invoked with the hermetic
    parameter set.
    Title: Task called with hermetic param set
    Description: Verify the task in the PipelineRun attestation was
    invoked with the proper parameters to make the task execution
    hermetic.

  ${BOLD}AI Analysis:${NC} ${GREEN}82% confidence${NC}
    Category: ${CYAN}config_error${NC}
    Fix: Add ${CYAN}hermetic: "true"${NC} to the buildah-remote-oci-ta task params
    in the component's .tekton PipelineRun definition.
ENDOFOUTPUT
)

OUTPUT_POLICY_GAP=$(cat <<ENDOFOUTPUT
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}Stage → Prod EC Policy Gap${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    ${BOLD}Charts${NC}  ${RED}8 at risk${NC}
      Stage: ${CYAN}registry-acme-chart-stage${NC} (8 exceptions, 0 temporary)
      Prod:  ${CYAN}registry-acme-chart-prod${NC}  (0 exceptions, 0 temporary)
      Common: 0  │  Stage-only: ${RED}8${NC}  │  Prod-only: 0

      ${YELLOW}Rules excepted in stage but NOT in prod:${NC}
        base_image_registries.allowed_registries_provided     ${CYAN}permanent${NC}
        cve.cve_results_found                                 ${CYAN}permanent${NC}
        labels.required_labels                                ${CYAN}permanent${NC}
        sbom.found                                            ${CYAN}permanent${NC}
        tasks.required_tasks_found                            ${CYAN}permanent${NC}
        ${DIM}... and 3 more${NC}

    ${BOLD}Components${NC}  ${RED}39 at risk${NC}
      Stage: ${CYAN}registry-acme-stage${NC} (41 exceptions, 6 temporary)
      Prod:  ${CYAN}registry-acme-prod${NC}  (3 exceptions, 3 temporary)
      Common: 1  │  Stage-only: ${RED}39${NC}  │  Prod-only: 2

      ${YELLOW}Rules excepted in stage but NOT in prod:${NC}
        hermetic_task.hermetic                                ${CYAN}permanent${NC}
        hermetic_task                                         ${CYAN}permanent${NC}
        trusted_task.trusted                                  ${CYAN}permanent${NC}
        rpm_signature.allowed:unsigned                        ${CYAN}permanent${NC}
        hermetic_task                                         ${GREEN}35d left${NC}
          ${DIM}https://your-jira.example.com/browse/PLATFORM-7552${NC}
        cve.cve_results_found                                 ${GREEN}35d left${NC}
          ${DIM}https://your-jira.example.com/browse/PROJECT-36999${NC}
        ${DIM}... and 33 more${NC}

    ${BOLD}Fbc${NC}  ${RED}3 at risk${NC}
      Stage: ${CYAN}fbc-acme-stage${NC} (7 exceptions, 1 temporary)
      Prod:  ${CYAN}fbc-acme-prod${NC}  (5 exceptions, 2 temporary)
      Common: 4  │  Stage-only: ${RED}3${NC}  │  Prod-only: 1

  ${CYAN}Tip:${NC} Stage-only rules = will fail in prod. Fix before promoting.
       Temporary rules with days left = also expire from stage.
ENDOFOUTPUT
)

OUTPUT_RELEASE_STATUS=$(cat <<ENDOFOUTPUT
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}Release Status — acme-v2-1-ea-1${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Epoch:    ${CYAN}1779415746${NC}
  Snapshot: acme-fbc-fragment-ocp-419-1779415746
  Started:  2026-05-22T02:12:03Z

  ── ${BOLD}Stage${NC} ──────────────────────────────────────────────────
    ○  Components         Not started
    ○  Charts             Not started
    ${GREEN}✓${NC}  FBC OCP 4.19       ${GREEN}Succeeded${NC}   05-22 04:12  (8m)
    ${GREEN}✓${NC}  FBC OCP 4.20       ${GREEN}Succeeded${NC}   05-22 04:12  (8m)
    ${GREEN}✓${NC}  FBC OCP 4.21       ${GREEN}Succeeded${NC}   05-22 04:12  (8m)
  ── ${BOLD}Prod${NC} ──────────────────────────────────────────────────
    ○  Components         Not started
    ○  Charts             Not started
    ○  FBC OCP 4.19       Not started
    ○  FBC OCP 4.20       Not started
    ○  FBC OCP 4.21       Not started

  ── ${BOLD}Summary${NC} ────────────────────────────────────────────────────
    Stage: ${GREEN}3/5${NC} complete
    Prod:  ${YELLOW}0/5${NC} complete
    Elapsed: 3d 11h (in progress)

    → Next step: ${BOLD}stage Components${NC}
ENDOFOUTPUT
)

OUTPUT_READINESS=$(cat <<ENDOFOUTPUT
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}Release Readiness — acme-v2-1-ea-1${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ${BOLD}Epoch:${NC}    ${CYAN}1779415746${NC}  (2026-05-22)
  ${BOLD}Schedule:${NC} Release: ${RED}Jun 17 (22d)${NC}  │  Rel Window: Jun 16 (21d)  │  RC: May 21 (passed)
                    Code Freeze: May 15 (passed)
  ${BOLD}Next:${NC}     acme-2.1.EA2

  ${RED}❄ PIPELINE FROZEN${NC} until May 25 — Memorial Day

  ── ${BOLD}Pipeline${NC} ────────────────────────────────────────────────────
    Stage: 5/5 ${GREEN}✓${NC}
      ${GREEN}✓${NC} Components  ${GREEN}✓${NC} Charts  ${GREEN}✓${NC} FBC OCP 4.19  ${GREEN}✓${NC} FBC OCP 4.20  ${GREEN}✓${NC} FBC OCP 4.21
    Prod:  0/5 ${YELLOW}○${NC}
      ${YELLOW}○${NC} Components  ${YELLOW}○${NC} Charts  ${YELLOW}○${NC} FBC OCP 4.19  ${YELLOW}○${NC} FBC OCP 4.20  ${YELLOW}○${NC} FBC OCP 4.21

  ── ${BOLD}Build Health${NC} — ${YELLOW}22d to release${NC} ────────────────────────────────────
    Failing: ${RED}2${NC}    Fixed (7d): ${GREEN}92${NC}
      ${RED}✗${NC} acme-inference-operator-v3-5-ea-1   (score: -, 0 consecutive)
      ${RED}✗${NC} acme-api-registry-sync-v3-5-ea-1 (score: -, 0 consecutive)
      ${GREEN}✓${NC} acme-pipelines-api-v3-5-ea-1 (fixed May 25)
      ${GREEN}✓${NC} acme-auth-proxy-v3-5-ea-1            (fixed May 25)
      ${GREEN}✓${NC} acme-pipelines-driver-v3-5-ea-1        (fixed May 25)

  ── ${BOLD}Conforma Compliance${NC} — ${YELLOW}22d to release${NC} ─────────────────────────────
      ${RED}✗${NC} acme-core-engine-v3-5-ea-1                   (4 violations, ${RED}NO exception${NC})
      ${RED}✗${NC} acme-openshift-chart-v3-5-ea-1        (16 violations, ${RED}NO exception${NC})
      ${RED}✗${NC} acme-xks-chart-v3-5-ea-1              (16 violations, ${RED}NO exception${NC})
      ${RED}✗${NC} acme-fbc-fragment-v3-5-ea-1             (22 violations, ${RED}NO exception${NC})
    Summary: ${RED}4 blocking${NC}

  ── ${BOLD}Expiring Exceptions${NC} — ${YELLOW}22d to release${NC} ─────────────────────────────
      ${RED}✗${NC} 0d     9 exception(s): sbom_spdx.allowed_package_sources
      ${YELLOW}△${NC} 8d     6 exception(s): cve, hermetic_task, sbom_spdx
      ${YELLOW}△${NC} 9d     2 exception(s): olm.unmapped_references
      ${YELLOW}△${NC} 11d    51 exception(s): hermetic_task, sbom_spdx
      ${GREEN}○${NC} 43d    2 exception(s): test.no_erred_tests, test.no_failed_tests
    ${RED}Warning:${NC} 1 exception(s) expired in the last 7 days

  ── ${BOLD}Verdict${NC} ─────────────────────────────────────────────────────

    ${RED}${BOLD}✗ NOT READY${NC}

    ${BOLD}Blockers:${NC}
      1. Conforma: 4 component(s) have unexcepted violations
      2. Pipeline frozen until May 25 (Memorial Day)

    ${BOLD}Production outlook:${NC} 2 build failure(s) + 4 conforma violation(s) would also block prod

  ${CYAN}Tip:${NC} Use 'ic get policy-gap' for stage vs prod policy differences
ENDOFOUTPUT
)

OUTPUT_DASHBOARD=$(cat <<ENDOFOUTPUT
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}CI Auto-Healing Dashboard${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Application: ${CYAN}acme-v2-1-ea-1${NC}
  Date: 2026-05-25 15:36

  ${BOLD}Analysis Queue${NC}
    Build:    ${GREEN}3 analyzed${NC}  ${YELLOW}2 pending${NC}
    Conforma: ${GREEN}9 analyzed${NC}  ${YELLOW}174 pending${NC}

  ${BOLD}Enrichment Coverage${NC}
    Enriched: ${GREEN}2/2${NC} (100%)  Failed: ${GREEN}0${NC}

  ${BOLD}Pattern Library${NC}
    Patterns: ${BOLD}30${NC} total (16 with confidence data)
    Avg confidence: ${CYAN}73%${NC}  Total occurrences: 54

  ${BOLD}Fix Outcomes (last 30 days)${NC}
    Total: 0   Success: 0   Failed: 0  Pending: 0
    Success rate: ${DIM}0% (no fix attempts yet)${NC}

  ${BOLD}API Costs (last 30 days)${NC}
    Analyses: ${BOLD}33${NC}  Tokens: 620,745  Cost: ${BOLD}\$2.38${NC}
ENDOFOUTPUT
)

OUTPUT_EXPORT_JIRA=$(cat <<ENDOFOUTPUT
  ${DIM}h3. Description of problem${NC}

  Build pipeline failure on component ${BOLD}acme-api-registry-sync-v3-5-ea-1${NC}
  in application ${BOLD}acme-v2-1-ea-1${NC}.
  Error type: ${RED}Task Failure${NC} in pipeline step build-images.
  ${DIM}{code}${NC}
  step-build exited with code 1: Error
  ${DIM}{code}${NC}

  AI analysis (78% confidence) categorizes this as a ${CYAN}dependency_issue${NC} issue.
  The build is failing because the prefetched RPM dependencies in the cachi2 cache
  are mismatched with the versions available in the UBI 9 appstream repository...

  ${DIM}h3. Prerequisites${NC}

  * Component: acme-api-registry-sync-v3-5-ea-1
  * Application: acme-v2-1-ea-1
  * Repository: model-registry (github.com/acme-org/api-registry)
  * Triggering commit: eca2830b by jsmith
  * Commit message: Fix missing dependency-signing package

  ${DIM}h3. Recommended Fix (AI-generated, 78% confidence)${NC}

  Re-run the prefetch-dependencies task to regenerate the cachi2 cache
  matching the new base image digest.
ENDOFOUTPUT
)

OUTPUT_EXPORT_SLACK=$(cat <<ENDOFOUTPUT
  ${RED}:red_circle:${NC} ${BOLD}Build failure on acme-api-registry-sync-v3-5-ea-1${NC}

  Hi team, we have a build failure that could use some attention.

  ${BOLD}Error:${NC} Task Failure in step build-images
  ${BOLD}Failing since:${NC} 25 May (7 occurrences)
  ${BOLD}Last commit:${NC} ${CYAN}eca2830b${NC} by jsmith
  ${BOLD}AI root cause:${NC} cachi2 cache mismatch after base image update

  ${BOLD}Recommended fix:${NC} Re-run prefetch-dependencies to regenerate cache

  ${DIM}:link: View in Konflux  |  :jira: PROJECT-39812${NC}

  cc @jsmith @acme-ci-team — can someone pick this up
  when you get a chance? Happy to provide more context if needed.
ENDOFOUTPUT
)

# ─── Sections ────────────────────────────────────────────────────────

# ── INTRO ────────────────────────────────────────────────────────────

intro_what_is_ic() {
    section_banner "1" "What is ic?"
    echo ""
    echo -e "  ${BOLD}ic${NC} is a kubectl-style CLI for monitoring Konflux CI/CD pipelines."
    echo ""
    narrate "Install: ${CYAN}pip install ic-tool${NC} — works anywhere with Python 3.9+"
    echo ""
    narrate "It tracks three things:"
    echo -e "    ${RED}•${NC} ${BOLD}Build failures${NC}    — why did the container image fail to build?"
    echo -e "    ${YELLOW}•${NC} ${BOLD}Conforma${NC}          — supply-chain security policy violations"
    echo -e "    ${GREEN}•${NC} ${BOLD}Release pipeline${NC}  — is the release progressing or blocked?"
    echo ""
    narrate "And it can ${BOLD}act on them${NC}:"
    echo -e "    ${CYAN}•${NC} AI reads build logs and ${BOLD}identifies the root cause${NC}"
    echo -e "    ${CYAN}•${NC} Executes ${BOLD}remediation skills${NC} in sandboxed environments"
    echo -e "    ${CYAN}•${NC} Generates ${BOLD}PR fixes${NC} automatically for known patterns"
    echo -e "    ${CYAN}•${NC} Creates ${BOLD}Jira tickets${NC} and ${BOLD}Slack alerts${NC} with full context"
    echo -e "    ${CYAN}•${NC} ${BOLD}Tracks triage state${NC} across sessions with persistent items"
    echo ""
    narrate "Three access modes: CLI, MCP server (50+ tools), REST API."
    narrate "Two deploy modes: ${CYAN}ic config use-local${NC} / ${CYAN}ic config use-cluster <url>${NC}"
    advance 6

    callout "Let's start with the command overview:"
    type_cmd "ic"
    show_output "$OUTPUT_HELP"
    advance 7
}

intro_apps() {
    section_banner "2" "What are we monitoring?"
    narrate "Each 'application' groups all container images for one product version."
    narrate "ic tracks multiple versions simultaneously."
    echo ""
    type_cmd "ic get apps"
    show_output "$OUTPUT_APPS"
    narrate "30 application versions tracked. The current one (${GREEN}✓${NC}${DIM}) has 148 failure records."
    narrate "Switch versions with: ic config set-app <name>"
    advance 5
}

intro_alerts() {
    section_banner "3" "What's broken right now?"
    narrate "The alert dashboard is the starting point for daily triage."
    narrate "One unified view: builds + conforma + releases + in-progress."
    echo ""
    type_cmd "ic get alerts"
    show_output "$OUTPUT_ALERTS"
    callout "Key insight: 9 alerts across 3 types."
    narrate "Drill into any alert: ${CYAN}ic describe failure <component>${NC} or ${CYAN}ic describe conforma <component>${NC}"
    advance 8
}

# ── TRACK: BUILD FAILURES ────────────────────────────────────────────

build_group() {
    section_banner "▸ Builds" "Root Cause Grouping"
    narrate "9 alerts — but how many ${BOLD}actual problems${NC} are there?"
    narrate "Group by root cause to collapse duplicates."
    echo ""
    type_cmd "ic get alerts --group"
    show_output "$OUTPUT_ALERTS_GROUP"
    callout "4 conforma failures share the same policy scenario."
    narrate "Fix the policy → fix 4 components at once."
    advance 6
}

build_describe() {
    section_banner "▸ Builds" "Drilling Into a Failure"
    narrate "Drill into a build failure by component name. ic shows everything:"
    narrate "metadata, error, build logs, build history from Tekton Results."
    echo ""
    type_cmd "ic describe failure acme-api-registry-sync-v3-5-ea-1"
    show_output "$OUTPUT_DESCRIBE_1"
    narrate "The build history shows this started failing after commit ${CYAN}eca2830b${NC}."
    narrate "It suggests running AI analysis: ${YELLOW}ic ai analyze acme-api-registry-sync-v3-5-ea-1${NC}"
    advance 7
}

build_ai_analyze() {
    section_banner "▸ Builds" "AI Root Cause Analysis"
    narrate "The AI engine reads build logs (200KB+), commit diffs, Dockerfiles,"
    narrate "and .tekton pipeline configs. It classifies the failure and suggests a fix."
    echo ""
    type_cmd "ic ai analyze acme-api-registry-sync-v3-5-ea-1"
    show_output "$OUTPUT_AI_ANALYZE"
    callout "78% confidence: cachi2 cache mismatch after a base image bump."
    narrate "The AI matched this against a known pattern seen 12 times before."
    narrate "Each analysis costs one LLM call (~\$0.06)."
    advance 8
}

build_fix() {
    section_banner "▸ Builds" "Interactive Triage → Action"
    narrate "Now we know the root cause. Time to act."
    narrate "${CYAN}ic fix${NC} gives you an interactive menu with 4 options:"
    echo -e "    ${BOLD}[1]${NC} Generate PR fix   — AI writes the code change, you review"
    echo -e "    ${BOLD}[2]${NC} Create Jira ticket — pre-filled with AI analysis + context"
    echo -e "    ${BOLD}[3]${NC} Send Slack alert   — notify the team with all details"
    echo -e "    ${BOLD}[4]${NC} Skip               — come back later"
    echo ""
    type_cmd "ic fix acme-api-registry-sync-v3-5-ea-1"
    show_output "$OUTPUT_FIX"
    callout "One command: triage → Jira ticket → Slack notification → tracked."
    narrate "The ticket includes AI root cause, build logs, commit info, and links."
    narrate "The Slack message links back to the Jira ticket."
    narrate "Everything is tracked in ${CYAN}ic triage${NC} — persists across sessions."
    advance 8
}

build_export() {
    section_banner "▸ Builds" "Export Formats"
    narrate "Need more control? ${CYAN}ic export${NC} generates content in multiple formats."
    echo ""
    type_cmd "ic export acme-api-registry-sync-v3-5-ea-1 jira"
    show_output "$OUTPUT_EXPORT_JIRA"
    narrate "That's Jira markup — paste directly into a ticket description."
    advance 5

    callout "Same failure as a Slack message:"
    type_cmd "ic export acme-api-registry-sync-v3-5-ea-1 slack"
    show_output "$OUTPUT_EXPORT_SLACK"
    narrate "Also available: ${CYAN}ic export <component> markdown${NC} (GitHub) and ${CYAN}ic export <component> json${NC} (automation)."
    advance 6
}

# ── TRACK: CONFORMA ──────────────────────────────────────────────────

conforma_describe() {
    section_banner "▸ Conforma" "Policy Violation Details"
    narrate "Conforma (Enterprise Contract) enforces supply-chain security policies."
    narrate "Every container image must pass checks before release."
    narrate "Let's see why ${BOLD}acme-core-engine${NC} is failing:"
    echo ""
    type_cmd "ic describe conforma acme-core-engine-v3-5-ea-1"
    show_output "$OUTPUT_DESCRIBE_CONFORMA"
    narrate "4 violations across 3 architectures (arm64, amd64, ppc64le) + index."
    narrate "All failing the same rule: ${RED}hermetic_task.hermetic${NC}"
    narrate "The AI already suggests the fix: add ${CYAN}hermetic: \"true\"${NC} to the pipeline."
    advance 7
}

conforma_policy_gap() {
    section_banner "▸ Conforma" "Stage vs Prod Policy Gap"
    narrate "Stage and prod use ${BOLD}different${NC} EC policies with different exception sets."
    narrate "A component passing stage can ${RED}fail prod${NC} if its exception only exists in stage."
    narrate "The policy gap shows this risk ${BOLD}before${NC} you try to promote:"
    echo ""
    type_cmd "ic get policy-gap"
    show_output "$OUTPUT_POLICY_GAP"
    callout "39 rules excepted in stage but NOT in prod for Components."
    narrate "5 of those are temporary (35 days left) — they'll also expire from stage."
    narrate "This is the 'will it break in prod?' question answered."
    advance 8
}

# ── TRACK: RELEASES ──────────────────────────────────────────────────

release_status() {
    section_banner "▸ Releases" "Pipeline Status"
    narrate "Releases flow through a multi-step pipeline:"
    narrate "Stage (Components → Charts → FBC per OCP version) then Prod (same steps)."
    echo ""
    type_cmd "ic release status"
    show_output "$OUTPUT_RELEASE_STATUS"
    narrate "FBC fragments for OCP 4.19-4.21 passed stage."
    narrate "Components and Charts haven't started yet → next step."
    advance 6
}

release_readiness() {
    section_banner "▸ Releases" "Can We Ship?"
    narrate "The readiness command is the ${BOLD}go/no-go decision aid${NC}."
    narrate "It combines pipeline, build health, conforma compliance,"
    narrate "expiring exceptions, and schedule countdown into ${BOLD}one verdict${NC}."
    echo ""
    type_cmd "ic release readiness"
    show_output "$OUTPUT_READINESS"
    callout "Verdict: NOT READY — 4 unexcepted conforma violations blocking."
    narrate "Key sections:"
    echo -e "    ${BOLD}Schedule${NC}  — 22 days to release (Jun 17)"
    echo -e "    ${BOLD}Expiring${NC}  — 51 hermetic_task exceptions expire in 11 days"
    echo -e "    ${BOLD}Outlook${NC}   — even if stage passes, these issues would block prod"
    advance 8
}

# ── TRACK: AI & OPERATIONS ───────────────────────────────────────────

ai_status() {
    section_banner "▸ AI" "Analysis Engine Overview"
    narrate "The AI engine is the core of the auto-healing system."
    narrate "It reads build logs, violation details, and commit context,"
    narrate "then classifies failures and matches them against known patterns."
    echo ""
    type_cmd "ic ai status"
    show_output "$OUTPUT_AI_STATUS"
    narrate "10 analyses completed, 6 pending."
    narrate "Pattern library has ${BOLD}30 known error patterns${NC} built from past failures."
    narrate "Total cost tracked: \$0.73 for 30 days of continuous monitoring."
    advance 7
}

dashboard() {
    section_banner "▸ Operations" "Dashboard"
    narrate "The dashboard shows operational health of the auto-healing system itself."
    echo ""
    type_cmd "ic dashboard"
    show_output "$OUTPUT_DASHBOARD"
    narrate "Key metrics:"
    echo -e "    ${BOLD}Analysis queue${NC}  — how much work is pending for the AI"
    echo -e "    ${BOLD}Pattern library${NC} — 30 patterns at 73% avg confidence"
    echo -e "    ${BOLD}API costs${NC}       — \$2.38 for 33 analyses this month"
    echo ""
    narrate "This is the meta-view: monitoring the monitoring system."
    advance 7
}

# ── TRACK: TRIAGE TRACKING ───────────────────────────────────────────

OUTPUT_TRIAGE_SHOW=$(cat <<ENDOFOUTPUT
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}Triage Items — acme-v2-1-ea-1${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   #  │ Component                              │ Group              │ Status │ JIRA          │ Slack
  ────┼────────────────────────────────────────┼────────────────────┼────────┼───────────────┼──────
   12 │ acme-api-registry-sync-v3-5-ea-1       │ dependency_issue   │ ${YELLOW}Open${NC}   │ PROJECT-39812 │ ✓
   13 │ acme-inference-operator-v3-5-ea-1      │ infrastructure     │ ${YELLOW}Open${NC}   │ -             │ -
   14 │ acme-core-engine-v3-5-ea-1             │ hermetic_task      │ ${YELLOW}Open${NC}   │ -             │ -
   14 │ acme-openshift-chart-v3-5-ea-1         │ hermetic_task      │ ${YELLOW}Open${NC}   │ -             │ -
   14 │ acme-xks-chart-v3-5-ea-1              │ hermetic_task      │ ${YELLOW}Open${NC}   │ -             │ -
   15 │ acme-fbc-fragment-v3-5-ea-1            │ fbc_policy         │ ${YELLOW}Open${NC}   │ -             │ -

  Active: ${BOLD}6${NC} items (4 groups)  │  Resolved (7d): ${GREEN}3${NC}
ENDOFOUTPUT
)

triage_tracking() {
    section_banner "▸ Triage" "Persistent Tracking"
    narrate "Every failure you investigate is tracked as a ${BOLD}triage item${NC}."
    narrate "Items persist across sessions — no more losing context."
    echo ""
    type_cmd "ic triage show"
    show_output "$OUTPUT_TRIAGE_SHOW"
    callout "4 groups from 6 items — shared root causes grouped automatically."
    narrate "Track new items: ${CYAN}ic triage track <component> --group \"label\" --cause \"reason\"${NC}"
    narrate "Link to Jira:    ${CYAN}ic triage update 13 --jira PROJECT-39813${NC}"
    narrate "Resolve:         ${CYAN}ic triage resolve 12${NC}"
    narrate ""
    narrate "The MCP equivalent: ${CYAN}mcp__ic__track_triage_item()${NC}, ${CYAN}mcp__ic__update_triage_item()${NC}"
    advance 6
}

# ── TRACK: SKILLS ───────────────────────────────────────────────────

OUTPUT_SKILLS=$(cat <<ENDOFOUTPUT
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}Available Skills${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    Skill                        │ Risk   │ Description
  ──────────────────────────────┼────────┼──────────────────────────────────
    retrigger-build              │ ${GREEN}Low${NC}    │ Retrigger a Konflux PipelineRun
    update-prefetch              │ ${YELLOW}Medium${NC} │ Regenerate cachi2 prefetch cache
    add-hermetic-param           │ ${YELLOW}Medium${NC} │ Add hermetic: true to .tekton pipeline
    create-fix-pr                │ ${RED}High${NC}   │ Create a GitHub PR with AI-generated fix
    request-exception            │ ${RED}High${NC}   │ Submit policy exception via Conforma API

  ${BOLD}Execution Model:${NC}
    ${GREEN}Low${NC}     Subprocess with env masking          (automatic)
    ${YELLOW}Medium${NC}  Subprocess with restricted env        (user prompt)
    ${RED}High${NC}    K8s Job sandbox — isolated container  (explicit approval)

  ${BOLD}Security:${NC}
    • Environment variables masked (only declared vars passed)
    • High-risk skills run in isolated K8s Jobs (no host access)
    • Full execution audit trail (command, args, stdout, exit code)
    • Secrets redacted from all logs before storage
    • API rate limiting (100 read, 10 write per min per key)
ENDOFOUTPUT
)

OUTPUT_SKILL_EXEC=$(cat <<ENDOFOUTPUT
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${BOLD}Skill Execution — update-prefetch${NC}
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Component: acme-api-registry-sync-v3-5-ea-1
  Risk:      ${YELLOW}Medium${NC} (file modification)

  ${DIM}Dry-run: showing what would change...${NC}

  ${BOLD}Changes:${NC}
    .tekton/acme-api-registry-sync-pull-request.yaml
      prefetch-input: ${RED}- old-digest${NC}
      prefetch-input: ${GREEN}+ new-digest${NC}

  ${BOLD}Approve execution? [y/N]:${NC} ${GREEN}y${NC}

  ${DIM}Running with masked environment (3 vars passed)...${NC}
  ${GREEN}✓ Skill completed successfully${NC} (exit 0, 4.2s)

  ${GREEN}✓ Triage item #12 updated${NC} — skill execution logged
ENDOFOUTPUT
)

skills_section() {
    section_banner "▸ Skills" "Executable Remediation"
    narrate "Skills are the ${BOLD}hands${NC} of the system — they go beyond diagnosis."
    narrate "When the AI identifies a root cause, skills can ${BOLD}apply the fix${NC}."
    echo ""
    type_cmd "ic skills list"
    show_output "$OUTPUT_SKILLS"
    callout "Risk-based execution: low → automatic, medium → prompt, high → K8s sandbox."
    narrate "Every skill execution is recorded in the audit trail."
    advance 7

    callout "Let's run a skill:"
    type_cmd "ic fix acme-api-registry-sync-v3-5-ea-1 --skill update-prefetch --dry-run"
    show_output "$OUTPUT_SKILL_EXEC"
    narrate "Dry-run shows the diff first. Then approval. Then execution."
    narrate "Result is linked back to the triage item automatically."
    advance 6
}

# ── WRAP-UP ──────────────────────────────────────────────────────────

wrapup() {
    section_banner "★" "Architecture & What's Possible"
    narrate "Everything in this demo runs on ${BOLD}OpenShift${NC} with three interfaces:"
    echo ""
    echo -e "                     ${BOLD}┌────────────┐${NC}"
    echo -e "                     │ ${CYAN}PostgreSQL${NC} │"
    echo -e "                     └──────┬─────┘"
    echo -e "                            │"
    echo -e "              ┌─────────────┼─────────────┐"
    echo -e "              │             │             │"
    echo -e "        ${BOLD}┌─────┴────┐${NC}  ${BOLD}┌────┴────┐${NC}  ${BOLD}┌────┴─────┐${NC}"
    echo -e "        │ ${CYAN}Worker${NC}   │  │ ${CYAN}MinIO${NC}   │  │ ${CYAN}Watcher${NC}  │"
    echo -e "        │${DIM}sync loop${NC} │  │${DIM}blobs${NC}    │  │${DIM}K8s watch${NC} │"
    echo -e "        └──────────┘  └─────────┘  └──────────┘"
    echo -e "                            │"
    echo -e "                     ${BOLD}┌──────┴─────┐${NC}"
    echo -e "                     │ ${CYAN}REST API${NC}   │"
    echo -e "                     │ ${DIM}FastAPI${NC}     │"
    echo -e "                     └──────┬─────┘"
    echo -e "                            │"
    echo -e "         ┌──────────────────┼──────────────────┐"
    echo -e "   ${BOLD}┌─────┴──────┐${NC}    ${BOLD}┌──────┴──────┐${NC}    ${BOLD}┌──────┴──────┐${NC}"
    echo -e "   │ ${CYAN}CLI (ic)${NC}   │    │ ${CYAN}MCP Server${NC} │    │ ${CYAN}Dashboards${NC} │"
    echo -e "   │ ${DIM}PyPI pkg${NC}   │    │ ${DIM}50+ tools${NC}  │    │ ${DIM}webhooks${NC}   │"
    echo -e "   └────────────┘    └─────────────┘    └─────────────┘"
    echo ""
    narrate "Same data, three access patterns — zero duplication."
    narrate "Install: ${CYAN}pip install ic-tool${NC}"
    narrate "Local:   ${CYAN}ic config use-local${NC}"
    narrate "Cluster: ${CYAN}ic config use-cluster https://api.example.com --key <key>${NC}"
    advance 6

    echo ""
    echo -e "  ${BOLD}The triage loop:${NC}"
    echo ""
    echo -e "    ${RED}Alert${NC}  →  ${YELLOW}Analyze${NC}  →  ${GREEN}Fix${NC}"
    echo -e "    ${DIM}ic get alerts${NC}   ${DIM}ic ai analyze <component>${NC}   ${DIM}ic fix <component>${NC}"
    echo ""
    echo -e "    ${BOLD}1.${NC} See what's broken              ${DIM}(ic get alerts)${NC}"
    echo -e "    ${BOLD}2.${NC} Group by root cause             ${DIM}(ic get alerts --group)${NC}"
    echo -e "    ${BOLD}3.${NC} Drill into a failure            ${DIM}(ic describe failure <component>)${NC}"
    echo -e "    ${BOLD}4.${NC} AI reads logs, finds the cause  ${DIM}(ic ai analyze <component>)${NC}"
    echo -e "    ${BOLD}5.${NC} Create PR / Jira / Slack        ${DIM}(ic fix <component>)${NC}"
    echo -e "    ${BOLD}6.${NC} Check release readiness          ${DIM}(ic release readiness)${NC}"
    echo ""
    advance 7

    echo -e "  ${BOLD}Commands:${NC}"
    echo ""
    echo -e "  ${HEADER}Triage & Alerts${NC}"
    echo -e "  ${CYAN}ic get alerts${NC}                         Daily triage starting point"
    echo -e "  ${CYAN}ic get alerts --group${NC}                 Group by root cause"
    echo -e "  ${CYAN}ic describe failure <component>${NC}       Drill into a build failure"
    echo -e "  ${CYAN}ic describe conforma <component>${NC}      Conforma violation details"
    echo -e "  ${CYAN}ic triage show${NC}                        Persistent triage items"
    echo -e "  ${CYAN}ic triage track <comp> --group X${NC}      Track a new failure"
    echo -e "  ${CYAN}ic triage update <id> --jira KEY${NC}      Link Jira to triage item"
    echo -e "  ${CYAN}ic triage resolve <id>${NC}                Mark resolved"
    echo ""
    echo -e "  ${HEADER}AI & Skills${NC}"
    echo -e "  ${CYAN}ic ai analyze <component>${NC}             AI root cause analysis"
    echo -e "  ${CYAN}ic ai batch${NC}                           Analyze all pending failures"
    echo -e "  ${CYAN}ic ai status${NC}                          AI analysis overview"
    echo -e "  ${CYAN}ic fix <component>${NC}                    Interactive triage → action"
    echo -e "  ${CYAN}ic skills list${NC}                        Available remediation skills"
    echo -e "  ${CYAN}ic fix <comp> --skill X --dry-run${NC}     Preview skill execution"
    echo ""
    echo -e "  ${HEADER}Export & Release${NC}"
    echo -e "  ${CYAN}ic export <component> jira|slack${NC}      Generate formatted content"
    echo -e "  ${CYAN}ic get policy-gap${NC}                     Stage vs prod risk"
    echo -e "  ${CYAN}ic release status${NC}                     Pipeline checklist"
    echo -e "  ${CYAN}ic release readiness${NC}                  Go/no-go decision"
    echo -e "  ${CYAN}ic dashboard${NC}                          Operational metrics"
    echo ""
    echo -e "  ${HEADER}Config${NC}"
    echo -e "  ${CYAN}ic get apps${NC}                           Monitored applications"
    echo -e "  ${CYAN}ic config use-local${NC}                   Switch to local mode"
    echo -e "  ${CYAN}ic config use-cluster <url>${NC}           Switch to cluster mode"
    echo -e "  ${CYAN}ic config show${NC}                        Show current config"
    echo ""
    echo -e "  ${DIM}Demo data captured from acme-v2-1-ea-1 on 2026-05-25${NC}"
    echo ""
}

# ─── Track menu ──────────────────────────────────────────────────────

track_menu() {
    echo ""
    echo -e "  ${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  ${BOLD}What would you like to explore?${NC}"
    echo ""
    echo -e "  ${WHITE}1${NC}) ${BOLD}Build Failures${NC}     — triage, AI analysis, fix workflow, export"
    echo -e "  ${WHITE}2${NC}) ${BOLD}Conforma${NC}           — policy violations, stage vs prod gap"
    echo -e "  ${WHITE}3${NC}) ${BOLD}Releases${NC}           — pipeline status, readiness, schedule"
    echo -e "  ${WHITE}4${NC}) ${BOLD}AI & Operations${NC}    — analysis engine, dashboard, patterns"
    echo -e "  ${WHITE}5${NC}) ${BOLD}Triage & Skills${NC}    — persistent tracking, skill execution"
    echo -e "  ${WHITE}6${NC}) ${BOLD}Full showcase${NC}      — everything, in order"
    echo -e "  ${WHITE}q${NC}) Exit"
    echo ""
    echo -ne "  ${DIM}Choice [6]:${NC} "
}

run_track_builds() {
    build_group
    build_describe
    build_ai_analyze
    build_fix
    build_export
}

run_track_conforma() {
    conforma_describe
    conforma_policy_gap
}

run_track_releases() {
    release_status
    release_readiness
}

run_track_ai() {
    ai_status
    dashboard
}

run_track_triage_skills() {
    triage_tracking
    skills_section
}

run_full() {
    build_group
    build_describe
    build_ai_analyze
    build_fix
    conforma_describe
    conforma_policy_gap
    release_status
    release_readiness
    ai_status
    dashboard
    triage_tracking
    skills_section
    build_export
}

# ─── Main ────────────────────────────────────────────────────────────

main() {
    # Intro — always shown
    intro_what_is_ic
    intro_apps
    intro_alerts

    if [ "$AUTO" = true ]; then
        run_full
    else
        track_menu
        read -r choice
        choice="${choice:-6}"

        case "$choice" in
            1) run_track_builds ;;
            2) run_track_conforma ;;
            3) run_track_releases ;;
            4) run_track_ai ;;
            5) run_track_triage_skills ;;
            6) run_full ;;
            q|Q) echo -e "\n  ${DIM}Demo ended.${NC}\n"; exit 0 ;;
            *) run_full ;;
        esac
    fi

    wrapup
    echo -e "  ${DIM}Thanks for watching!${NC}"
    echo ""
}

main "$@"
