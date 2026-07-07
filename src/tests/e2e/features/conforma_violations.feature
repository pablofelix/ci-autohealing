Feature: Conforma violation tracking
  As a release engineer
  I want to track conforma (Enterprise Contract) violations
  So that I can ensure compliance before release

  Background:
    Given the API is running
    And the application "test-app" exists

  Scenario: List violations for an application
    Given component "my-component" has conforma violations in "test-app"
    When I request violations for "test-app"
    Then I receive a 200 response
    And the response contains a list of violations
    And each violation has component_name, scenario, and violations_count fields

  Scenario: Get violation details for a component
    Given component "my-component" has conforma violations in "test-app"
    When I request violation details for "my-component" in "test-app"
    Then I receive a 200 response
    And the response contains violation_rules and violation_summary

  Scenario: View exception lifecycle
    Given there are policy exceptions in the system
    When I request the exception lifecycle
    Then I receive a 200 response
    And the response includes permanent, active_temporary, and expired counts
    And each exception has rule, policy, and status fields

  Scenario: View violation rules
    Given "test-app" has violation data
    When I request violation rules for "test-app"
    Then I receive a 200 response
    And the response contains categorized rules
