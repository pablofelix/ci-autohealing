"""Tests for onboarding.jira_analysis — Jira integration for onboarding tracking.

Covers: search_onboarding_jira, map_labels_to_steps, extract_pr_links,
analyze_bot_comments, compute_heuristic_analysis, build_diff_data,
get_jira_client_from_env, build_onboarding_report, and all private helpers.
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from onboarding.jira_analysis import (
    AUTOMATION_STEPS,
    ERROR_PATTERNS,
    _automation_step_actual,
    _automation_step_expected,
    _classify_pr_url,
    _extract_step_from_error,
    _jql_escape,
    _konflux_step_actual,
    _konflux_step_expected,
    _step_in_progress_reason,
    _suggest_fixes,
    analyze_bot_comments,
    build_diff_data,
    build_onboarding_report,
    compute_heuristic_analysis,
    extract_pr_links,
    get_jira_client_from_env,
    map_labels_to_steps,
    search_onboarding_jira,
)

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _make_jira_client():
    """Create a mock JiraClient with session and _api."""
    client = MagicMock()
    client._api.return_value = 'https://issues.redhat.com/rest/api/2/search'
    client._session = MagicMock()
    return client


def _jira_search_response(issues):
    """Build a mock response for Jira search."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'issues': issues}
    return resp


def _make_issue(key='RHOAIENG-100', summary='Onboarding foo',
                status_name='In Progress', status_cat='indeterminate',
                labels=None, comments=None):
    return {
        'key': key,
        'fields': {
            'summary': summary,
            'status': {
                'name': status_name,
                'statusCategory': {'key': status_cat},
            },
            'labels': labels or [],
            'comment': {'comments': comments or []},
        },
    }


def _make_comment(body, author='jira bot', created='2026-01-15T10:00:00.000+0000'):
    return {
        'body': body,
        'author': {'displayName': author},
        'created': created,
    }


# ═══════════════════════════════════════════════════════════════════════
# search_onboarding_jira
# ═══════════════════════════════════════════════════════════════════════

class TestSearchOnboardingJira:
    def test_returns_empty_when_no_client(self):
        assert search_onboarding_jira('my-comp', None) == []

    def test_returns_results_for_both_prefixes(self):
        client = _make_jira_client()
        issue1 = _make_issue('RHOAIENG-1', 'RHOAI Onboarding comp',
                             labels=['component-onboarding'])
        issue2 = _make_issue('RHOAIENG-2', 'ODH Onboarding comp',
                             labels=['devops-onboarding'])
        resp1 = _jira_search_response([issue1])
        resp2 = _jira_search_response([issue2])
        client._session.get.side_effect = [resp1, resp2]

        results = search_onboarding_jira('comp', client)
        assert len(results) == 2
        assert results[0]['key'] == 'RHOAIENG-1'
        assert results[0]['type'] == 'rhoai'
        assert results[1]['key'] == 'RHOAIENG-2'
        assert results[1]['type'] == 'odh'

    def test_result_structure(self):
        client = _make_jira_client()
        issue = _make_issue(
            'RHOAIENG-99', 'RHOAI Onboarding x',
            status_name='Done', status_cat='done',
            labels=['yaml-attached', 'quay-mr-merged'],
            comments=[_make_comment('some comment')],
        )
        client._session.get.side_effect = [
            _jira_search_response([issue]),
            _jira_search_response([]),
        ]

        results = search_onboarding_jira('x', client)
        assert len(results) == 1
        r = results[0]
        assert r['key'] == 'RHOAIENG-99'
        assert r['summary'] == 'RHOAI Onboarding x'
        assert r['status'] == 'Done'
        assert r['status_category'] == 'done'
        assert r['labels'] == ['yaml-attached', 'quay-mr-merged']
        assert r['url'] == 'https://issues.redhat.com/browse/RHOAIENG-99'
        assert r['type'] == 'rhoai'
        assert len(r['comments']) == 1

    def test_skips_non_200_responses(self):
        client = _make_jira_client()
        resp_404 = MagicMock()
        resp_404.status_code = 404
        client._session.get.side_effect = [resp_404, resp_404]

        results = search_onboarding_jira('comp', client)
        assert results == []

    def test_handles_exception_gracefully(self):
        client = _make_jira_client()
        client._session.get.side_effect = Exception('Connection timeout')

        results = search_onboarding_jira('comp', client)
        assert results == []

    def test_handles_missing_fields_in_issue(self):
        client = _make_jira_client()
        # Minimal issue with missing nested fields
        sparse_issue = {'key': 'RHOAIENG-50', 'fields': {}}
        client._session.get.side_effect = [
            _jira_search_response([sparse_issue]),
            _jira_search_response([]),
        ]
        results = search_onboarding_jira('comp', client)
        assert len(results) == 1
        assert results[0]['status'] == ''
        assert results[0]['labels'] == []

    def test_first_search_fails_second_succeeds(self):
        client = _make_jira_client()
        issue = _make_issue('RHOAIENG-10', 'ODH Onboarding comp')
        client._session.get.side_effect = [
            Exception('network error'),
            _jira_search_response([issue]),
        ]
        results = search_onboarding_jira('comp', client)
        assert len(results) == 1
        assert results[0]['type'] == 'odh'


# ═══════════════════════════════════════════════════════════════════════
# map_labels_to_steps
# ═══════════════════════════════════════════════════════════════════════

