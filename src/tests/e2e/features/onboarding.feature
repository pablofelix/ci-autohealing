Feature: Component onboarding
  As a developer onboarding a new component
  I want to check my component's Konflux setup progress
  So that I know what steps remain

  Background:
    Given the API is running
    And the application "test-app" exists

  Scenario: Check onboarding status endpoint exists
    Given application "test-app" has components in various onboarding stages
    When I request onboarding status for "test-app"
    Then the endpoint is available

  Scenario: Check specific component onboarding endpoint exists
    Given component "new-service" is partially onboarded in "test-app"
    When I request onboarding details for "new-service" in "test-app"
    Then the endpoint is available
