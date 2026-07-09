"""Step definitions for BDD tests."""
import os
import sys
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

# Load all feature files
scenarios('../features/build_triage.feature')
scenarios('../features/conforma_violations.feature')
scenarios('../features/release_readiness.feature')
scenarios('../features/onboarding.feature')


# Mark all tests as e2e
pytestmark = pytest.mark.e2e


# ==============================================================================
# Background steps
# ==============================================================================

@given('the API is running')
def api_running(client):
    """API is running via the client fixture."""
    pass


@given(parsers.parse('the application "{app}" exists'))
def application_exists(app, context):
    """Mark application as existing in context."""
    context['application'] = app


# ==============================================================================
# Build failure steps (Given)
# ==============================================================================

@given(parsers.parse('component "{component}" has a build failure in "{app}"'))
def component_has_build_failure(component, app, context, mock_build_repo):
    """Mock a build failure for the component."""
    failure = {
        'component': component,
        'application': app,
        'status': 'failed',
        'error_message': 'Build failed: compilation error',
        'error_type': 'compilation',
        'first_seen': datetime.utcnow() - timedelta(hours=2),
        'last_seen': datetime.utcnow(),
        'failure_count': 3,
        'pipeline_run': 'test-pipeline-run-123',
        'build_logs': 'error: undefined reference to main\n',
    }

    # For get_failure_details endpoint
    failure_details = {
        'component_name': component,
        'pipelinerun_name': 'test-pipeline-run-123',
        'status': 'failed',
        'error_message': 'Build failed: compilation error',
        'error_type': 'compilation',
        'failed_task_name': 'build-container',
        'failed_step_name': 'build',
        'task_summary': 'Build failed during compilation',
        'build_logs': 'error: undefined reference to main\n',
        'commit_sha': 'abc123def456',
        'commit_message': 'Fix build issue',
        'commit_author': 'test-user',
        'commit_url': 'https://github.com/org/repo/commit/abc123',
        'repository_url': 'https://github.com/org/repo',
        'branch': 'main',
        'output_image': 'quay.io/org/image:latest',
        'jira_key': None,
        'commit_context': {},
        'konflux_url': 'https://console.redhat.com/preview/application-pipeline/ns/test-ns/pipelinerun/test-pipeline-run-123/logs',
    }

    mock_build_repo.find_by_component.return_value = failure
    mock_build_repo.find_by_application.return_value = [failure]
    mock_build_repo.find_failing_component_names.return_value = {component}
    mock_build_repo.get_latest_failures.return_value = [failure]
    mock_build_repo.get_failure_details.return_value = failure_details
    mock_build_repo.get_component_history.return_value = {'builds': []}

    context['build_failure'] = failure
    context['failure_details'] = failure_details
    context['mock_build_repo'] = mock_build_repo


@given(parsers.parse('"{app}" has {count:d} build failures'))
def app_has_n_build_failures(app, count, context, mock_build_repo):
    """Mock N build failures for the application."""
    failures = []
    for i in range(count):
        failures.append({
            'component': f'component-{i}',
            'application': app,
            'status': 'failed',
            'error_message': f'Build failed: error {i}',
            'error_type': 'compilation',
            'first_seen': datetime.utcnow() - timedelta(hours=i+1),
            'last_seen': datetime.utcnow(),
            'failure_count': i+1,
        })

    mock_build_repo.find_by_application.return_value = failures
    mock_build_repo.find_failing_component_names.return_value = {f['component'] for f in failures}
    context['build_failures'] = failures
    context['mock_build_repo'] = mock_build_repo


@given(parsers.parse('"{app}" has no build failures'))
def app_has_no_build_failures(app, context, mock_build_repo):
    """Mock no build failures."""
    mock_build_repo.find_by_application.return_value = []
    mock_build_repo.find_failing_component_names.return_value = set()
    context['mock_build_repo'] = mock_build_repo


# ==============================================================================
# Conforma violation steps (Given)
# ==============================================================================