class TestMapLabelsToSteps:
    def test_no_labels_all_pending(self):
        steps = map_labels_to_steps([])
        assert all(s['status'] == 'pending' for s in steps)
        assert all(s['matched_label'] is None for s in steps)
        assert len(steps) == len(AUTOMATION_STEPS)

    def test_none_labels_all_pending(self):
        steps = map_labels_to_steps(None)
        assert all(s['status'] == 'pending' for s in steps)

    def test_done_label_marks_step_done(self):
        steps = map_labels_to_steps(['yaml-attached'])
        yaml_step = next(s for s in steps if s['key'] == 'yaml_attached')
        assert yaml_step['status'] == 'done'
        assert yaml_step['matched_label'] == 'yaml-attached'

    def test_in_progress_label(self):
        steps = map_labels_to_steps(['quay-mr-raised'])
        quay_step = next(s for s in steps if s['key'] == 'quay_repo')
        assert quay_step['status'] == 'in_progress'
        assert quay_step['matched_label'] == 'quay-mr-raised'

    def test_done_overrides_in_progress(self):
        # If both done and in_progress labels present, done wins
        steps = map_labels_to_steps(['quay-mr-raised', 'quay-mr-merged'])
        quay_step = next(s for s in steps if s['key'] == 'quay_repo')
        assert quay_step['status'] == 'done'
        assert quay_step['matched_label'] == 'quay-mr-merged'

    def test_multiple_done_labels_first_match(self):
        # delivery_repo has two done labels: delivery-repo-created, delivery-repo-exists
        steps = map_labels_to_steps(['delivery-repo-exists'])
        d_step = next(s for s in steps if s['key'] == 'delivery_repo')
        assert d_step['status'] == 'done'
        assert d_step['matched_label'] == 'delivery-repo-exists'

    def test_all_done(self):
        all_done_labels = []
        for step_def in AUTOMATION_STEPS:
            all_done_labels.append(step_def['done_labels'][0])
        steps = map_labels_to_steps(all_done_labels)
        assert all(s['status'] == 'done' for s in steps)

    def test_step_structure(self):
        steps = map_labels_to_steps(['okc-pr-raised'])
        okc = next(s for s in steps if s['key'] == 'okc_pr')
        assert 'key' in okc
        assert 'label' in okc
        assert 'status' in okc
        assert 'matched_label' in okc
        assert 'verification' in okc
        assert okc['label'] == 'odh-konflux-central PR'

    def test_unrelated_labels_ignored(self):
        steps = map_labels_to_steps(['random-label', 'another-one'])
        assert all(s['status'] == 'pending' for s in steps)


# ═══════════════════════════════════════════════════════════════════════
# extract_pr_links
# ═══════════════════════════════════════════════════════════════════════

class TestExtractPrLinks:
    def test_empty_comments(self):
        assert extract_pr_links([]) == {}

    def test_extracts_github_pr(self):
        comments = [_make_comment(
            'PR created: https://github.com/openshift/app-interface/pull/42'
        )]
        links = extract_pr_links(comments)
        assert 'quay_repo' in links
        assert links['quay_repo'][0]['url'] == \
            'https://github.com/openshift/app-interface/pull/42'

    def test_extracts_gitlab_mr(self):
        comments = [_make_comment(
            'MR: https://gitlab.cee.redhat.com/releng/konflux-release-data/merge_requests/99'
        )]
        links = extract_pr_links(comments)
        assert 'krd_mr' in links

    def test_classifies_okc_pr(self):
        comments = [_make_comment(
            'https://github.com/opendatahub-io/odh-konflux-central/pull/7'
        )]
        links = extract_pr_links(comments)
        assert 'okc_pr' in links

    def test_classifies_bundle_integration(self):
        comments = [_make_comment(
            'https://github.com/red-hat-data-services/odh-build-config/pull/5'
        )]
        links = extract_pr_links(comments)
        assert 'bundle_integration' in links

    def test_classifies_rhoai_build_config(self):
        comments = [_make_comment(
            'https://github.com/red-hat-data-services/rhoai-build-config/pull/3'
        )]
        links = extract_pr_links(comments)
        assert 'bundle_integration' in links

    def test_classifies_operator_pr(self):
        comments = [_make_comment(
            'https://github.com/opendatahub-io/opendatahub-operator/pull/12'
        )]
        links = extract_pr_links(comments)
        assert 'operator_pr' in links

    def test_classifies_rhods_operator(self):
        comments = [_make_comment(
            'https://github.com/red-hat-data-services/rhods-operator/pull/8'
        )]
        links = extract_pr_links(comments)
        assert 'operator_pr' in links

    def test_unclassified_url_not_in_results(self):
        comments = [_make_comment(
            'https://github.com/some-random-repo/unknown/pull/1'
        )]
        links = extract_pr_links(comments)
        assert links == {}

    def test_multiple_urls_in_one_comment(self):
        comments = [_make_comment(
            'PRs: https://github.com/openshift/app-interface/pull/1 '
            'and https://github.com/opendatahub-io/odh-konflux-central/pull/2'
        )]
        links = extract_pr_links(comments)
        assert 'quay_repo' in links
        assert 'okc_pr' in links

    def test_preserves_author_and_created(self):
        comments = [_make_comment(
            'https://github.com/openshift/app-interface/pull/5',
            author='Bot User',
            created='2026-02-20T08:30:00.000+0000',
        )]
        links = extract_pr_links(comments)
        entry = links['quay_repo'][0]
        assert entry['author'] == 'Bot User'
        assert entry['created'] == '2026-02-20T08:30:00.000+0000'

    def test_no_urls_in_comment(self):
        comments = [_make_comment('No links here, just text')]
        assert extract_pr_links(comments) == {}

    def test_missing_body_key(self):
        comments = [{'author': {'displayName': 'bot'}, 'created': '2026-01-01'}]
        assert extract_pr_links(comments) == {}


# ═══════════════════════════════════════════════════════════════════════
# analyze_bot_comments
# ═══════════════════════════════════════════════════════════════════════

