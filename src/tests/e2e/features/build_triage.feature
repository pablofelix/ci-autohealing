Feature: Build failure triage
  As a CI engineer
  I want to triage build failures systematically
  So that I can prioritize and resolve them efficiently

  Background:
    Given the API is running
    And the application "test-app" exists

  Scenario: View all build failures
    Given component "failing-component" has a build failure in "test-app"
    When I request the failures list for "test-app"
    Then I receive a 200 response
    And the response contains a list of failures
    And each failure has component, status, and first_seen fields

  Scenario: Get details for a specific failure
    Given component "my-component" has a build failure in "test-app"
    When I request failure details for "my-component" in "test-app"
    Then I receive a 200 response
    And the response contains error_message and error_type
    And the response contains build_logs

  Scenario: Track a failure for triage
    Given component "my-component" has a build failure in "test-app"
    When I submit a triage tracking request for component "my-component" in "test-app"
    Then I receive a 200 response
    And the triage item has status "tracked"

  Scenario: Resolve a triaged failure
    Given component "my-component" is tracked for triage in "test-app"
    When I resolve the triage item with verdict "fixed"
    Then I receive a 200 response
    And the item status is "resolved"

  Scenario: View triage summary
    Given "test-app" has tracked and resolved triage items
    When I request the triage summary for "test-app"
    Then I receive a 200 response
    And the summary includes total_items, urgent_items, and new_items