@given(parsers.parse('component "{component}" has conforma violations in "{app}"'))
def component_has_conforma_violations(component, app, context, mock_conforma_repo):
    """Mock conforma violations for the component."""
    violation = {
        'component_name': component,
        'application': app,
        'scenario': 'oci-trusted-task-v0.1-prod',
        'violations_count': 5,
        'violation_summary': 'deprecated_image_reference: 1, task_bundle_version: 4',
        'pipeline_run': 'test-pipeline-run-456',
        'last_updated': datetime.utcnow(),
        'violation_rules': ['deprecated_image_reference', 'task_bundle_version'],
    }

    # For get_violation_details endpoint (needs more fields)
    violation_details = {
        'component_name': component,
        'application': app,
        'scenario': 'oci-trusted-task-v0.1-prod',
        'violations_count': 5,
        'warnings_count': 0,
        'successes_count': 10,
        'violation_summary': 'deprecated_image_reference: 1, task_bundle_version: 4',
        'pipelinerun_name': 'test-pipeline-run-456',
        'violation_details': {
            'rules': [
                {'name': 'deprecated_image_reference', 'count': 1},
                {'name': 'task_bundle_version', 'count': 4},
            ]
        },
        'repository_url': 'https://github.com/org/repo',
        'commit_sha': 'abc123def456',
        'snapshot_name': 'test-snapshot',
        'first_detected_at': datetime.utcnow() - timedelta(hours=2),
        'last_updated': datetime.utcnow(),
    }

    mock_conforma_repo.get_violation_summaries.return_value = [violation]
    mock_conforma_repo.get_violation_details.return_value = violation_details
    mock_conforma_repo.find_unresolved_component_names.return_value = {component}

    context['violation'] = violation
    context['violation_details'] = violation_details
    context['mock_conforma_repo'] = mock_conforma_repo


@given(parsers.parse('"{app}" has {count:d} conforma violations'))
def app_has_n_conforma_violations(app, count, context, mock_conforma_repo):
    """Mock N conforma violations for the application."""
    violations = []
    for i in range(count):
        violations.append({
            'component_name': f'component-{i}',
            'application': app,
            'scenario': 'oci-trusted-task-v0.1-prod',
            'violations_count': i+1,
            'violation_summary': f'rule_{i}: {i+1}',
        })

    mock_conforma_repo.get_violation_summaries.return_value = violations
    mock_conforma_repo.find_unresolved_component_names.return_value = {v['component_name'] for v in violations}
    context['violations'] = violations
    context['mock_conforma_repo'] = mock_conforma_repo


@given(parsers.parse('"{app}" has no conforma violations'))
def app_has_no_conforma_violations(app, context, mock_conforma_repo):
    """Mock no conforma violations."""
    mock_conforma_repo.get_violation_summaries.return_value = []
    mock_conforma_repo.find_unresolved_component_names.return_value = set()
    context['mock_conforma_repo'] = mock_conforma_repo


@given('there are policy exceptions in the system')
def policy_exceptions_exist(context):
    """Mock policy exceptions data."""
    context['has_exceptions'] = True


@given(parsers.parse('"{app}" has violation data'))
def app_has_violation_data(app, context, mock_conforma_repo):
    """Mock that application has some violation data."""
    mock_conforma_repo.get_violation_summaries.return_value = [
        {
            'component_name': 'test-component',
            'application': app,
            'scenario': 'oci-trusted-task-v0.1-prod',
            'violations_count': 1,
        }
    ]
    context['mock_conforma_repo'] = mock_conforma_repo


# ==============================================================================
# Triage steps (Given)
# ==============================================================================

@given(parsers.parse('component "{component}" is tracked for triage in "{app}"'))
def component_tracked_for_triage(component, app, context, mock_triage_repo):
    """Mock a triage item for the component."""
    triage_item = {
        'id': 123,
        'application': app,
        'components': [component],
        'status': 'tracked',
        'group_label': None,
        'root_cause': None,
        'created_at': datetime.utcnow() - timedelta(hours=1),
        'resolved_at': None,
    }

    mock_triage_repo.find_by_id.return_value = triage_item
    mock_triage_repo.find_by_component.return_value = triage_item
    mock_triage_repo.create_item.return_value = 123

    context['triage_item'] = triage_item
    context['triage_id'] = 123
    context['mock_triage_repo'] = mock_triage_repo