class TestAnalyzeBotComments:
    def test_empty_comments(self):
        result = analyze_bot_comments([])
        assert result['has_errors'] is False
        assert result['error_categories'] == []
        assert result['retry_count'] == 0
        assert result['error_timeline'] == []
        assert result['stuck_steps'] == {}

    def test_none_comments(self):
        result = analyze_bot_comments(None)
        assert result['has_errors'] is False

    def test_non_bot_comments_ignored(self):
        comments = [_make_comment('error fail retry', author='Human User')]
        result = analyze_bot_comments(comments)
        assert result['has_errors'] is False

    def test_bot_comment_without_errors_ignored(self):
        comments = [_make_comment(
            'Step quay completed successfully', author='jira bot')]
        result = analyze_bot_comments(comments)
        assert result['has_errors'] is False

    def test_retry_storm_detected(self):
        comments = [
            _make_comment('error: will retry after 3 attempts', author='Jira Bot'),
        ]
        result = analyze_bot_comments(comments)
        assert result['has_errors'] is True
        cats = {c['category'] for c in result['error_categories']}
        assert 'retry_storm' in cats

    def test_branch_exists_detected(self):
        comments = [
            _make_comment(
                'Error: branch onboarding-comp already exists',
                author='devtestops jira bot',
            ),
        ]
        result = analyze_bot_comments(comments)
        cats = {c['category'] for c in result['error_categories']}
        assert 'branch_exists' in cats

    def test_cluster_connectivity_detected(self):
        comments = [
            _make_comment(
                'Could not connect to Konflux cluster: Dial timeout',
                author='Jira Bot',
            ),
        ]
        result = analyze_bot_comments(comments)
        cats = {c['category'] for c in result['error_categories']}
        assert 'cluster_connectivity' in cats

    def test_ci_environment_detected(self):
        comments = [
            _make_comment(
                'error: unable to auto-detect email address',
                author='Jira Bot',
            ),
        ]
        result = analyze_bot_comments(comments)
        cats = {c['category'] for c in result['error_categories']}
        assert 'ci_environment' in cats

    def test_component_not_found_detected(self):
        comments = [
            _make_comment(
                'error: Component odh-foo does NOT exist in namespace',
                author='Jira Bot',
            ),
        ]
        result = analyze_bot_comments(comments)
        cats = {c['category'] for c in result['error_categories']}
        assert 'component_not_found' in cats

    def test_workflow_dispatch_detected(self):
        comments = [
            _make_comment(
                'error: no workflow run appeared after dispatch',
                author='Jira Bot',
            ),
        ]
        result = analyze_bot_comments(comments)
        cats = {c['category'] for c in result['error_categories']}
        assert 'workflow_dispatch' in cats

    def test_dockerfile_missing_detected(self):
        comments = [
            _make_comment(
                'Dockerfile Digest Check failed: Could not fetch the Dockerfile',
                author='Jira Bot',
            ),
        ]
        result = analyze_bot_comments(comments)
        cats = {c['category'] for c in result['error_categories']}
        assert 'dockerfile_missing' in cats

    def test_vendor_inconsistency_detected(self):
        comments = [
            _make_comment(
                'error: vendor directory inconsistency detected',
                author='Jira Bot',
            ),
        ]
        result = analyze_bot_comments(comments)
        cats = {c['category'] for c in result['error_categories']}
        assert 'vendor_inconsistency' in cats

    def test_edit_yaml_error_detected(self):
        comments = [
            _make_comment(
                'edit_yaml.py error: invalid choice for command',
                author='Jira Bot',
            ),
        ]
        result = analyze_bot_comments(comments)
        cats = {c['category'] for c in result['error_categories']}
        assert 'edit_yaml_error' in cats

    def test_attachment_missing_detected(self):
        comments = [
            _make_comment(
                'error: component_onboarding_details.yaml not found on ticket',
                author='Jira Bot',
            ),
        ]
        result = analyze_bot_comments(comments)
        cats = {c['category'] for c in result['error_categories']}
        assert 'attachment_missing' in cats

    def test_http_422_detected(self):
        comments = [
            _make_comment(
                'error: HTTP 422 Unprocessable Entity',
                author='Jira Bot',
            ),
        ]
        result = analyze_bot_comments(comments)
        cats = {c['category'] for c in result['error_categories']}
        assert 'http_422' in cats

    def test_pr_merge_conflict_detected(self):
        comments = [
            _make_comment(
                'error: merge conflict detected in PR',
                author='Jira Bot',
            ),
        ]
        result = analyze_bot_comments(comments)
        cats = {c['category'] for c in result['error_categories']}
        assert 'pr_merge_conflict' in cats

    def test_build_failure_post_onboarding_detected(self):
        comments = [
            _make_comment(
                'error: build failed for component after onboarding',
                author='Jira Bot',
            ),
        ]
        result = analyze_bot_comments(comments)
        cats = {c['category'] for c in result['error_categories']}
        assert 'build_failure_post_onboarding' in cats

    def test_krd_version_missing_detected(self):
        comments = [
            _make_comment(
                'error: ProjectDevelopmentStream not found for v3.5',
                author='Jira Bot',
            ),
        ]
        result = analyze_bot_comments(comments)
        cats = {c['category'] for c in result['error_categories']}
        assert 'krd_version_missing' in cats

    def test_unclassified_error(self):
        comments = [
            _make_comment(
                'error: something totally unexpected happened',
                author='Jira Bot',
            ),
        ]
        result = analyze_bot_comments(comments)
        assert result['has_errors'] is True
        assert result['error_timeline'][0]['categories'] == ['unclassified']

    def test_stuck_steps_detected_three_failures(self):
        comments = [
            _make_comment('error: krd step failed attempt 1', author='Jira Bot',
                          created='2026-01-01T01:00:00.000+0000'),
            _make_comment('error: krd step failed attempt 2', author='Jira Bot',
                          created='2026-01-01T02:00:00.000+0000'),
            _make_comment('error: krd step failed attempt 3', author='Jira Bot',
                          created='2026-01-01T03:00:00.000+0000'),
        ]
        result = analyze_bot_comments(comments)
        assert 'krd_mr' in result['stuck_steps']
        assert result['stuck_steps']['krd_mr'] == 3

    def test_not_stuck_with_only_two_failures(self):
        comments = [
            _make_comment('error: krd step failed attempt 1', author='Jira Bot'),
            _make_comment('error: krd step failed attempt 2', author='Jira Bot'),
        ]
        result = analyze_bot_comments(comments)
        assert result['stuck_steps'] == {}

    def test_retry_count_totals(self):
        comments = [
            _make_comment('error: will retry after timeout', author='Jira Bot',
                          created='2026-01-01T01:00:00.000+0000'),
            _make_comment('error: will retry step again', author='Jira Bot',
                          created='2026-01-01T02:00:00.000+0000'),
        ]
        result = analyze_bot_comments(comments)
        assert result['retry_count'] >= 2

    def test_error_categories_sorted_by_count(self):
        comments = [
            _make_comment('error: will retry step 1', author='Jira Bot',
                          created='2026-01-01T01:00:00.000+0000'),
            _make_comment('error: will retry step 2', author='Jira Bot',
                          created='2026-01-01T02:00:00.000+0000'),
            _make_comment('error: merge conflict', author='Jira Bot',
                          created='2026-01-01T03:00:00.000+0000'),
        ]
        result = analyze_bot_comments(comments)
        cats = result['error_categories']
        for i in range(len(cats) - 1):
            assert cats[i]['count'] >= cats[i + 1]['count']

    def test_error_timeline_has_excerpts(self):
        long_body = 'error: ' + 'x' * 300
        comments = [_make_comment(long_body, author='Jira Bot')]
        result = analyze_bot_comments(comments)
        assert len(result['error_timeline'][0]['excerpt']) <= 200

    def test_first_seen_last_seen_tracking(self):
        comments = [
            _make_comment('error: will retry', author='Jira Bot',
                          created='2026-01-01T01:00:00.000+0000'),
            _make_comment('error: will retry again', author='Jira Bot',
                          created='2026-01-02T05:00:00.000+0000'),
        ]
        result = analyze_bot_comments(comments)
        retry_cat = next(
            c for c in result['error_categories'] if c['category'] == 'retry_storm')
        assert retry_cat['first_seen'] == '2026-01-01T01:00:00.000+0000'
        assert retry_cat['last_seen'] == '2026-01-02T05:00:00.000+0000'


