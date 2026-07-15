Feature: Onboarding Overlay
  As a DevOps engineer
  I want to see onboarding progress for each component on the system map
  So that I can identify which components need onboarding attention

  Background:
    Given the live status endpoint is available

  # ── Data flow ─────────────────────────────────────────────────────────────

  Scenario: Live status response includes onboarding data
    Given IC reports onboarding data for "rhoai-v3-5"
    When I request the live status for "rhoai-v3-5"
    Then the response contains an "onboarding" array
    And the onboarding array has 3 entries

  Scenario: IC unavailable returns empty onboarding list
    Given IC is unavailable
    When I request the live status for "rhoai-v3-5"
    Then the response contains an "onboarding" array
    And the onboarding array has 0 entries
    And ic_available is false

  # ── Badge colors by status ────────────────────────────────────────────────

  Scenario: Complete component gets green badge
    Given IC reports onboarding data for "rhoai-v3-5"
    When I request the live status for "rhoai-v3-5"
    Then component "odh-dashboard-v3-5" has onboarding score 100
    And component "odh-dashboard-v3-5" has onboarding overall "complete"
    And component "odh-dashboard-v3-5" has badge color "#10b981"

  Scenario: Partial component gets yellow badge
    Given IC reports onboarding data for "rhoai-v3-5"
    When I request the live status for "rhoai-v3-5"
    Then component "odh-vllm-cpu-v3-5" has onboarding score 75
    And component "odh-vllm-cpu-v3-5" has onboarding overall "partial"
    And component "odh-vllm-cpu-v3-5" has badge color "#f59e0b"

  Scenario: Incomplete component gets red badge
    Given IC reports onboarding data for "rhoai-v3-5"
    When I request the live status for "rhoai-v3-5"
    Then component "odh-notebook-v3-5" has onboarding score 35
    And component "odh-notebook-v3-5" has onboarding overall "incomplete"
    And component "odh-notebook-v3-5" has badge color "#ef4444"

  # ── Onboarding checks ────────────────────────────────────────────────────

  Scenario: Onboarding checks are preserved with status and detail
    Given IC reports onboarding data for "rhoai-v3-5"
    When I request the live status for "rhoai-v3-5"
    Then component "odh-notebook-v3-5" check "branch" has status "FAIL"
    And component "odh-notebook-v3-5" check "branch" has a fix suggestion
    And component "odh-notebook-v3-5" check "repository" has status "PASS"

  Scenario: Failing checks are listed in the failing array
    Given IC reports onboarding data for "rhoai-v3-5"
    When I request the live status for "rhoai-v3-5"
    Then component "odh-notebook-v3-5" has failing checks "branch,container_image,builds"
    And component "odh-dashboard-v3-5" has no failing checks

  # ── Jira integration ──────────────────────────────────────────────────────

  Scenario: Onboarding entry includes Jira key when available
    Given IC reports onboarding data for "rhoai-v3-5"
    When I request the live status for "rhoai-v3-5"
    Then component "odh-vllm-cpu-v3-5" has jira key "RHOAI-12345"
    And component "odh-dashboard-v3-5" has no jira key

  # ── Node ID mapping ──────────────────────────────────────────────────────

  Scenario: Onboarding node IDs use comp- prefix for frontend matching
    Given IC reports onboarding data for "rhoai-v3-5"
    When I request the live status for "rhoai-v3-5"
    Then all onboarding node_ids start with "comp-"

  # ── Edge cases ────────────────────────────────────────────────────────────

  Scenario: Empty components list returns empty onboarding
    Given IC reports empty onboarding for "rhoai-v3-5"
    When I request the live status for "rhoai-v3-5"
    Then the onboarding array has 0 entries

  Scenario: Components without names are skipped
    Given IC reports onboarding with a nameless component
    When I request the live status for "rhoai-v3-5"
    Then the onboarding array has 1 entries
