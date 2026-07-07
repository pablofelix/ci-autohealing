"""Tests for ConformaEvaluator incremental evaluation logic."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from collectors.conforma_evaluator import (
    ConformaEvaluator,
    _extract_version,
    _group_by_component,
    _strip_arch_suffix,
)
from config import CollectorConfig, DatabaseConfig, KubernetesConfig


@pytest.fixture
def config():
    return CollectorConfig(
        db=DatabaseConfig(
            host="localhost", port=5432, user="test",
            password="test", database="testdb"
        ),
        k8s=KubernetesConfig(
            namespace="rhoai-tenant", application_name="rhoai-v3-5-ea-2",
            kubearchive_api_url="https://kubearchive.example.com"
        )
    )


@pytest.fixture
def evaluator(config):
    return ConformaEvaluator(
        config,
        db=MagicMock(),
        conforma_repo=MagicMock(),
        konflux=MagicMock(),
    )


# --- _extract_version ---

class TestExtractVersion:
    def test_ea_version(self):
        assert _extract_version('rhoai-v3-5-ea-2') == 'v3-5-ea-2'

    def test_simple_version(self):
        assert _extract_version('rhoai-v3-5') == 'v3-5'

    def test_no_version(self):
        assert _extract_version('some-app') == 'some-app'

    def test_version_at_start(self):
        assert _extract_version('v4-0-rc1') == 'v4-0-rc1'


# --- _strip_arch_suffix ---

class TestStripArchSuffix:
    def test_with_arch(self):
        assert _strip_arch_suffix('comp-sha256:abc123-amd64') == 'comp'

    def test_without_arch(self):
        assert _strip_arch_suffix('comp-name') == 'comp-name'


# --- _group_by_component ---

class TestGroupByComponent:
    def test_aggregates_violations(self):
        ec_comps = [
            {
                'name': 'comp-a',
                'containerImage': 'quay.io/img:v1',
                'violations': [
                    {'metadata': {'code': 'rule.one'}, 'msg': 'fail 1'},
                ],
                'warnings': [],
                'successes': [{'metadata': {'code': 'ok'}}],
            },
            {
                'name': 'comp-a-sha256:abc-amd64',
                'containerImage': 'quay.io/img:v1@sha256:abc',
                'violations': [
                    {'metadata': {'code': 'rule.two'}, 'msg': 'fail 2'},
                ],
                'warnings': [{'metadata': {'code': 'warn.one'}}],
                'successes': [],
            },
        ]
        grouped = _group_by_component(ec_comps)
        assert 'comp-a' in grouped
        assert grouped['comp-a']['violations_count'] == 2
        assert grouped['comp-a']['warnings_count'] == 1
        assert grouped['comp-a']['successes_count'] == 1
        assert 'rule.one' in grouped['comp-a']['violation_summary']
        assert 'rule.two' in grouped['comp-a']['violation_summary']

    def test_empty_input(self):
        assert _group_by_component([]) == {}

    def test_dedup_violation_codes(self):
        ec_comps = [
            {
                'name': 'comp-b',
                'containerImage': 'img:v1',
                'violations': [
                    {'metadata': {'code': 'dup.rule'}, 'msg': 'msg1'},
                    {'metadata': {'code': 'dup.rule'}, 'msg': 'msg2'},
                ],
                'warnings': [],
                'successes': [],
            },
        ]
        grouped = _group_by_component(ec_comps)
        assert grouped['comp-b']['violations_count'] == 2
        assert grouped['comp-b']['violation_summary'].count('dup.rule') == 1


# --- categorize_component ---

class TestCategorizeComponent:
    def test_fbc(self, evaluator):
        assert evaluator.categorize_component('odh-fbc-fragment-v3-5') == 'fbc'

    def test_chart(self, evaluator):
        assert evaluator.categorize_component('rhoai-chart-v3-5') == 'chart'

    def test_chart_suffix(self, evaluator):
        assert evaluator.categorize_component('rhoai-some-chart') == 'chart'

    def test_registry(self, evaluator):
        assert evaluator.categorize_component('odh-dashboard-v3-5') == 'registry'


# --- is_stale ---

class TestIsStale:
    def test_no_timestamp(self, evaluator):
        evaluator.conforma_repo.get_latest_future_timestamp.return_value = None
        assert evaluator.is_stale() is True

    def test_recent(self, evaluator):
        evaluator.conforma_repo.get_latest_future_timestamp.return_value = (
            datetime.now(UTC) - timedelta(hours=1)
        )
        assert evaluator.is_stale() is False

    def test_old(self, evaluator):
        evaluator.conforma_repo.get_latest_future_timestamp.return_value = (
            datetime.now(UTC) - timedelta(hours=10)
        )
        assert evaluator.is_stale() is True

    def test_custom_max_age(self, evaluator):
        evaluator.conforma_repo.get_latest_future_timestamp.return_value = (
            datetime.now(UTC) - timedelta(hours=3)
        )
        assert evaluator.is_stale(max_age_hours=2) is True
        assert evaluator.is_stale(max_age_hours=4) is False

    def test_uses_app_name_from_config(self, evaluator):
        evaluator.conforma_repo.get_latest_future_timestamp.return_value = None
        evaluator.is_stale()
        evaluator.conforma_repo.get_latest_future_timestamp.assert_called_with(
            'rhoai-v3-5-ea-2'
        )

    def test_explicit_app_name(self, evaluator):
        evaluator.conforma_repo.get_latest_future_timestamp.return_value = None
        evaluator.is_stale(app_name='rhoai-v3-5')
        evaluator.conforma_repo.get_latest_future_timestamp.assert_called_with(
            'rhoai-v3-5'
        )


# --- _filter_changed ---

class TestFilterChanged:
    def test_no_previous_evaluations(self, evaluator):
        evaluator.conforma_repo.get_evaluated_images.return_value = {}
        components = [
            {'name': 'comp-a', 'containerImage': 'img:v1'},
            {'name': 'comp-b', 'containerImage': 'img:v2'},
        ]
        result = evaluator._filter_changed('app', components)
        assert result == components

    def test_all_unchanged(self, evaluator):
        evaluator.conforma_repo.get_evaluated_images.return_value = {
            'comp-a': 'img:v1',
            'comp-b': 'img:v2',
        }
        components = [
            {'name': 'comp-a', 'containerImage': 'img:v1'},
            {'name': 'comp-b', 'containerImage': 'img:v2'},
        ]
        result = evaluator._filter_changed('app', components)
        assert result == []

    def test_one_changed(self, evaluator):
        evaluator.conforma_repo.get_evaluated_images.return_value = {
            'comp-a': 'img:v1',
            'comp-b': 'img:v2',
        }
        components = [
            {'name': 'comp-a', 'containerImage': 'img:v1'},
            {'name': 'comp-b', 'containerImage': 'img:v3'},
        ]
        result = evaluator._filter_changed('app', components)
        assert len(result) == 1
        assert result[0]['name'] == 'comp-b'

    def test_new_component(self, evaluator):
        evaluator.conforma_repo.get_evaluated_images.return_value = {
            'comp-a': 'img:v1',
        }
        components = [
            {'name': 'comp-a', 'containerImage': 'img:v1'},
            {'name': 'comp-new', 'containerImage': 'img:new'},
        ]
        result = evaluator._filter_changed('app', components)
        assert len(result) == 1
        assert result[0]['name'] == 'comp-new'


# --- run() incremental ---

class TestRunIncremental:
    def test_incremental_skips_unchanged(self, evaluator):
        evaluator.konflux.get_ec_policies.return_value = [
            {'metadata': {'name': 'registry-rhoai-prod-v3-5-ea-2-future'}},
        ]
        evaluator.konflux.get_snapshots.return_value = [{
            'metadata': {'name': 'snap-1'},
            'spec': {'components': [
                {'name': 'comp-a', 'containerImage': 'img:v1'},
                {'name': 'comp-b', 'containerImage': 'img:v2'},
            ]},
        }]
        evaluator.conforma_repo.get_evaluated_images.return_value = {
            'comp-a': 'img:v1',
            'comp-b': 'img:v2',
        }

        result = evaluator.run(incremental=True)
        assert result['evaluated'] == 0
        assert result['skipped'] == 2
        assert result['incremental'] is True

    def test_incremental_evaluates_changed(self, evaluator):
        evaluator.konflux.get_ec_policies.return_value = [
            {'metadata': {'name': 'registry-rhoai-prod-v3-5-ea-2-future'}},
        ]
        evaluator.konflux.get_snapshots.return_value = [{
            'metadata': {'name': 'snap-1'},
            'spec': {'components': [
                {'name': 'comp-a', 'containerImage': 'img:v1'},
                {'name': 'comp-b', 'containerImage': 'img:v3'},
            ]},
        }]
        evaluator.conforma_repo.get_evaluated_images.return_value = {
            'comp-a': 'img:v1',
            'comp-b': 'img:v2',
        }

        ec_output = {
            'components': [{
                'name': 'comp-b',
                'containerImage': 'img:v3',
                'violations': [],
                'warnings': [],
                'successes': [{'metadata': {'code': 'ok'}}],
            }]
        }
        with patch('shutil.which', return_value='/usr/bin/ec'), patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout='{}', stderr='', returncode=0
            )
            import json
            mock_run.return_value.stdout = json.dumps(ec_output)
            result = evaluator.run(incremental=True)

        assert result['evaluated'] == 1
        assert result['violations'] == 0

    def test_non_incremental_evaluates_all(self, evaluator):
        evaluator.konflux.get_ec_policies.return_value = [
            {'metadata': {'name': 'registry-rhoai-prod-v3-5-ea-2-future'}},
        ]
        evaluator.konflux.get_snapshots.return_value = [{
            'metadata': {'name': 'snap-1'},
            'spec': {'components': [
                {'name': 'comp-a', 'containerImage': 'img:v1'},
                {'name': 'comp-b', 'containerImage': 'img:v2'},
            ]},
        }]

        ec_output = {
            'components': [
                {'name': 'comp-a', 'containerImage': 'img:v1',
                 'violations': [], 'warnings': [], 'successes': []},
                {'name': 'comp-b', 'containerImage': 'img:v2',
                 'violations': [], 'warnings': [], 'successes': []},
            ]
        }
        with patch('shutil.which', return_value='/usr/bin/ec'), patch('subprocess.run') as mock_run:
            import json
            mock_run.return_value = MagicMock(
                stdout=json.dumps(ec_output), stderr='', returncode=0
            )
            result = evaluator.run(incremental=False)

        assert result['evaluated'] == 2

    def test_incremental_ignored_with_component_filter(self, evaluator):
        evaluator.konflux.get_ec_policies.return_value = [
            {'metadata': {'name': 'registry-rhoai-prod-v3-5-ea-2-future'}},
        ]
        evaluator.konflux.get_snapshots.return_value = [{
            'metadata': {'name': 'snap-1'},
            'spec': {'components': [
                {'name': 'comp-a', 'containerImage': 'img:v1'},
            ]},
        }]

        ec_output = {
            'components': [{
                'name': 'comp-a', 'containerImage': 'img:v1',
                'violations': [], 'warnings': [], 'successes': [],
            }]
        }
        with patch('shutil.which', return_value='/usr/bin/ec'), patch('subprocess.run') as mock_run:
            import json
            mock_run.return_value = MagicMock(
                stdout=json.dumps(ec_output), stderr='', returncode=0
            )
            result = evaluator.run(
                incremental=True, component_filter='comp-a'
            )

        assert result['evaluated'] == 1
        evaluator.conforma_repo.get_evaluated_images.assert_not_called()

    def test_no_policies_returns_early(self, evaluator):
        evaluator.konflux.get_ec_policies.return_value = []
        result = evaluator.run()
        assert result['evaluated'] == 0

    def test_no_snapshot_returns_early(self, evaluator):
        evaluator.konflux.get_ec_policies.return_value = [
            {'metadata': {'name': 'registry-rhoai-prod-v3-5-ea-2-future'}},
        ]
        evaluator.konflux.get_snapshots.return_value = []
        result = evaluator.run()
        assert result['evaluated'] == 0


# --- resolve_policies ---

class TestResolvePolicies:
    def test_future_policies(self, evaluator):
        evaluator.konflux.get_ec_policies.return_value = [
            {'metadata': {'name': 'registry-rhoai-prod-v3-5-ea-2-future'}},
            {'metadata': {'name': 'fbc-rhoai-prod-v3-5-ea-2-future'}},
            {'metadata': {'name': 'registry-rhoai-chart-prod-v3-5-ea-2-future'}},
        ]
        policies = evaluator.resolve_policies('rhoai-v3-5-ea-2', tier='future')
        assert len(policies) == 3
        types = [p[0] for p in policies]
        assert 'registry' in types
        assert 'fbc' in types
        assert 'chart' in types

    def test_stage_policies(self, evaluator):
        evaluator.konflux.get_ec_policies.return_value = [
            {'metadata': {'name': 'registry-rhoai-stage'}},
            {'metadata': {'name': 'fbc-rhoai-stage'}},
        ]
        policies = evaluator.resolve_policies('rhoai-v3-5-ea-2', tier='stage')
        assert len(policies) == 2

    def test_missing_policy(self, evaluator):
        evaluator.konflux.get_ec_policies.return_value = [
            {'metadata': {'name': 'registry-rhoai-prod-v3-5-ea-2-future'}},
        ]
        policies = evaluator.resolve_policies('rhoai-v3-5-ea-2', tier='future')
        assert len(policies) == 1
        assert policies[0][0] == 'registry'