# ═══════════════════════════════════════════════════════════════════════
# _extract_step_from_error
# ═══════════════════════════════════════════════════════════════════════

class TestExtractStepFromError:
    def test_projectdevelopmentstream_maps_to_krd(self):
        assert _extract_step_from_error(
            'ProjectDevelopmentStream not found') == 'krd_mr'

    def test_known_alias_quay(self):
        assert _extract_step_from_error(
            'create-quay-repo step failed') == 'quay_repo'

    def test_known_alias_krd(self):
        assert _extract_step_from_error('krd merge request failed') == 'krd_mr'

    def test_known_alias_okc(self):
        assert _extract_step_from_error('okc pr creation error') == 'okc_pr'

    def test_known_alias_operator(self):
        assert _extract_step_from_error(
            'integrate-component-with-odh-operator failed') == 'operator_pr'

    def test_known_alias_bundle(self):
        assert _extract_step_from_error('bundle pr failed') == 'bundle_integration'

    def test_known_alias_renovate(self):
        assert _extract_step_from_error(
            'enable-renovate-on-rhoai-component-repo error') == 'renovate'

    def test_known_alias_product_listing(self):
        assert _extract_step_from_error(
            'product-listing step failed') == 'product_listing'

    def test_known_alias_auto_merge(self):
        assert _extract_step_from_error(
            'auto-merge enable failed') == 'auto_merge'

    def test_known_alias_delivery_repo(self):
        assert _extract_step_from_error(
            'delivery-repo creation error') == 'delivery_repo'

    def test_known_alias_rkc(self):
        assert _extract_step_from_error(
            'rkc configuration error') == 'rkc'

    def test_known_alias_rhoai_konflux_config(self):
        assert _extract_step_from_error(
            'rhoai-konflux-config failed') == 'rkc'

    def test_known_alias_onboarder_workflow(self):
        assert _extract_step_from_error(
            'run-odh-konflux-onboarder-workflow dispatch timeout') == 'onboarder_workflow'

    def test_known_alias_odh_konflux_onboarder(self):
        assert _extract_step_from_error(
            'odh-konflux-onboarder timed out') == 'onboarder_workflow'

    def test_step_regex_fallback(self):
        result = _extract_step_from_error('step 3: custom-step-name failed')
        assert result == 'custom-step-name'

    def test_unknown_returns_unknown(self):
        assert _extract_step_from_error('something went wrong') == 'unknown'


# ═══════════════════════════════════════════════════════════════════════
# _classify_pr_url
# ═══════════════════════════════════════════════════════════════════════

class TestClassifyPrUrl:
    def test_app_interface(self):
        assert _classify_pr_url(
            'https://github.com/openshift/app-interface/pull/1') == 'quay_repo'

    def test_odh_konflux_central(self):
        assert _classify_pr_url(
            'https://github.com/opendatahub-io/odh-konflux-central/pull/2') == 'okc_pr'

    def test_konflux_release_data(self):
        assert _classify_pr_url(
            'https://gitlab.cee.redhat.com/releng/konflux-release-data/merge_requests/3'
        ) == 'krd_mr'

    def test_odh_build_config(self):
        assert _classify_pr_url(
            'https://github.com/red-hat-data-services/odh-build-config/pull/4'
        ) == 'bundle_integration'

    def test_rhoai_build_config(self):
        assert _classify_pr_url(
            'https://github.com/red-hat-data-services/rhoai-build-config/pull/5'
        ) == 'bundle_integration'

    def test_opendatahub_operator(self):
        assert _classify_pr_url(
            'https://github.com/opendatahub-io/opendatahub-operator/pull/6'
        ) == 'operator_pr'

    def test_rhods_operator(self):
        assert _classify_pr_url(
            'https://github.com/red-hat-data-services/rhods-operator/pull/7'
        ) == 'operator_pr'

    def test_unknown_repo_returns_none(self):
        assert _classify_pr_url(
            'https://github.com/random/repo/pull/8') is None

    def test_case_insensitive(self):
        assert _classify_pr_url(
            'https://github.com/openshift/APP-INTERFACE/pull/9') == 'quay_repo'