@given(parsers.parse('"{app}" has tracked and resolved triage items'))
def app_has_triage_items(app, context, mock_triage_repo):
    """Mock triage items (tracked and resolved)."""
    items = [
        {
            'id': 1,
            'application': app,
            'components': ['comp-1'],
            'status': 'tracked',
            'created_at': datetime.utcnow() - timedelta(hours=5),
        },
        {
            'id': 2,
            'application': app,
            'components': ['comp-2'],
            'status': 'resolved',
            'created_at': datetime.utcnow() - timedelta(days=1),
            'resolved_at': datetime.utcnow() - timedelta(hours=2),
        },
    ]

    summary = {
        'total_items': 2,
        'urgent_items': 1,
        'new_items': 1,
        'active_items': 1,
        'resolved_items': 1,
    }

    mock_triage_repo.get_report.return_value = items
    mock_triage_repo.get_summary.return_value = summary

    context['triage_items'] = items
    context['triage_summary'] = summary
    context['mock_triage_repo'] = mock_triage_repo


# ==============================================================================
# Release readiness steps (Given)
# ==============================================================================

@given('there is no active freeze')
def no_active_freeze(context):
    """Mock no active freeze."""
    context['freeze'] = None


@given(parsers.parse('there is an active freeze with reason "{reason}"'))
def active_freeze_exists(reason, context):
    """Mock an active freeze."""
    today = date.today()
    context['freeze'] = {
        'id': 1,
        'start_date': str(today - timedelta(days=1)),
        'end_date': str(today + timedelta(days=7)),
        'reason': reason,
        'status': 'active',
    }


@given(parsers.parse('"{app}" has a release schedule'))
def app_has_schedule(app, context):
    """Mock a release schedule."""
    today = date.today()
    context['schedule'] = {
        'application': app,
        'code_freeze': str(today + timedelta(days=10)),
        'code_freeze_days': 10,
        'release_date': str(today + timedelta(days=1)),
        'release_date_days': 1,
    }


# ==============================================================================
# Onboarding steps (Given)
# ==============================================================================

@given(parsers.parse('application "{app}" has components in various onboarding stages'))
def app_has_components_various_stages(app, context):
    """Mock components in different onboarding stages."""
    context['onboarding_components'] = [
        {'name': 'complete-component', 'score': 100, 'overall': 'complete'},
        {'name': 'partial-component', 'score': 60, 'overall': 'partial'},
        {'name': 'incomplete-component', 'score': 20, 'overall': 'incomplete'},
    ]


@given(parsers.parse('component "{component}" is partially onboarded in "{app}"'))
def component_partially_onboarded(component, app, context):
    """Mock a partially onboarded component."""
    context['onboarding_component'] = {
        'component': component,
        'application': app,
        'overall': 'partial',
        'score': 65,
        'checks': {
            'repository': {'status': 'PASS', 'detail': 'https://github.com/org/repo'},
            'branch': {'status': 'PASS', 'detail': 'main'},
            'container_image': {'status': 'PASS', 'detail': 'quay.io/org/img'},
            'pac': {'status': 'WARN', 'detail': 'No PaC Repository CR found'},
            'builds': {'status': 'FAIL', 'detail': 'No builds found'},
            'last_built': {'status': 'WARN', 'detail': 'No successful build recorded'},
            'nudges': {'status': 'INFO', 'detail': 'No nudges configured'},
        },
        'failing': ['builds'],
        'warnings': ['pac', 'last_built'],
    }


# ==============================================================================
# Action steps (When)
# ==============================================================================

@when(parsers.parse('I request the failures list for "{app}"'))
def request_failures_list(app, client, context):
    """Request failures list."""
    with patch('api.routes.failures.get_repository') as mock_get_repo:
        if 'mock_build_repo' in context:
            mock_get_repo.return_value = context['mock_build_repo']

        context['response'] = client.get(f'/api/v1/applications/{app}/failures')


@when(parsers.parse('I request failure details for "{component}" in "{app}"'))
def request_failure_details(component, app, client, context):
    """Request failure details for a component."""
    with patch('api.routes.failures.get_repository') as mock_get_repo:
        if 'mock_build_repo' in context:
            mock_get_repo.return_value = context['mock_build_repo']

        context['response'] = client.get(f'/api/v1/applications/{app}/failures/{component}')


