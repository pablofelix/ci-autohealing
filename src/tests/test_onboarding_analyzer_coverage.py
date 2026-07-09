"""Tests for OnboardingAnalyzer: prompt building, response parsing, full flow."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _patch_system_prompt():
    """Patch SYSTEM_PROMPT for all tests so we never call load_prompt."""
    with patch('analyzers.onboarding_analyzer.SYSTEM_PROMPT', 'test system prompt'):
        yield


def _make_analyzer():
    """Create an OnboardingAnalyzer with a mocked LLM."""
    from analyzers.onboarding_analyzer import OnboardingAnalyzer
    llm = MagicMock()
    llm.model_name.return_value = 'claude-test-model'
    return OnboardingAnalyzer(llm)


def _minimal_data(**overrides):
    """Minimal onboarding_data with required fields."""
    base = {
        'component': 'odh-model-controller',
        'phase': 'onboarding',
        'progress': 50,
        'type': 'operator',
    }
    base.update(overrides)
    return base


def _full_data():
    """Onboarding data with every optional section populated."""
    return {
        'component': 'odh-dashboard',
        'phase': 'konflux-onboarding',
        'progress': 75,
        'type': 'component',
        'jira_tickets': [
            {
                'key': 'RHOAIENG-1234',
                'type': 'Task',
                'summary': 'Onboard odh-dashboard to Konflux',
                'status': 'In Progress',
            },
            {
                'key': 'RHOAIENG-1235',
                'type': 'Sub-task',
                'summary': 'Create nudge PR for golang deps',
                'status': 'Done',
            },
        ],
        'steps': [
            {'step': 'create-app', 'status': 'done', 'label': 'Create application'},
            {
                'step': 'build-pipeline',
                'status': 'failed',
                'label': 'Build pipeline',
                'detail': 'FIPS check failed',
                'fix': 'Add FIPS-compliant base image',
            },
        ],
        'automation_steps': [
            {
                'label': 'Create component',
                'status': 'done',
                'matched_label': 'component-created',
                'pr_links': ['https://github.com/org/repo/pull/1'],
            },
            {
                'label': 'Configure pipeline',
                'status': 'pending',
            },
        ],
        'bot_error_analysis': {
            'has_errors': True,
            'retry_count': 7,
            'error_categories': [
                {
                    'category': 'git_conflict',
                    'count': 3,
                    'description': 'Merge conflict in Dockerfile',
                    'automation_fix': 'rebase',
                },
                {
                    'category': 'auth_failure',
                    'count': 2,
                    'description': 'Token expired',
                    'automation_fix': 'refresh token',
                },
            ],
            'stuck_steps': {
                'configure-pipeline': 5,
                'create-nudge-pr': 3,
            },
            'error_timeline': [
                {
                    'timestamp': '2025-06-01T10:00:00Z',
                    'step': 'configure-pipeline',
                    'categories': ['git_conflict'],
                    'excerpt': 'CONFLICT (content): Merge conflict in .tekton/pipeline.yaml',
                },
                {
                    'timestamp': '2025-06-02T11:00:00Z',
                    'step': 'configure-pipeline',
                    'categories': ['auth_failure', 'git_conflict'],
                    'excerpt': 'Failed to push: remote rejected',
                },
            ],
        },
        'odh_bot_error_analysis': {
            'has_errors': True,
            'retry_count': 2,
            'error_categories': [
                {
                    'category': 'image_not_found',
                    'count': 2,
                    'description': 'Base image tag not found in registry',
                },
            ],
        },
        'analysis': {
            'status': 'blocked',
            'blocked_at': 'configure-pipeline',
            'blocked_reason': 'Merge conflicts prevent pipeline config',
            'impact': 'Cannot proceed to build step',
            'fix_component': 'Resolve Dockerfile merge conflict',
            'fix_automation': 'Retry after rebase',
        },
        'report': {
            'action_items': [
                {'priority': 'HIGH', 'action': 'Resolve merge conflict in Dockerfile'},
                {'priority': 'MED', 'action': 'Refresh bot token'},
            ],
        },
        'nudge_prs': [
            {
                'package': 'golang.org/x/net',
                'from_version': 'v0.23.0',
                'to_version': 'v0.25.0',
                'merged': True,
                'pr_url': 'https://github.com/org/repo/pull/42',
            },
            {
                'package': 'k8s.io/client-go',
                'from_version': 'v0.28.0',
                'to_version': 'v0.29.0',
                'merged': False,
                'pr_url': 'https://github.com/org/repo/pull/43',
            },
        ],
    }


def _valid_tool_call(**overrides):
    """Build a valid record_onboarding_analysis tool call dict."""
    base = {
        'name': 'record_onboarding_analysis',
        'input': {
            'root_cause': 'Pipeline config blocked by merge conflict in .tekton/',
            'failure_category': 'automation_stuck',
            'confidence_score': 0.85,
            'recommended_fix': 'Rebase the bot branch onto main and retry the pipeline config step',
            'blocked_step': 'configure-pipeline',
            'can_auto_fix': True,
            'requires_human_review': False,
        },
    }
    if overrides:
        base['input'].update(overrides)
    return base


def _mock_llm_response(tool_calls=None, input_tokens=100, output_tokens=50):
    """Create a mock LLM response object."""
    resp = MagicMock()
    resp.tool_calls = tool_calls
    resp.input_tokens = input_tokens
    resp.output_tokens = output_tokens
    return resp


# ═══════════════════════════════════════════════════════════════════════
# build_analysis_prompt tests
# ═══════════════════════════════════════════════════════════════════════

class TestBuildAnalysisPrompt:
    """Tests for OnboardingAnalyzer.build_analysis_prompt."""

    def test_minimal_data_returns_tuple(self):
        analyzer = _make_analyzer()
        result = analyzer.build_analysis_prompt(_minimal_data())
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_minimal_data_system_prompt(self):
        analyzer = _make_analyzer()
        system_prompt, _ = analyzer.build_analysis_prompt(_minimal_data())
        assert system_prompt == 'test system prompt'

    def test_minimal_data_has_component_info(self):
        analyzer = _make_analyzer()
        _, user_prompt = analyzer.build_analysis_prompt(_minimal_data())
        assert 'odh-model-controller' in user_prompt
        assert 'operator' in user_prompt
        assert 'onboarding' in user_prompt
        assert '50%' in user_prompt

    def test_minimal_data_has_tool_instruction(self):
        analyzer = _make_analyzer()
        _, user_prompt = analyzer.build_analysis_prompt(_minimal_data())
        assert 'record_onboarding_analysis' in user_prompt

    def test_full_data_jira_tickets(self):
        analyzer = _make_analyzer()
        _, user_prompt = analyzer.build_analysis_prompt(_full_data())
        assert '## Jira Tickets' in user_prompt
        assert 'RHOAIENG-1234' in user_prompt
        assert 'In Progress' in user_prompt
        assert 'RHOAIENG-1235' in user_prompt

    def test_full_data_konflux_steps(self):
        analyzer = _make_analyzer()
        _, user_prompt = analyzer.build_analysis_prompt(_full_data())
        assert '## Konflux Steps' in user_prompt
        assert '[DONE] Create application' in user_prompt
        assert '[FAILED] Build pipeline' in user_prompt
        assert 'FIPS check failed' in user_prompt
        assert 'fix: Add FIPS-compliant base image' in user_prompt

    def test_full_data_automation_steps(self):
        analyzer = _make_analyzer()
        _, user_prompt = analyzer.build_analysis_prompt(_full_data())
        assert '## Automation Steps' in user_prompt
        assert '[DONE] Create component' in user_prompt
        assert 'label: component-created' in user_prompt
        assert 'PR: https://github.com/org/repo/pull/1' in user_prompt
        assert '[PENDING] Configure pipeline' in user_prompt

    def test_full_data_bot_error_analysis(self):
        analyzer = _make_analyzer()
        _, user_prompt = analyzer.build_analysis_prompt(_full_data())
        assert '## Bot Error History' in user_prompt
        assert 'Total errors: 7' in user_prompt
        assert 'git_conflict (3x)' in user_prompt
        assert 'auth_failure (2x)' in user_prompt
        assert 'Automation fix: rebase' in user_prompt

    def test_full_data_stuck_steps(self):
        analyzer = _make_analyzer()
        _, user_prompt = analyzer.build_analysis_prompt(_full_data())
        assert 'Stuck steps' in user_prompt
        assert 'configure-pipeline: 5 failures' in user_prompt
        assert 'create-nudge-pr: 3 failures' in user_prompt

    def test_full_data_error_timeline(self):
        analyzer = _make_analyzer()
        _, user_prompt = analyzer.build_analysis_prompt(_full_data())
        assert 'Recent errors' in user_prompt
        assert 'step=configure-pipeline' in user_prompt
        assert 'CONFLICT (content)' in user_prompt

    def test_full_data_odh_bot_errors(self):
        analyzer = _make_analyzer()
        _, user_prompt = analyzer.build_analysis_prompt(_full_data())
        assert '## ODH Bot Error History' in user_prompt
        assert 'Total errors: 2' in user_prompt
        assert 'image_not_found (2x)' in user_prompt

    def test_full_data_heuristic_analysis(self):
        analyzer = _make_analyzer()
        _, user_prompt = analyzer.build_analysis_prompt(_full_data())
        assert '## Heuristic Analysis' in user_prompt
        assert 'Blocked at: configure-pipeline' in user_prompt
        assert 'Fix (component): Resolve Dockerfile merge conflict' in user_prompt
        assert 'Fix (automation): Retry after rebase' in user_prompt

    def test_full_data_action_items(self):
        analyzer = _make_analyzer()
        _, user_prompt = analyzer.build_analysis_prompt(_full_data())
        assert '## Action Items' in user_prompt
        assert '[HIGH] Resolve merge conflict' in user_prompt
        assert '[MED] Refresh bot token' in user_prompt

    def test_full_data_nudge_prs(self):
        analyzer = _make_analyzer()
        _, user_prompt = analyzer.build_analysis_prompt(_full_data())
        assert '## Nudge PRs' in user_prompt
        assert 'golang.org/x/net' in user_prompt
        assert 'v0.23.0' in user_prompt
        assert '[merged]' in user_prompt
        assert 'k8s.io/client-go' in user_prompt
        assert '[open]' in user_prompt

    def test_empty_optional_sections_omitted(self):
        """Sections with empty lists should not appear in prompt."""
        analyzer = _make_analyzer()
        data = _minimal_data(
            jira_tickets=[],
            steps=[],
            automation_steps=[],
            bot_error_analysis={},
            nudge_prs=[],
        )
        _, user_prompt = analyzer.build_analysis_prompt(data)
        assert '## Jira Tickets' not in user_prompt
        assert '## Konflux Steps' not in user_prompt
        assert '## Automation Steps' not in user_prompt
        assert '## Bot Error History' not in user_prompt
        assert '## Nudge PRs' not in user_prompt

    def test_bot_error_analysis_no_errors_omitted(self):
        """Bot error section skipped when has_errors is False."""
        analyzer = _make_analyzer()
        data = _minimal_data(
            bot_error_analysis={'has_errors': False, 'retry_count': 0},
        )
        _, user_prompt = analyzer.build_analysis_prompt(data)
        assert '## Bot Error History' not in user_prompt

    def test_missing_keys_use_defaults(self):
        """Missing keys in onboarding_data fall back to defaults."""
        analyzer = _make_analyzer()
        result = analyzer.build_analysis_prompt({})
        _, user_prompt = result
        assert '## Component: ?' in user_prompt
        assert 'Phase: ?' in user_prompt
        assert 'Progress: 0%' in user_prompt
        assert 'Type: unknown' in user_prompt


# ═══════════════════════════════════════════════════════════════════════
# parse_analysis_response tests
# ═══════════════════════════════════════════════════════════════════════

class TestParseAnalysisResponse:
    """Tests for OnboardingAnalyzer.parse_analysis_response."""

    def test_valid_tool_call(self):
        analyzer = _make_analyzer()
        resp = _mock_llm_response(tool_calls=[_valid_tool_call()])
        result = analyzer.parse_analysis_response(resp)
        assert result['root_cause'] == 'Pipeline config blocked by merge conflict in .tekton/'
        assert result['failure_category'] == 'automation_stuck'
        assert result['confidence_score'] == 0.85
        assert result['can_auto_fix'] is True
        assert result['blocked_step'] == 'configure-pipeline'

    def test_no_tool_calls_raises(self):
        analyzer = _make_analyzer()
        resp = _mock_llm_response(tool_calls=None)
        with pytest.raises(ValueError, match='did not return tool_use'):
            analyzer.parse_analysis_response(resp)

    def test_empty_tool_calls_raises(self):
        analyzer = _make_analyzer()
        resp = _mock_llm_response(tool_calls=[])
        with pytest.raises(ValueError, match='did not return tool_use'):
            analyzer.parse_analysis_response(resp)

    def test_wrong_tool_name_raises(self):
        analyzer = _make_analyzer()
        wrong_call = {'name': 'wrong_tool', 'input': {}}
        resp = _mock_llm_response(tool_calls=[wrong_call])
        with pytest.raises(ValueError, match='did not call record_onboarding_analysis'):
            analyzer.parse_analysis_response(resp)

    def test_pydantic_validation_error_returns_fallback(self):
        """Invalid data triggers Pydantic error; fallback dict returned."""
        analyzer = _make_analyzer()
        bad_call = {
            'name': 'record_onboarding_analysis',
            'input': {
                'root_cause': 'x',  # too short (min_length=10)
                'failure_category': 'automation_stuck',
                'confidence_score': 0.5,
                'recommended_fix': 'y',  # too short
                'can_auto_fix': False,
            },
        }
        resp = _mock_llm_response(tool_calls=[bad_call])
        result = analyzer.parse_analysis_response(resp)
        # Fallback should have these keys
        assert result['failure_category'] == 'manual_intervention'
        assert result['confidence_score'] == 0.0
        assert result['can_auto_fix'] is False
        assert result['requires_human_review'] is True
        assert result['root_cause'] == 'x'
        assert result['recommended_fix'] == 'y'

    def test_pydantic_fallback_missing_fields(self):
        """Fallback handles missing input keys gracefully."""
        analyzer = _make_analyzer()
        bad_call = {
            'name': 'record_onboarding_analysis',
            'input': {
                'failure_category': 'not_a_real_enum_value',
                'confidence_score': 99,  # out of range
            },
        }
        resp = _mock_llm_response(tool_calls=[bad_call])
        result = analyzer.parse_analysis_response(resp)
        assert result['root_cause'] == 'Invalid response'
        assert result['recommended_fix'] == 'Manual review required'
        assert result['blocked_step'] == ''

    def test_picks_correct_tool_among_multiple(self):
        """When multiple tool calls exist, find the right one."""
        analyzer = _make_analyzer()
        calls = [
            {'name': 'other_tool', 'input': {}},
            _valid_tool_call(),
        ]
        resp = _mock_llm_response(tool_calls=calls)
        result = analyzer.parse_analysis_response(resp)
        assert result['failure_category'] == 'automation_stuck'

    def test_valid_response_model_dump_fields(self):
        """Successful parse returns all OnboardingAnalysisResult fields."""
        analyzer = _make_analyzer()
        resp = _mock_llm_response(tool_calls=[_valid_tool_call()])
        result = analyzer.parse_analysis_response(resp)
        expected_keys = {
            'root_cause', 'failure_category', 'confidence_score',
            'recommended_fix', 'blocked_step', 'can_auto_fix',
            'requires_human_review', 'evidence_references',
            'source_transparency',
        }
        assert expected_keys.issubset(set(result.keys()))


# ═══════════════════════════════════════════════════════════════════════
# analyze (full flow) tests
# ═══════════════════════════════════════════════════════════════════════

class TestAnalyze:
    """Tests for OnboardingAnalyzer.analyze end-to-end."""

    def test_happy_path(self):
        analyzer = _make_analyzer()
        resp = _mock_llm_response(
            tool_calls=[_valid_tool_call()],
            input_tokens=200,
            output_tokens=100,
        )
        analyzer.llm.create_message.return_value = resp

        result = analyzer.analyze(_minimal_data())

        # Core analysis fields
        assert result['failure_category'] == 'automation_stuck'
        assert result['root_cause'] == 'Pipeline config blocked by merge conflict in .tekton/'

        # Token/cost metadata
        assert result['tokens_used'] == 300
        expected_cost = (200 * 0.000003) + (100 * 0.000015)
        assert abs(result['cost_usd'] - expected_cost) < 1e-9
        assert result['model_used'] == 'claude-test-model'
        assert result['duration'] >= 0

    def test_llm_called_with_correct_args(self):
        from analyzers.onboarding_analyzer import ONBOARDING_ANALYSIS_TOOL

        analyzer = _make_analyzer()
        resp = _mock_llm_response(
            tool_calls=[_valid_tool_call()],
            input_tokens=10,
            output_tokens=10,
        )
        analyzer.llm.create_message.return_value = resp

        analyzer.analyze(_minimal_data())

        analyzer.llm.create_message.assert_called_once()
        call_kwargs = analyzer.llm.create_message.call_args
        assert call_kwargs.kwargs['system'] == 'test system prompt'
        assert call_kwargs.kwargs['tools'] == [ONBOARDING_ANALYSIS_TOOL]
        assert 'odh-model-controller' in call_kwargs.kwargs['user_content']

    def test_cost_calculation_zero_tokens(self):
        analyzer = _make_analyzer()
        resp = _mock_llm_response(
            tool_calls=[_valid_tool_call()],
            input_tokens=0,
            output_tokens=0,
        )
        analyzer.llm.create_message.return_value = resp

        result = analyzer.analyze(_minimal_data())
        assert result['tokens_used'] == 0
        assert result['cost_usd'] == 0.0


# ═══════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases: None values, missing sub-keys, boundary conditions."""

    def test_none_values_in_data(self):
        """None for optional fields should not crash."""
        analyzer = _make_analyzer()
        data = _minimal_data(
            jira_tickets=None,
            steps=None,
            automation_steps=None,
            nudge_prs=None,
        )
        # None is falsy, so sections should be skipped
        _, user_prompt = analyzer.build_analysis_prompt(data)
        assert '## Component: odh-model-controller' in user_prompt
        assert '## Jira Tickets' not in user_prompt

    def test_ticket_missing_subkeys(self):
        """Jira ticket dicts with missing keys use .get defaults."""
        analyzer = _make_analyzer()
        data = _minimal_data(jira_tickets=[{}])
        _, user_prompt = analyzer.build_analysis_prompt(data)
        assert '## Jira Tickets' in user_prompt

    def test_step_with_no_detail_no_fix(self):
        """Steps without detail or fix should omit those suffixes."""
        analyzer = _make_analyzer()
        data = _minimal_data(steps=[{'step': 'init', 'status': 'done'}])
        _, user_prompt = analyzer.build_analysis_prompt(data)
        assert '[DONE]' in user_prompt
        # 'label' falls back to 'step' when label key missing
        assert 'init' in user_prompt

    def test_analysis_without_blocked_at(self):
        """Analysis section with no blocked_at skips blocked details."""
        analyzer = _make_analyzer()
        data = _minimal_data(analysis={'status': 'progressing'})
        _, user_prompt = analyzer.build_analysis_prompt(data)
        assert '## Heuristic Analysis' in user_prompt
        assert 'Status: progressing' in user_prompt
        assert 'Blocked at:' not in user_prompt

    def test_action_items_missing_priority(self):
        """Action items without priority key default to MED."""
        analyzer = _make_analyzer()
        data = _minimal_data(
            report={'action_items': [{'action': 'Do something'}]},
        )
        _, user_prompt = analyzer.build_analysis_prompt(data)
        assert '[MED] Do something' in user_prompt

    def test_tool_schema_structure(self):
        """ONBOARDING_ANALYSIS_TOOL has expected schema shape."""
        from analyzers.onboarding_analyzer import ONBOARDING_ANALYSIS_TOOL
        assert ONBOARDING_ANALYSIS_TOOL['name'] == 'record_onboarding_analysis'
        props = ONBOARDING_ANALYSIS_TOOL['input_schema']['properties']
        assert 'root_cause' in props
        assert 'failure_category' in props
        assert 'confidence_score' in props
        required = ONBOARDING_ANALYSIS_TOOL['input_schema']['required']
        assert 'root_cause' in required
        assert 'can_auto_fix' in required