# ═══════════════════════════════════════════════════════════════════════
# _jql_escape
# ═══════════════════════════════════════════════════════════════════════

class TestJqlEscape:
    def test_escapes_double_quotes(self):
        assert _jql_escape('comp "v1"') == 'comp \\"v1\\"'

    def test_no_quotes_unchanged(self):
        assert _jql_escape('simple-name') == 'simple-name'

    def test_empty_string(self):
        assert _jql_escape('') == ''


# ═══════════════════════════════════════════════════════════════════════
# compute_heuristic_analysis
# ═══════════════════════════════════════════════════════════════════════

class TestComputeHeuristicAnalysis:
    def _all_done_steps(self):
        return [{'key': s['key'], 'label': s['label'],
                 'status': 'done', 'matched_label': s['done_labels'][0],
                 'verification': s['verification']}
                for s in AUTOMATION_STEPS]

    def _some_done_then_pending(self, done_count=3):
        steps = []
        for i, s in enumerate(AUTOMATION_STEPS):
            status = 'done' if i < done_count else 'pending'
            steps.append({
                'key': s['key'], 'label': s['label'],
                'status': status,
                'matched_label': s['done_labels'][0] if status == 'done' else None,
                'verification': s['verification'],
            })
        return steps

    def test_no_blockers_returns_on_track(self):
        result = compute_heuristic_analysis([], self._all_done_steps())
        assert result['status'] == 'on_track'
        assert result['summary'] == 'No blockers detected'

    def test_in_progress_step_detected(self):
        steps = self._some_done_then_pending(2)
        steps[2]['status'] = 'in_progress'
        steps[2]['matched_label'] = 'quay-mr-raised'
        result = compute_heuristic_analysis([], steps)
        assert result['status'] == 'blocked'
        assert result['blocked_at'] == steps[2]['label']

    def test_pending_after_done_detected(self):
        steps = self._some_done_then_pending(3)
        result = compute_heuristic_analysis([], steps)
        assert result['status'] == 'blocked'
        assert result['blocked_step_key'] == AUTOMATION_STEPS[3]['key']
        assert 'not started' in result['blocked_reason'].lower()

    def test_impact_counts_pending_steps(self):
        steps = self._some_done_then_pending(2)
        steps[2]['status'] = 'in_progress'
        steps[2]['matched_label'] = 'quay-mr-raised'
        result = compute_heuristic_analysis([], steps)
        # All steps after done_count should be pending except the in_progress one
        pending = sum(1 for s in steps if s['status'] == 'pending')
        assert str(pending) in result['impact']

    def test_fix_suggestions_populated(self):
        steps = self._some_done_then_pending(2)
        steps[2]['status'] = 'in_progress'
        steps[2]['matched_label'] = 'quay-mr-raised'
        result = compute_heuristic_analysis([], steps)
        assert 'fix_component' in result
        assert 'fix_automation' in result

    def test_konflux_blocked_added_to_analysis(self):
        konflux_steps = [
            {'step': 'first_build', 'status': 'blocked',
             'detail': 'PipelineRun failed', 'fix': 'Check Tekton logs'},
        ]
        result = compute_heuristic_analysis(
            konflux_steps, self._all_done_steps())
        assert result['status'] == 'blocked'
        assert result['konflux_blocked_at'] == 'first_build'
        assert result['konflux_blocked_detail'] == 'PipelineRun failed'
        assert result['konflux_fix'] == 'Check Tekton logs'

    def test_both_automation_and_konflux_blocked(self):
        steps = self._some_done_then_pending(1)
        steps[1]['status'] = 'in_progress'
        steps[1]['matched_label'] = 'okc-pr-raised'
        konflux_steps = [
            {'step': 'pac', 'status': 'failed', 'detail': 'No PaC CR',
             'fix': 'Create PaC'},
        ]
        result = compute_heuristic_analysis(konflux_steps, steps)
        assert result['status'] == 'blocked'
        assert 'blocked_at' in result
        assert 'konflux_blocked_at' in result

    def test_no_automation_steps_only_konflux(self):
        konflux_steps = [
            {'step': 'component_cr', 'status': 'failed',
             'detail': 'Missing', 'fix': 'Create CR'},
        ]
        result = compute_heuristic_analysis(konflux_steps, [])
        assert result['status'] == 'blocked'
        assert result['konflux_blocked_at'] == 'component_cr'

    def test_all_pending_first_step_blocked(self):
        steps = self._some_done_then_pending(0)
        result = compute_heuristic_analysis([], steps)
        assert result['status'] == 'blocked'
        assert result['blocked_step_key'] == AUTOMATION_STEPS[0]['key']


# ═══════════════════════════════════════════════════════════════════════
# _step_in_progress_reason
# ═══════════════════════════════════════════════════════════════════════

class TestStepInProgressReason:
    def test_raised_with_pr_link(self):
        step = {'key': 'quay_repo', 'matched_label': 'quay-mr-raised'}
        pr_links = {'quay_repo': [{'url': 'https://github.com/x/y/pull/1'}]}
        reason = _step_in_progress_reason(step, pr_links)
        assert 'not merged' in reason
        assert 'https://github.com/x/y/pull/1' in reason

    def test_raised_without_pr_link(self):
        step = {'key': 'quay_repo', 'matched_label': 'quay-mr-raised'}
        reason = _step_in_progress_reason(step, None)
        assert 'raised but not merged yet' in reason

    def test_non_raised_label(self):
        step = {'key': 'yaml_attached', 'matched_label': 'yaml-progress'}
        reason = _step_in_progress_reason(step, {})
        assert reason == 'Step in progress'

    def test_raised_and_merged_in_label(self):
        # Label contains both 'raised' and 'merged' -- not a typical case
        step = {'key': 'quay_repo', 'matched_label': 'quay-mr-raised-and-merged'}
        reason = _step_in_progress_reason(step, {})
        assert reason == 'Step in progress'