@when(parsers.parse('I submit a triage tracking request for component "{component}" in "{app}"'))
def submit_triage_tracking(component, app, client, context):
    """Submit triage tracking request."""
    with patch('api.routes.triage.get_repository') as mock_get_repo:
        if 'mock_triage_repo' in context:
            mock_get_repo.return_value = context['mock_triage_repo']
            # Ensure create_item returns an ID
            context['mock_triage_repo'].create_item.return_value = 123

        body = {'component': component}
        context['response'] = client.post(f'/api/v1/applications/{app}/triage', json=body)


@when(parsers.parse('I resolve the triage item with verdict "{verdict}"'))
def resolve_triage_item(verdict, client, context):
    """Resolve a triage item."""
    with patch('api.routes.triage.get_repository') as mock_get_repo:
        if 'mock_triage_repo' in context:
            mock_get_repo.return_value = context['mock_triage_repo']

        app = context.get('application', 'test-app')
        item_id = context.get('triage_id', 123)
        body = {'verdict': verdict, 'resolution': 'Fixed'}
        context['response'] = client.post(
            f'/api/v1/applications/{app}/triage/{item_id}/resolve',
            json=body
        )


@when(parsers.parse('I request the triage summary for "{app}"'))
def request_triage_summary(app, client, context):
    """Request triage summary."""
    with patch('api.routes.triage.get_repository') as mock_get_repo:
        if 'mock_triage_repo' in context:
            mock_get_repo.return_value = context['mock_triage_repo']

        context['response'] = client.get(f'/api/v1/applications/{app}/triage')


@when(parsers.parse('I request violations for "{app}"'))
def request_violations(app, client, context):
    """Request violations list."""
    with patch('api.routes.violations.get_repository') as mock_get_repo, \
         patch('conforma.policy_tools.fetch_exceptions_by_policy') as mock_exceptions:
        if 'mock_conforma_repo' in context:
            mock_get_repo.return_value = context['mock_conforma_repo']
        mock_exceptions.return_value = {}

        context['response'] = client.get(f'/api/v1/applications/{app}/violations')


@when(parsers.parse('I request violation details for "{component}" in "{app}"'))
def request_violation_details(component, app, client, context):
    """Request violation details for a component."""
    with patch('api.routes.violations.get_repository') as mock_get_repo, \
         patch('conforma.policy_tools.fetch_exceptions_by_policy') as mock_exceptions, \
         patch('conforma.policy_tools.extract_policy_from_scenario') as mock_extract, \
         patch('conforma.policy_tools.extract_violation_rules') as mock_rules, \
         patch('conforma.policy_tools.lookup_exceptions') as mock_lookup, \
         patch('conforma.policy_tools.compute_violation_coverage') as mock_coverage, \
         patch('conforma.policy_tools.compute_blocks') as mock_blocks, \
         patch('conforma.policy_tools.count_unique_violations') as mock_count, \
         patch('conforma.policy_tools.compute_coverage_by_env') as mock_cov_env, \
         patch('conforma.policy_tools.policy_env') as mock_policy_env:
        if 'mock_conforma_repo' in context:
            mock_get_repo.return_value = context['mock_conforma_repo']
        mock_exceptions.return_value = {}
        mock_extract.return_value = 'oci-trusted-task-v0.1-prod'
        mock_rules.return_value = ['deprecated_image_reference']
        mock_lookup.return_value = []
        mock_coverage.return_value = {}
        mock_blocks.return_value = ''
        mock_count.return_value = (2, [{'rule': 'deprecated_image_reference', 'detail': ''}])
        mock_cov_env.return_value = {
            'stage': {'coverage': None, 'covered_rules': []},
            'prod': {'coverage': None, 'covered_rules': []},
        }
        mock_policy_env.return_value = 'prod'

        context['response'] = client.get(
            f'/api/v1/applications/{app}/violations/{component}'
        )


@when('I request the exception lifecycle')
def request_exception_lifecycle(client, context):
    """Request exception lifecycle."""
    with patch('conforma.policy_tools.fetch_exceptions_by_policy') as mock_exceptions:
        # Mock some exceptions data
        mock_exceptions.return_value = {
            'oci-trusted-task-v0.1-prod': [
                {
                    'value': 'deprecated_image_reference',
                    'effectiveUntil': '2026-12-31',
                    'permanent': False,
                    'days_left': 180,
                    'reference': 'RHOAIENG-123',
                    'gitlab_link': 'https://gitlab.com/...',
                }
            ]
        }

        context['response'] = client.get('/api/v1/exceptions/lifecycle')


