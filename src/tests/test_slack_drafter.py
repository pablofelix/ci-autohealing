"""Tests for Slack draft message templates."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.slack_drafter import format_draft


def test_resolved_template_includes_component_and_url():
    result = format_draft(
        message_type='resolved',
        component='odh-automl-v3-5',
        contact='Dorota',
        build_url='https://konflux-ui.example.com/pipelinerun/abc/logs',
    )
    assert 'odh-automl-v3-5' in result
    assert 'Dorota' in result
    assert 'https://konflux-ui.example.com' in result


def test_new_failure_template_includes_root_cause():
    result = format_draft(
        message_type='new_failure',
        component='odh-feature-server-v3-5',
        contact='Puneet',
        build_url='https://example.com/logs',
        root_cause='PEP 440 local version suffix',
        failed_step='prefetch-dependencies',
    )
    assert 'PEP 440' in result
    assert 'prefetch-dependencies' in result
    assert 'Puneet' in result


def test_followup_template_includes_days_to_freeze():
    result = format_draft(
        message_type='followup',
        component='odh-mcp-lifecycle-operator-v3-5',
        contact='ksuszyns',
        root_cause='vendor inconsistency',
        days_to_freeze=4,
    )
    assert '4' in result


def test_escalation_template_mentions_days_failing():
    result = format_draft(
        message_type='escalation',
        component='odh-codeserver-v3-5',
        contact='team',
        root_cause='hermeto path mismatch',
        days_failing=5,
    )
    assert '5' in result
    assert 'odh-codeserver-v3-5' in result


def test_unknown_type_returns_generic_message():
    result = format_draft(
        message_type='unknown_type',
        component='some-comp',
        contact='someone',
    )
    assert 'some-comp' in result