# ═══════════════════════════════════════════════════════════════════════
# _suggest_fixes
# ═══════════════════════════════════════════════════════════════════════

class TestSuggestFixes:
    def test_yaml_attached_fix(self):
        step = {'key': 'yaml_attached'}
        comp, auto = _suggest_fixes(step, None)
        assert 'YAML' in comp or 'yaml' in comp.lower()

    def test_quay_repo_fix_with_link(self):
        step = {'key': 'quay_repo'}
        pr_links = {'quay_repo': [{'url': 'https://github.com/x/y/pull/1'}]}
        comp, auto = _suggest_fixes(step, pr_links)
        assert 'https://github.com/x/y/pull/1' in comp

    def test_quay_repo_fix_without_link(self):
        step = {'key': 'quay_repo'}
        comp, auto = _suggest_fixes(step, None)
        assert 'app-interface' in comp.lower()

    def test_all_known_steps_have_fixes(self):
        known_keys = [s['key'] for s in AUTOMATION_STEPS]
        for key in known_keys:
            comp, auto = _suggest_fixes({'key': key}, None)
            assert comp, f'No fix_component for {key}'
            assert auto, f'No fix_automation for {key}'

    def test_unknown_step_gets_default(self):
        comp, auto = _suggest_fixes({'key': 'nonexistent_step'}, None)
        assert 'Jira ticket' in comp
        assert 'aiops-infra' in auto


# ═══════════════════════════════════════════════════════════════════════
# build_diff_data
# ═══════════════════════════════════════════════════════════════════════

class TestBuildDiffData:
    def test_empty_inputs(self):
        assert build_diff_data([], []) == []

    def test_konflux_steps_in_diff(self):
        konflux = [{'step': 'component_cr', 'label': 'Component CR',
                     'status': 'done', 'detail': 'exists'}]
        diffs = build_diff_data(konflux, [])
        assert len(diffs) == 1
        assert diffs[0]['section'] == 'konflux'
        assert diffs[0]['step'] == 'component_cr'
        assert diffs[0]['status'] == 'done'

    def test_automation_steps_in_diff(self):
        auto_steps = [{
            'key': 'yaml_attached', 'label': 'YAML attached',
            'status': 'done', 'matched_label': 'yaml-attached',
            'verification': 'Attachment present',
        }]
        diffs = build_diff_data([], auto_steps)
        assert len(diffs) == 1
        assert diffs[0]['section'] == 'automation'
        assert diffs[0]['step'] == 'yaml_attached'

    def test_both_sections(self):
        konflux = [{'step': 'pac', 'label': 'PaC', 'status': 'pending'}]
        auto = [{'key': 'quay_repo', 'label': 'Quay', 'status': 'done',
                 'matched_label': 'quay-mr-merged', 'verification': 'v'}]
        diffs = build_diff_data(konflux, auto)
        sections = {d['section'] for d in diffs}
        assert sections == {'konflux', 'automation'}

    def test_diff_has_expected_and_actual(self):
        auto = [{'key': 'yaml_attached', 'label': 'YAML attached',
                 'status': 'pending', 'matched_label': None,
                 'verification': 'v'}]
        diffs = build_diff_data([], auto)
        assert 'expected' in diffs[0]
        assert 'actual' in diffs[0]


# ═══════════════════════════════════════════════════════════════════════
# _konflux_step_expected / _konflux_step_actual
# ═══════════════════════════════════════════════════════════════════════

class TestKonfluxStepExpectedActual:
    def test_expected_known_step(self):
        assert 'Component CR' in _konflux_step_expected({'step': 'component_cr'})

    def test_expected_unknown_step_default(self):
        assert 'completed successfully' in _konflux_step_expected({'step': 'unknown_step'})

    def test_expected_uses_key_fallback(self):
        result = _konflux_step_expected({'key': 'first_build'})
        assert 'PipelineRun' in result

    def test_actual_done(self):
        assert _konflux_step_actual({'status': 'done'}) == 'OK'

    def test_actual_done_with_detail(self):
        assert _konflux_step_actual(
            {'status': 'done', 'detail': 'found'}) == 'OK — found'

    def test_actual_in_progress(self):
        assert _konflux_step_actual({'status': 'in_progress'}) == 'In progress'

    def test_actual_in_progress_with_detail(self):
        r = _konflux_step_actual({'status': 'in_progress', 'detail': 'building'})
        assert 'building' in r

    def test_actual_blocked(self):
        r = _konflux_step_actual({'status': 'blocked', 'detail': 'No PR'})
        assert 'FAILED' in r
        assert 'No PR' in r

    def test_actual_failed(self):
        r = _konflux_step_actual({'status': 'failed'})
        assert 'Failed' in r

    def test_actual_pending(self):
        r = _konflux_step_actual({'status': 'pending'})
        assert 'Not done' in r

    def test_actual_pending_with_detail(self):
        r = _konflux_step_actual({'status': 'pending', 'detail': 'waiting'})
        assert 'waiting' in r

    def test_actual_unknown_status(self):
        r = _konflux_step_actual({'status': 'weird'})
        assert r == 'Unknown'

    def test_actual_unknown_with_detail(self):
        r = _konflux_step_actual({'status': 'weird', 'detail': 'info'})
        assert r == 'info'


# ═══════════════════════════════════════════════════════════════════════
# _automation_step_expected / _automation_step_actual
# ═══════════════════════════════════════════════════════════════════════