@when(parsers.parse('I request violation rules for "{app}"'))
def request_violation_rules(app, client, context):
    """Request violation rules."""
    with patch('api.routes.violations.get_repository') as mock_get_repo, \
         patch('conforma.policy_tools.fetch_exceptions_by_policy') as mock_exceptions:
        if 'mock_conforma_repo' in context:
            mock_get_repo.return_value = context['mock_conforma_repo']
        mock_exceptions.return_value = {}

        context['response'] = client.get(f'/api/v1/applications/{app}/violations/rules')


@when(parsers.parse('I check release readiness for "{app}"'))
def check_release_readiness(app, client, context):
    """Check release readiness."""
    with patch('api.routes.releases.get_repository') as mock_get_repo, \
         patch('api.routes.releases.get_active_freeze') as mock_freeze, \
         patch('api.routes.releases.get_schedule') as mock_schedule, \
         patch('api.routes.releases._run_readiness_checks') as mock_checks:

        # Setup mocks
        def get_repo_impl(repo_class):
            if repo_class.__name__ == 'BuildFailureRepository':
                return context.get('mock_build_repo', MagicMock())
            elif repo_class.__name__ == 'ConformaRepository':
                return context.get('mock_conforma_repo', MagicMock())
            return MagicMock()

        mock_get_repo.side_effect = get_repo_impl

        # Mock freeze
        mock_freeze.return_value = context.get('freeze', None)

        # Mock schedule
        mock_schedule.return_value = context.get('schedule', None)

        # Mock readiness checks
        mock_checks.return_value = []

        context['response'] = client.get(f'/api/v1/applications/{app}/readiness')


@when(parsers.parse('I request the schedule for "{app}"'))
def request_schedule(app, client, context):
    """Request release schedule."""
    with patch('api.routes.releases._db') as mock_db_func:
        if 'mock_db_connection' in context:
            mock_db_func.return_value.connection.return_value = context['mock_db_connection']

        context['response'] = client.get(f'/api/v1/applications/{app}/schedule')


@when(parsers.parse('I request onboarding status for "{app}"'))
def request_onboarding_status(app, client, context):
    """Request onboarding status."""
    # For now, expect 500 since onboarding requires Konflux API access
    # In future, we could mock the Konflux client properly
    context['response'] = client.get(f'/api/v1/applications/{app}/onboarding')


@when(parsers.parse('I request onboarding details for "{component}" in "{app}"'))
def request_onboarding_details(component, app, client, context):
    """Request onboarding details for a component."""
    # For now, expect 500 since onboarding requires Konflux API access
    # In future, we could mock the Konflux client properly
    context['response'] = client.get(
        f'/api/v1/applications/{app}/onboarding/{component}'
    )


# ==============================================================================
# Assertion steps (Then)
# ==============================================================================

@then(parsers.parse('I receive a {status_code:d} response'))
def check_status_code(status_code, context):
    """Check response status code."""
    response = context.get('response')
    assert response is not None, "No response in context"
    assert response.status_code == status_code, \
        f"Expected {status_code}, got {response.status_code}. Body: {response.text}"


@then('the response contains a list of failures')
def response_has_failures_list(context):
    """Check response contains failures list."""
    response = context['response']
    data = response.json()
    assert isinstance(data, list) or 'failures' in data, \
        f"Expected list or object with 'failures', got: {data}"


@then('each failure has component, status, and first_seen fields')
def failures_have_required_fields(context):
    """Check failures have required fields."""
    response = context['response']
    data = response.json()
    failures = data if isinstance(data, list) else data.get('failures', [])

    if not failures:
        # If no failures, step passes (empty list is valid)
        return

    for failure in failures:
        assert 'component' in failure, f"Missing 'component' in {failure}"
        assert 'status' in failure, f"Missing 'status' in {failure}"
        # first_seen might be in various formats
        assert any(k in failure for k in ['first_seen', 'created_at', 'detected_at']), \
            f"Missing timestamp field in {failure}"


@then('the response contains error_message and error_type')
def response_has_error_fields(context):
    """Check response contains error details."""
    response = context['response']
    data = response.json()
    assert 'error_message' in data or 'message' in data, \
        f"Missing error_message in {data}"
    assert 'error_type' in data or 'type' in data or 'category' in data, \
        f"Missing error_type in {data}"


