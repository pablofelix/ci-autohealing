"""Tests for ComponentHealthSource enrichment source."""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

sys.modules.setdefault('logger', MagicMock(setup_logger=MagicMock(return_value=MagicMock())))
sys.modules.setdefault('repositories.connection', MagicMock())
sys.modules.setdefault('psycopg2', MagicMock())
sys.modules.setdefault('psycopg2.pool', MagicMock())

from enrichment.sources.component_health import ComponentHealthSource


def _make_config(app_name='rhoai-v3-5', namespace='rhoai-v3-5-tenant'):
    config = MagicMock()
    config.k8s.application_name = app_name
    config.k8s.namespace = namespace
    return config


class TestComponentHealthSourceProperties:

    def test_source_name(self):
        src = ComponentHealthSource(_make_config())
        assert src.source_name() == 'component_health'

    def test_requires_external_api(self):
        src = ComponentHealthSource(_make_config())
        assert src.requires_external_api is True

    def test_timeout_seconds(self):
        src = ComponentHealthSource(_make_config())
        assert src.timeout_seconds == 15


def _make_pipelinerun(succeeded=True, sha='abc123', transition_time='2026-07-10T10:00:00Z'):
    """Build a mock PipelineRun dict matching Tekton Results format."""
    status_val = 'True' if succeeded else 'False'
    reason = 'Succeeded' if succeeded else 'Failed'
    return {
        'metadata': {
            'name': 'build-{}'.format(sha[:8]),
            'annotations': {
                'build.appstudio.redhat.com/commit_sha': sha,
            },
        },
        'status': {
            'conditions': [{
                'type': 'Succeeded',
                'status': status_val,
                'reason': reason,
                'lastTransitionTime': transition_time,
            }],
        },
    }


class TestComponentHealthFetch:

    def test_component_with_successes(self):
        mock_tr = MagicMock()
        mock_tr.query_component_build_history.return_value = [
            _make_pipelinerun(succeeded=False, sha='fail1'),
            _make_pipelinerun(succeeded=True, sha='success1', transition_time='2026-07-08T10:00:00Z'),
            _make_pipelinerun(succeeded=True, sha='success2', transition_time='2026-07-05T10:00:00Z'),
        ]

        src = ComponentHealthSource(_make_config())
        with patch.dict(sys.modules, {'clients.tekton_results': MagicMock(
            TektonResultsClient=MagicMock(return_value=mock_tr)
        )}):
            result = src.fetch({
                'component_name': 'my-component',
                'application': 'rhoai-v3-5',
            })

        assert result is not None
        health = result['component_health']
        assert health['has_ever_succeeded'] is True
        assert health['total_builds'] == 3
        assert health['successful_builds'] == 2
        assert health['failed_builds'] == 1
        assert health['consecutive_failures'] == 1
        assert health['last_success_date'] == '2026-07-08T10:00:00Z'
        assert health['last_success_sha'] == 'success1'

    def test_component_never_succeeded(self):
        mock_tr = MagicMock()
        mock_tr.query_component_build_history.return_value = [
            _make_pipelinerun(succeeded=False, sha='fail1'),
            _make_pipelinerun(succeeded=False, sha='fail2'),
            _make_pipelinerun(succeeded=False, sha='fail3'),
        ]

        src = ComponentHealthSource(_make_config())
        with patch.dict(sys.modules, {'clients.tekton_results': MagicMock(
            TektonResultsClient=MagicMock(return_value=mock_tr)
        )}):
            result = src.fetch({
                'component_name': 'odh-trustyai-service-v3-5',
                'application': 'rhoai-v3-5',
            })

        assert result is not None
        health = result['component_health']
        assert health['has_ever_succeeded'] is False
        assert health['total_builds'] == 3
        assert health['successful_builds'] == 0
        assert health['failed_builds'] == 3
        assert health['consecutive_failures'] == 3
        assert health['last_success_date'] is None
        assert health['last_success_sha'] is None

    def test_empty_build_history(self):
        mock_tr = MagicMock()
        mock_tr.query_component_build_history.return_value = []

        src = ComponentHealthSource(_make_config())
        with patch.dict(sys.modules, {'clients.tekton_results': MagicMock(
            TektonResultsClient=MagicMock(return_value=mock_tr)
        )}):
            result = src.fetch({
                'component_name': 'brand-new-component',
                'application': 'rhoai-v3-5',
            })

        assert result is not None
        health = result['component_health']
        assert health['has_ever_succeeded'] is False
        assert health['total_builds'] == 0

    def test_tekton_results_unavailable(self):
        src = ComponentHealthSource(_make_config())
        with patch.dict(sys.modules, {'clients.tekton_results': MagicMock(
            TektonResultsClient=MagicMock(side_effect=Exception("connection refused"))
        )}):
            result = src.fetch({
                'component_name': 'my-component',
                'application': 'rhoai-v3-5',
            })

        assert result is None

    def test_missing_component_name(self):
        src = ComponentHealthSource(_make_config())
        result = src.fetch({'id': 1})
        assert result is None

    def test_uses_page_size_20(self):
        """Fetch enough builds to distinguish structural from transient."""
        mock_tr = MagicMock()
        mock_tr.query_component_build_history.return_value = []

        src = ComponentHealthSource(_make_config())
        with patch.dict(sys.modules, {'clients.tekton_results': MagicMock(
            TektonResultsClient=MagicMock(return_value=mock_tr)
        )}):
            src.fetch({
                'component_name': 'comp',
                'application': 'rhoai-v3-5',
            })

        mock_tr.query_component_build_history.assert_called_once_with(
            'rhoai-v3-5', 'comp', page_size=20
        )

    def test_consecutive_failures_resets_on_success(self):
        """Consecutive failures counts from newest until first success."""
        mock_tr = MagicMock()
        mock_tr.query_component_build_history.return_value = [
            _make_pipelinerun(succeeded=False, sha='f1'),
            _make_pipelinerun(succeeded=False, sha='f2'),
            _make_pipelinerun(succeeded=False, sha='f3'),
            _make_pipelinerun(succeeded=True, sha='s1'),
            _make_pipelinerun(succeeded=False, sha='f4'),
        ]

        src = ComponentHealthSource(_make_config())
        with patch.dict(sys.modules, {'clients.tekton_results': MagicMock(
            TektonResultsClient=MagicMock(return_value=mock_tr)
        )}):
            result = src.fetch({'component_name': 'comp', 'application': 'app'})

        assert result['component_health']['consecutive_failures'] == 3
        assert result['component_health']['has_ever_succeeded'] is True

    def test_pipelinerun_without_conditions(self):
        """PipelineRuns missing conditions are skipped."""
        mock_tr = MagicMock()
        mock_tr.query_component_build_history.return_value = [
            {'metadata': {'name': 'broken'}, 'status': {}},
            _make_pipelinerun(succeeded=True, sha='s1'),
        ]

        src = ComponentHealthSource(_make_config())
        with patch.dict(sys.modules, {'clients.tekton_results': MagicMock(
            TektonResultsClient=MagicMock(return_value=mock_tr)
        )}):
            result = src.fetch({'component_name': 'comp', 'application': 'app'})

        health = result['component_health']
        assert health['total_builds'] == 1
        assert health['has_ever_succeeded'] is True