class TestAutomationStepExpectedActual:
    def test_expected_known_step(self):
        step = {'key': 'yaml_attached', 'label': 'YAML', 'status': 'done',
                'verification': 'Attachment present'}
        result = _automation_step_expected(step)
        assert 'yaml-attached' in result

    def test_expected_unknown_step(self):
        step = {'key': 'unknown_key', 'verification': 'Custom check'}
        result = _automation_step_expected(step)
        assert result == 'Custom check'

    def test_expected_unknown_no_verification(self):
        step = {'key': 'unknown_key'}
        result = _automation_step_expected(step)
        assert result == 'Step completed'

    def test_expected_non_dict(self):
        result = _automation_step_expected('not a dict')
        assert result == 'not a dict'

    def test_actual_done_with_label(self):
        result = _automation_step_actual(
            {'status': 'done', 'matched_label': 'yaml-attached'})
        assert 'yaml-attached' in result

    def test_actual_done_without_label(self):
        result = _automation_step_actual({'status': 'done', 'matched_label': ''})
        assert result == 'OK'

    def test_actual_in_progress_with_label(self):
        result = _automation_step_actual(
            {'status': 'in_progress', 'matched_label': 'quay-mr-raised'})
        assert 'quay-mr-raised' in result
        assert 'not merged' in result

    def test_actual_in_progress_without_label(self):
        result = _automation_step_actual(
            {'status': 'in_progress', 'matched_label': ''})
        assert result == 'In progress'

    def test_actual_pending(self):
        result = _automation_step_actual({'status': 'pending'})
        assert result == 'No label found'

    def test_actual_unknown(self):
        result = _automation_step_actual({'status': 'some_other'})
        assert result == 'No label found'


# ═══════════════════════════════════════════════════════════════════════
# get_jira_client_from_env
# ═══════════════════════════════════════════════════════════════════════

class TestGetJiraClientFromEnv:
    @patch.dict(os.environ, {'JIRA_TOKEN': ''}, clear=False)
    def test_no_token_returns_none(self):
        result = get_jira_client_from_env()
        assert result is None

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_env_returns_none(self):
        result = get_jira_client_from_env()
        assert result is None

    @patch.dict(os.environ, {
        'JIRA_TOKEN': 'test-token',
        'JIRA_BASE_URL': 'https://jira.test.com',
        'JIRA_EMAIL': 'user@test.com',
        'JIRA_PROJECT': 'TEST',
    }, clear=False)
    def test_creates_client_with_env_vars(self):
        with patch('clients.jira_client.JiraClient') as mock_jira_cls:
            result = get_jira_client_from_env()
            mock_jira_cls.assert_called_once_with(
                'https://jira.test.com', 'user@test.com',
                'test-token', 'TEST',
            )

    @patch.dict(os.environ, {'JIRA_TOKEN': 'tok'}, clear=True)
    def test_uses_defaults_when_env_missing(self):
        with patch('clients.jira_client.JiraClient') as mock_cls:
            get_jira_client_from_env()
            mock_cls.assert_called_once_with(
                'https://issues.redhat.com', '', 'tok', 'RHOAIENG',
            )


# ═══════════════════════════════════════════════════════════════════════
# build_onboarding_report
# ═══════════════════════════════════════════════════════════════════════