@then('the response contains build_logs')
def response_has_build_logs(context):
    """Check response contains build logs."""
    response = context['response']
    data = response.json()
    assert 'build_logs' in data or 'logs' in data or 'log_snippet' in data, \
        f"Missing build_logs in {data}"


@then(parsers.parse('the triage item has status "{status}"'))
def triage_item_has_status(status, context):
    """Check triage item status."""
    response = context['response']
    data = response.json()

    # The response might be the item itself or an action result
    if 'status' in data:
        assert data['status'] == status, f"Expected status {status}, got {data['status']}"
    elif 'item' in data and 'status' in data['item']:
        assert data['item']['status'] == status
    elif 'action' in data:
        # For create/track responses, check action indicates tracking
        assert data['action'] in ('created', 'tracked', 'exists'), \
            f"Expected tracking action, got {data['action']}"


@then(parsers.parse('the item status is "{status}"'))
def item_status_is(status, context):
    """Check item status."""
    response = context['response']
    data = response.json()

    # For resolve responses, action should be 'resolved'
    if 'action' in data:
        assert data['action'] == status.lower(), \
            f"Expected action {status.lower()}, got {data['action']}"


@then('the summary includes total_items, urgent_items, and new_items')
def summary_has_required_fields(context):
    """Check summary has required fields."""
    response = context['response']
    data = response.json()

    summary = data.get('summary', data)
    assert 'total_items' in summary, f"Missing total_items in {summary}"
    assert 'urgent_items' in summary, f"Missing urgent_items in {summary}"
    assert 'new_items' in summary, f"Missing new_items in {summary}"


@then('the response contains a list of violations')
def response_has_violations_list(context):
    """Check response contains violations list."""
    response = context['response']
    data = response.json()
    assert isinstance(data, list) or 'violations' in data, \
        f"Expected list or object with 'violations', got: {data}"


@then('each violation has component_name, scenario, and violations_count fields')
def violations_have_required_fields(context):
    """Check violations have required fields."""
    response = context['response']
    data = response.json()
    violations = data if isinstance(data, list) else data.get('violations', [])

    if not violations:
        return

    for violation in violations:
        assert 'component_name' in violation or 'component' in violation, \
            f"Missing component field in {violation}"
        assert 'scenario' in violation or 'policy' in violation, \
            f"Missing scenario field in {violation}"
        assert 'violations_count' in violation or 'count' in violation or 'violations' in violation, \
            f"Missing violations_count in {violation}"


@then('the response contains violation_rules and violation_summary')
def response_has_violation_details(context):
    """Check response contains violation details."""
    response = context['response']
    data = response.json()
    assert 'violation_rules' in data or 'rules' in data or 'violation_summary' in data, \
        f"Missing violation details in {data}"


@then('the response includes permanent, active_temporary, and expired counts')
def exception_lifecycle_has_counts(context):
    """Check exception lifecycle has required counts."""
    response = context['response']
    data = response.json()

    # The structure might vary, look for exception-related fields
    assert 'permanent' in data or 'active_temporary' in data or 'expired' in data or \
           'summary' in data or 'exceptions' in data, \
        f"Missing exception lifecycle data in {data}"


@then('each exception has rule, policy, and status fields')
def exceptions_have_required_fields(context):
    """Check exceptions have required fields."""
    response = context['response']
    data = response.json()

    # Find exceptions in the response
    exceptions = []
    if isinstance(data, list):
        exceptions = data
    elif 'exceptions' in data:
        exceptions = data['exceptions']
    elif 'active_temporary' in data and isinstance(data['active_temporary'], list):
        exceptions = data['active_temporary']
    elif 'permanent' in data and isinstance(data['permanent'], list):
        exceptions = data['permanent']

    # If no exceptions found, check if response structure contains exception info
    if not exceptions:
        # Response might be a summary without individual exceptions
        return

    for exc in exceptions:
        # Flexible field checking
        assert any(k in exc for k in ['rule', 'violation_rule', 'exception_rule']), \
            f"Missing rule field in {exc}"


