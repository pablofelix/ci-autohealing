Feature: Release readiness assessment
  As a release manager
  I want to check if an application is ready for release
  So that I can gate releases on automated checks

  Background:
    Given the API is running
    And the application "test-app" exists

  Scenario: Application is ready for release
    Given "test-app" has no build failures
    And "test-app" has no conforma violations
    And there is no active freeze
    When I check release readiness for "test-app"
    Then I receive a 200 response
    And the verdict is "READY"
    And there are no blockers

  Scenario: Application blocked by conforma violations
    Given "test-app" has 3 conforma violations
    When I check release readiness for "test-app"
    Then I receive a 200 response
    And the verdict is "NOT_READY"
    And the blockers mention conforma violations

  Scenario: Application at risk due to build failures
    Given "test-app" has 2 build failures
    And "test-app" has no conforma violations
    When I check release readiness for "test-app"
    Then I receive a 200 response
    And the verdict is "AT_RISK"
    And the risks mention failing builds

  Scenario: Freeze calendar blocks release
    Given there is an active freeze with reason "RC1 stabilization"
    When I check release readiness for "test-app"
    Then the verdict is "NOT_READY"
    And the blockers mention "frozen"

  Scenario: View release schedule
    Given "test-app" has a release schedule
    When I request the schedule for "test-app"
    Then I receive a 200 response
    And the schedule includes code_freeze and release_date fields