class TestBuildOnboardingReport:
    def test_minimal_report(self):
        report = build_onboarding_report('comp', [], None, None, None, None)
        assert report['component'] == 'comp'
        assert report['jira_tickets'] == []

    def test_jira_tickets_mapped(self):
        tickets = [
            {'key': 'RHOAIENG-1', 'type': 'rhoai', 'status': 'Open',
             'url': 'https://jira/1', 'extra': 'ignored'},
        ]
        report = build_onboarding_report('comp', tickets, None, None, None, None)
        assert len(report['jira_tickets']) == 1
        assert report['jira_tickets'][0]['key'] == 'RHOAIENG-1'
        assert 'extra' not in report['jira_tickets'][0]

    def test_none_jira_tickets(self):
        report = build_onboarding_report('comp', None, None, None, None, None)
        assert report['jira_tickets'] == []

    def test_progress_calculation(self):
        steps = [
            {'key': 'a', 'label': 'A', 'status': 'done'},
            {'key': 'b', 'label': 'B', 'status': 'done'},
            {'key': 'c', 'label': 'C', 'status': 'pending'},
            {'key': 'd', 'label': 'D', 'status': 'pending'},
        ]
        report = build_onboarding_report('comp', [], steps, None, None, None)
        assert report['progress']['done'] == 2
        assert report['progress']['total'] == 4
        assert report['progress']['pct'] == 50

    def test_progress_all_done(self):
        steps = [
            {'key': 'a', 'label': 'A', 'status': 'done'},
        ]
        report = build_onboarding_report('comp', [], steps, None, None, None)
        assert report['progress']['pct'] == 100

    def test_progress_empty_steps(self):
        report = build_onboarding_report('comp', [], [], None, None, None)
        assert 'progress' not in report

    def test_current_step_in_progress(self):
        steps = [
            {'key': 'a', 'label': 'A', 'status': 'done'},
            {'key': 'b', 'label': 'B', 'status': 'in_progress'},
            {'key': 'c', 'label': 'C', 'status': 'pending'},
        ]
        report = build_onboarding_report('comp', [], steps, None, None, None)
        assert report['current_step']['key'] == 'b'

    def test_current_step_pending(self):
        steps = [
            {'key': 'a', 'label': 'A', 'status': 'done'},
            {'key': 'b', 'label': 'B', 'status': 'pending'},
        ]
        report = build_onboarding_report('comp', [], steps, None, None, None)
        assert report['current_step']['key'] == 'b'

    def test_current_step_all_done_is_none(self):
        steps = [
            {'key': 'a', 'label': 'A', 'status': 'done'},
        ]
        report = build_onboarding_report('comp', [], steps, None, None, None)
        assert report['current_step'] is None

    def test_steps_with_prs(self):
        steps = [
            {'key': 'a', 'label': 'A', 'status': 'done',
             'pr_links': [{'url': 'https://github.com/x/y/pull/1'}]},
            {'key': 'b', 'label': 'B', 'status': 'pending'},
        ]
        report = build_onboarding_report('comp', [], steps, None, None, None)
        assert len(report['steps_with_prs']) == 1
        assert report['steps_with_prs'][0]['step'] == 'a'

    def test_blocker_from_analysis(self):
        analysis = {
            'status': 'blocked',
            'blocked_at': 'Quay repo',
            'blocked_reason': 'MR not merged',
            'fix_component': 'Review MR',
            'fix_automation': 'Add timeout',
        }
        report = build_onboarding_report('comp', [], None, None, None, analysis)
        assert report['blocker']['step'] == 'Quay repo'
        assert report['blocker']['reason'] == 'MR not merged'

    def test_no_blocker_when_on_track(self):
        analysis = {'status': 'on_track'}
        report = build_onboarding_report('comp', [], None, None, None, analysis)
        assert 'blocker' not in report

    def test_error_history_from_bot_analysis(self):
        bot_analysis = {
            'has_errors': True,
            'retry_count': 5,
            'error_categories': [
                {'category': 'retry_storm', 'count': 3,
                 'description': 'Bot retrying', 'automation_fix': 'Add backoff',
                 'first_seen': '2026-01-01', 'last_seen': '2026-01-02'},
                {'category': 'http_422', 'count': 2,
                 'description': 'API error', 'automation_fix': 'Pre-check',
                 'first_seen': '2026-01-01', 'last_seen': '2026-01-01'},
            ],
            'stuck_steps': {'krd_mr': 4},
            'error_timeline': [],
        }
        report = build_onboarding_report(
            'comp', [], None, None, bot_analysis, None)
        assert report['error_history']['total_errors'] == 5
        assert report['error_history']['top_category'] == 'retry_storm'
        assert len(report['error_history']['categories']) == 2
        assert report['error_history']['stuck_steps'] == {'krd_mr': 4}

    def test_no_error_history_when_no_errors(self):
        bot_analysis = {'has_errors': False}
        report = build_onboarding_report(
            'comp', [], None, None, bot_analysis, None)
        assert 'error_history' not in report

    def test_action_items_from_stuck_steps(self):
        bot_analysis = {
            'has_errors': True,
            'retry_count': 3,
            'error_categories': [
                {'category': 'retry_storm', 'count': 3,
                 'description': 'd', 'automation_fix': 'f',
                 'first_seen': 'x', 'last_seen': 'y'},
            ],
            'stuck_steps': {'krd_mr': 5},
            'error_timeline': [],
        }
        report = build_onboarding_report(
            'comp', [], None, None, bot_analysis, None)
        assert len(report['action_items']) >= 1
        stuck_action = next(
            a for a in report['action_items']
            if a['type'] == 'automation_bug'
        )
        assert 'krd_mr' in stuck_action['action']
        assert stuck_action['priority'] == 'HIGH'

    def test_action_items_from_high_count_categories(self):
        bot_analysis = {
            'has_errors': True,
            'retry_count': 7,
            'error_categories': [
                {'category': 'retry_storm', 'count': 7,
                 'description': 'Bot retrying', 'automation_fix': 'Add backoff',
                 'first_seen': 'x', 'last_seen': 'y'},
            ],
            'stuck_steps': {},
            'error_timeline': [],
        }
        report = build_onboarding_report(
            'comp', [], None, None, bot_analysis, None)
        assert any(
            a['type'] == 'automation_improvement'
            for a in report.get('action_items', [])
        )

    def test_no_action_items_for_low_count_categories(self):
        bot_analysis = {
            'has_errors': True,
            'retry_count': 2,
            'error_categories': [
                {'category': 'http_422', 'count': 2,
                 'description': 'd', 'automation_fix': 'f',
                 'first_seen': 'x', 'last_seen': 'y'},
            ],
            'stuck_steps': {},
            'error_timeline': [],
        }
        report = build_onboarding_report(
            'comp', [], None, None, bot_analysis, None)
        assert 'action_items' not in report

    def test_bot_analysis_none_skipped(self):
        report = build_onboarding_report('comp', [], None, None, None, None)
        assert 'error_history' not in report

    def test_error_history_no_categories(self):
        bot_analysis = {
            'has_errors': True,
            'retry_count': 0,
            'error_categories': [],
            'stuck_steps': {},
            'error_timeline': [{'step': 'x', 'categories': ['unclassified']}],
        }
        report = build_onboarding_report(
            'comp', [], None, None, bot_analysis, None)
        assert report['error_history']['top_category'] is None


# ═══════════════════════════════════════════════════════════════════════
# AUTOMATION_STEPS and ERROR_PATTERNS data integrity
# ═══════════════════════════════════════════════════════════════════════

class TestDataIntegrity:
    def test_automation_steps_have_required_keys(self):
        for step in AUTOMATION_STEPS:
            assert 'key' in step
            assert 'label' in step
            assert 'done_labels' in step
            assert 'in_progress_labels' in step
            assert 'verification' in step
            assert len(step['done_labels']) >= 1

    def test_automation_steps_keys_unique(self):
        keys = [s['key'] for s in AUTOMATION_STEPS]
        assert len(keys) == len(set(keys)), 'Duplicate keys found'

    def test_error_patterns_have_required_fields(self):
        for p in ERROR_PATTERNS:
            assert 'category' in p
            assert 'regex' in p
            assert 'description' in p
            assert 'automation_fix' in p

    def test_error_pattern_categories_unique(self):
        cats = [p['category'] for p in ERROR_PATTERNS]
        assert len(cats) == len(set(cats)), 'Duplicate categories found'

    def test_error_patterns_regex_compiles(self):
        for p in ERROR_PATTERNS:
            # Just verify .search works without error
            p['regex'].search('test string')
