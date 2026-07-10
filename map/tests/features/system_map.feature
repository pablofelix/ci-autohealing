Feature: System Map UI
  As a DevOps engineer
  I want an interactive visual map of the RHOAI CI/CD infrastructure
  So that I can navigate, explore, and understand the system at a glance

  Background:
    Given the system map backend is running
    And the graph is seeded with infrastructure data

  # ── Graph loading ──────────────────────────────────────────────────────────

  Scenario: Graph loads with nodes and edges
    When I request the full graph
    Then I receive nodes and edges in React Flow format
    And each node has an id, type, and data object
    And each edge has source, target, and label

  Scenario: Graph contains all expected node types
    When I request the full graph
    Then the graph contains nodes of type "Repository"
    And the graph contains nodes of type "Workflow"
    And the graph contains nodes of type "Pipeline"
    And the graph contains nodes of type "TektonTask"
    And the graph contains nodes of type "ECPolicy"
    And the graph contains nodes of type "Automation"

  # ── Node detail panel ──────────────────────────────────────────────────────

  Scenario: Clicking a repository node shows detail with external link
    Given a repository node "aiops-infra" exists in the graph
    When I request the node detail for "aiops-infra"
    Then the detail panel shows the node type "Repository"
    And the detail includes a "url" property
    And the url starts with "https://"
    And the detail does not contain internal properties

  Scenario: Clicking a workflow node shows detail with GitHub Actions link
    Given a workflow node "gha-trigger-nightlies" exists in the graph
    When I request the node detail for "gha-trigger-nightlies"
    Then the detail panel shows the node type "Workflow"
    And the detail includes a "url" property
    And the url contains "actions/workflows"

  Scenario: Clicking an EC policy node shows detail with GitLab link
    Given an EC policy node "ec-registry-rhoai-prod" exists in the graph
    When I request the node detail for "ec-registry-rhoai-prod"
    Then the detail panel shows the node type "ECPolicy"
    And the detail includes a "url" property
    And the url contains "EnterpriseContractPolicy"

  Scenario: Clicking a pipeline node shows detail with source link
    Given a pipeline node "pipeline-container-build" exists in the graph
    When I request the node detail for "pipeline-container-build"
    Then the detail panel shows the node type "Pipeline"
    And the detail includes a "url" property

  Scenario: Clicking a Tekton task node shows detail with build-definitions link
    Given a Tekton task node "task-buildah-remote-oci-ta" exists in the graph
    When I request the node detail for "task-buildah-remote-oci-ta"
    Then the detail panel shows the node type "TektonTask"
    And the detail includes a "url" property
    And the url contains "build-definitions"

  Scenario: Clicking an automation node shows detail with link
    Given an automation node "automation-mintmaker" exists in the graph
    When I request the node detail for "automation-mintmaker"
    Then the detail panel shows the node type "Automation"
    And the detail includes a "url" property

  Scenario: Node detail shows connections to other nodes
    Given a workflow node "gha-trigger-nightlies" exists in the graph
    When I request the node detail for "gha-trigger-nightlies"
    Then the detail includes a list of neighbor connections
    And each neighbor has id, type, relationship, and direction

  Scenario: Node detail does not leak internal metadata
    When I request the node detail for "aiops-infra"
    Then the detail does not contain internal properties

  # ── Search ─────────────────────────────────────────────────────────────────

  Scenario: Searching for a node by name
    When I search for "operator"
    Then I receive search results
    And the results include a node matching "operator"

  Scenario: Searching for a non-existent node returns empty
    When I search for "xyznonexistent999"
    Then the search returns zero results

  Scenario: Searching filtered by type
    When I search for "rhoai" with type filter "ECPolicy"
    Then all results have type "ECPolicy"

  # ── Type filtering ────────────────────────────────────────────────────────

  Scenario: Filtering graph by node type
    When I request the full graph
    And I filter for type "Repository"
    Then only repository nodes and their direct connections are visible

  # ── Gap detection ──────────────────────────────────────────────────────────

  Scenario: Gap detection reports infrastructure issues
    When I request the infrastructure gaps
    Then I receive a structured gap report
    And each gap has node_id, type, severity, and message

  # ── Statistics ─────────────────────────────────────────────────────────────

  Scenario: Statistics show node and edge counts by type
    When I request the graph statistics
    Then I receive node counts grouped by type
    And I receive edge counts grouped by type

  # ── Path finding ───────────────────────────────────────────────────────────

  Scenario: Finding path between two connected nodes
    When I find the path from "rhoai-build-config" to "gha-trigger-nightly-bundle"
    Then I receive a path with nodes and edges
    And the path starts at "rhoai-build-config"
    And the path ends at "gha-trigger-nightly-bundle"

  Scenario: Finding path between unconnected nodes returns 404
    When I find the path from "aiops-infra" to "ec-fbc-rhoai-prod"
    Then I receive a 404 response

  # ── Health check ───────────────────────────────────────────────────────────

  Scenario: Health endpoint reports status
    When I request the health status
    Then the status is "ok"
    And neo4j is "connected"
    And the total node count is greater than zero