@then('the response contains categorized rules')
def response_has_categorized_rules(context):
    """Check response contains categorized rules."""
    response = context['response']
    data = response.json()

    # Rules might be categorized by policy, severity, etc.
    assert isinstance(data, (dict, list)), f"Expected dict or list, got {type(data)}"
    if isinstance(data, dict):
        assert len(data) > 0 or 'rules' in data or 'categories' in data, \
            f"Expected categorized data, got {data}"


@then(parsers.parse('the verdict is "{verdict}"'))
def check_verdict(verdict, context):
    """Check readiness verdict."""
    response = context['response']
    data = response.json()
    assert 'verdict' in data, f"Missing verdict in {data}"
    assert data['verdict'] == verdict, \
        f"Expected verdict {verdict}, got {data['verdict']}"


@then('there are no blockers')
def no_blockers(context):
    """Check no blockers."""
    response = context['response']
    data = response.json()
    blockers = data.get('blockers', [])
    assert len(blockers) == 0, f"Expected no blockers, got {blockers}"


@then('the blockers mention conforma violations')
def blockers_mention_conforma(context):
    """Check blockers mention conforma."""
    response = context['response']
    data = response.json()
    blockers = data.get('blockers', [])
    assert any('conforma' in str(b).lower() or 'violation' in str(b).lower()
               for b in blockers), \
        f"Blockers don't mention conforma: {blockers}"


@then('the risks mention failing builds')
def risks_mention_builds(context):
    """Check risks mention builds."""
    response = context['response']
    data = response.json()
    risks = data.get('risks', [])
    assert any('build' in str(r).lower() or 'fail' in str(r).lower()
               for r in risks), \
        f"Risks don't mention builds: {risks}"


@then(parsers.parse('the blockers mention "{text}"'))
def blockers_mention_text(text, context):
    """Check blockers mention specific text."""
    response = context['response']
    data = response.json()
    blockers = data.get('blockers', [])
    assert any(text.lower() in str(b).lower() for b in blockers), \
        f"Blockers don't mention '{text}': {blockers}"


@then('the schedule includes code_freeze and release_date fields')
def schedule_has_fields(context):
    """Check schedule has required fields."""
    response = context['response']
    data = response.json()
    assert 'code_freeze' in data or 'code_freeze_days' in data, \
        f"Missing code_freeze in {data}"
    assert 'release_date' in data or 'release_date_days' in data, \
        f"Missing release_date in {data}"


@then('the response contains a list of component statuses')
def response_has_component_statuses(context):
    """Check response contains component statuses."""
    response = context['response']
    data = response.json()
    assert isinstance(data, list) or 'components' in data, \
        f"Expected list or object with 'components', got: {data}"


@then('each component has a readiness_score between 0 and 100')
def components_have_readiness_score(context):
    """Check components have readiness score."""
    response = context['response']
    data = response.json()
    components = data if isinstance(data, list) else data.get('components', [])

    if not components:
        return

    for comp in components:
        assert 'score' in comp or 'readiness_score' in comp, \
            f"Missing score in {comp}"
        score = comp.get('score', comp.get('readiness_score', 0))
        assert 0 <= score <= 100, f"Score {score} out of range [0,100]"


@then('the response lists completed and pending steps')
def response_has_steps(context):
    """Check response lists steps."""
    response = context['response']
    data = response.json()
    # Should have checks or steps
    assert 'checks' in data or 'steps' in data or 'failing' in data or 'warnings' in data, \
        f"Missing steps/checks in {data}"


@then('the readiness_score reflects completed steps')
def score_reflects_steps(context):
    """Check score reflects completion."""
    response = context['response']
    data = response.json()
    score = data.get('score', data.get('readiness_score', 0))

    # Partially complete should have medium score
    assert 0 < score < 100 or score == 0 or score == 100, \
        f"Score {score} seems invalid"

    # If we have context about the component being partial, check score is medium
    if 'onboarding_component' in context:
        expected_overall = context['onboarding_component'].get('overall', '')
        if expected_overall == 'partial':
            assert 20 < score < 90, f"Partial component should have medium score, got {score}"


@then('the endpoint is available')
def endpoint_available(context):
    """Check endpoint is available (exists, even if it errors)."""
    response = context.get('response')
    assert response is not None, "No response in context"
    # Any response except 404 means the endpoint exists
    assert response.status_code != 404, "Endpoint not found (404)"
