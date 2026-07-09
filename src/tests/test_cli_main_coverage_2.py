"""Comprehensive tests for CLI commands: ai, config, skills, onboard, db, rebuild.

Tests the second half of the CLI command tree in src/cli/main.py.
All external calls (API, DB, LLM, K8s) are mocked.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cli.main import cli  # noqa: E402


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _NoopSpinner:
    """A no-op replacement for cli.progress.Spinner context manager."""
    def __init__(self, *args, **kwargs):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


NOOP_SPINNER = _NoopSpinner


# ===================================================================
#  AI GROUP
# ===================================================================

class TestAiGroup:
    """Tests for `ic ai` (no subcommand)."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.mode.has_api', return_value=True)
    @patch('cli.data.get_ai_stats', return_value={
        'build_failures': {'pending': 0, 'analyzed': 5, 'autofixable': 2},
        'conforma_violations': {'pending': 1, 'analyzed': 3},
        'total_cost_30d': 0.42,
    })
    def test_ai_no_subcommand_shows_status(self, mock_stats, mock_api, mock_cluster, runner):
        result = runner.invoke(cli, ['ai'])
        assert result.exit_code == 0
        assert 'AI Analysis Status' in result.output


class TestAiAnalyze:
    """Tests for `ic ai analyze component <name>`."""

    @patch('cli.mode.is_cluster', return_value=True)
    @patch('cli.data.require_data', return_value=True)
    @patch('cli.data.get_failure_details', return_value=None)
    @patch('cli.data.get_alerts', return_value={'build_failures': []})
    def test_analyze_component_not_found(self, mock_alerts, mock_details,
                                         mock_req, mock_cluster, runner):
        result = runner.invoke(cli, ['ai', 'analyze', 'component', 'missing-comp'])
        assert result.exit_code == 0
        assert 'No failure found' in result.output

    @patch('cli.mode.is_cluster', return_value=True)
    @patch('cli.data.require_data', return_value=True)
    @patch('cli.data.get_failure_details', return_value={
        'component': 'comp1',
        'build_logs': 'error: segfault',
        'error_message': 'build failed',
        'failed_task': 'build-step',
    })
    @patch('cli.data.submit_analysis', return_value={'action': 'stored'})
    @patch('analyzers.build_failure_analyzer.BuildFailureAnalyzer')
    @patch('clients.llm_provider.create_llm_provider')
    @patch('config.CollectorConfig')
    @patch('cli.progress.Spinner', NOOP_SPINNER)
    def test_analyze_component_success(self, mock_config_cls, mock_llm_fn,
                                        mock_analyzer_cls, mock_submit,
                                        mock_details, mock_req,
                                        mock_cluster, runner):
        mock_config = MagicMock()
        mock_config.llm = MagicMock(model='test-model')
        mock_config_cls.from_env.return_value = mock_config

        mock_llm = MagicMock()
        mock_llm.model_name.return_value = 'test-model'
        mock_response = MagicMock()
        mock_response.input_tokens = 100
        mock_response.output_tokens = 50
        mock_llm.create_message.return_value = mock_response
        mock_llm_fn.return_value = mock_llm

        mock_analyzer = MagicMock()
        mock_analyzer.build_analysis_prompt.return_value = ('sys', 'user')
        mock_analyzer.parse_analysis_response.return_value = {
            'root_cause': 'segfault in build',
            'failure_category': 'build_error',
            'confidence_score': 0.85,
            'recommended_fix': 'Fix memory allocation',
            'can_auto_fix': False,
        }
        mock_analyzer_cls.return_value = mock_analyzer

        result = runner.invoke(cli, ['ai', 'analyze', 'component', 'comp1'])
        assert result.exit_code == 0
        assert 'Analyzing' in result.output
        assert 'Analysis complete' in result.output
        assert 'segfault in build' in result.output

    @patch('cli.mode.is_cluster', return_value=True)
    @patch('cli.data.require_data', return_value=True)
    @patch('cli.data.get_failure_details', return_value={
        'component': 'comp1',
        'build_logs': '',
        'error_message': '',
    })
    def test_analyze_no_logs(self, mock_details, mock_req, mock_cluster, runner):
        result = runner.invoke(cli, ['ai', 'analyze', 'component', 'comp1'])
        assert result.exit_code == 0
        assert 'No logs or error info' in result.output

    @patch('cli.mode.is_cluster', return_value=True)
    @patch('cli.data.require_data', return_value=False)
    def test_analyze_no_data_available(self, mock_req, mock_cluster, runner):
        result = runner.invoke(cli, ['ai', 'analyze', 'component', 'comp1'])
        assert result.exit_code == 0

    @patch('cli.mode.is_cluster', return_value=True)
    @patch('cli.data.require_data', return_value=True)
    @patch('cli.data.get_failure_details', return_value={
        'component': 'comp1',
        'build_logs': 'error log',
        'error_message': 'fail',
    })
    @patch('config.CollectorConfig')
    def test_analyze_no_llm_configured(self, mock_config_cls, mock_details,
                                        mock_req, mock_cluster, runner):
        mock_config = MagicMock()
        mock_config.llm = None
        mock_config_cls.from_env.return_value = mock_config

        result = runner.invoke(cli, ['ai', 'analyze', 'component', 'comp1'])
        assert result.exit_code == 0
        assert 'No LLM provider configured' in result.output


class TestAiBatch:
    """Tests for `ic ai batch`."""

    @patch('cli.mode.is_cluster', return_value=True)
    @patch('cli.data.require_data', return_value=True)
    @patch('cli.data.get_alerts', return_value={
        'build_failures': [
            {'component': 'comp1', 'has_analysis': True},
            {'component': 'comp2', 'has_analysis': True},
        ],
    })
    def test_batch_no_pending(self, mock_alerts, mock_req, mock_cluster, runner):
        result = runner.invoke(cli, ['ai', 'batch'])
        assert result.exit_code == 0
        assert 'No pending failures' in result.output

    @patch('cli.mode.is_cluster', return_value=True)
    @patch('cli.data.require_data', return_value=False)
    def test_batch_no_data(self, mock_req, mock_cluster, runner):
        result = runner.invoke(cli, ['ai', 'batch'])
        assert result.exit_code == 0

    @patch('cli.mode.is_cluster', return_value=True)
    @patch('cli.data.require_data', return_value=True)
    @patch('cli.data.get_alerts', return_value={
        'build_failures': [
            {'component': 'comp1', 'has_analysis': False},
        ],
    })
    @patch('config.CollectorConfig')
    def test_batch_no_llm(self, mock_config_cls, mock_alerts, mock_req,
                          mock_cluster, runner):
        mock_config = MagicMock()
        mock_config.llm = None
        mock_config_cls.from_env.return_value = mock_config

        result = runner.invoke(cli, ['ai', 'batch'])
        assert result.exit_code == 0
        assert 'No LLM provider configured' in result.output


class TestAiStatus:
    """Tests for `ic ai status`."""

    @patch('cli.mode.has_api', return_value=True)
    @patch('cli.data.get_ai_stats', return_value={
        'build_failures': {'pending': 2, 'analyzed': 10, 'autofixable': 3},
        'conforma_violations': {'pending': 1, 'analyzed': 5},
        'total_cost_30d': 1.23,
    })
    def test_status_cluster_mode(self, mock_stats, mock_api, runner):
        result = runner.invoke(cli, ['ai', 'status'])
        assert result.exit_code == 0
        assert 'AI Analysis Status' in result.output
        assert 'Pending' in result.output
        assert 'Analyzed' in result.output
        assert '$1.23' in result.output

    @patch('cli.mode.has_api', return_value=False)
    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_status_local_mode(self, mock_get_repo, mock_req_db, mock_api, runner):
        mock_repo = MagicMock()
        mock_repo.get_extended_status.return_value = {
            'build': {
                'pending': 1, 'no_logs': 0, 'skipped': 0,
                'analyzed': 5, 'low_confidence': 1, 'auto_fixable': 2,
            },
            'conforma': {
                'pending': 0, 'skipped': 0, 'analyzed': 3,
                'low_confidence': 0, 'auto_fixable': 1,
            },
            'total_cost': 0.50,
        }
        mock_repo.get_recent_analyses.return_value = []
        mock_get_repo.return_value = mock_repo

        result = runner.invoke(cli, ['ai', 'status'])
        assert result.exit_code == 0
        assert 'AI Analysis Status' in result.output


class TestAiAnalyzeConfig:
    """Tests for `ic ai analyze-config`."""

    @patch('cli.db.require_db', return_value=False)
    def test_analyze_config_no_db(self, mock_db, runner):
        result = runner.invoke(cli, ['ai', 'analyze-config'])
        assert result.exit_code == 0

    @patch('cli.db.require_db', return_value=True)
    @patch('config.CollectorConfig')
    def test_analyze_config_no_llm(self, mock_config_cls, mock_db, runner):
        mock_config = MagicMock()
        mock_config.llm = None
        mock_config_cls.from_env.return_value = mock_config

        result = runner.invoke(cli, ['ai', 'analyze-config'])
        assert result.exit_code == 0
        assert 'No LLM provider configured' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('config.CollectorConfig')
    @patch('analyzers.config_analyzer.ConfigAnalyzer')
    def test_analyze_config_conforma_success(self, mock_analyzer_cls,
                                              mock_config_cls, mock_db, runner):
        mock_config = MagicMock()
        mock_config.llm = MagicMock(model='test-model')
        mock_config_cls.from_env.return_value = mock_config

        mock_analyzer = MagicMock()
        mock_analyzer.run.return_value = {
            'analyzed': True,
            'analysis': {
                'overall_severity': 'warning',
                'confidence_score': 0.8,
                'summary': 'Some config issues',
                'findings': [
                    {
                        'severity': 'warning',
                        'title': 'Test finding',
                        'category': 'test',
                        'fix_action': 'fix it',
                        'description': 'description',
                    },
                ],
                'auto_rebuild_candidates': ['comp1'],
                'release_blockers': [],
            },
            'cost_usd': 0.01,
            'model': 'test-model',
        }
        mock_analyzer_cls.return_value = mock_analyzer

        result = runner.invoke(cli, ['ai', 'analyze-config'])
        assert result.exit_code == 0
        assert 'Configuration' in result.output
        assert 'WARNING' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('config.CollectorConfig')
    @patch('analyzers.build_config_analyzer.BuildConfigAnalyzer')
    def test_analyze_config_build_type(self, mock_analyzer_cls,
                                        mock_config_cls, mock_db, runner):
        mock_config = MagicMock()
        mock_config.llm = MagicMock(model='test-model')
        mock_config_cls.from_env.return_value = mock_config

        mock_analyzer = MagicMock()
        mock_analyzer.run.return_value = {
            'analyzed': True,
            'analysis': {
                'overall_severity': 'info',
                'confidence_score': 0.9,
                'summary': 'All good',
                'findings': [],
                'auto_rebuild_candidates': [],
                'release_blockers': [],
            },
            'cost_usd': 0.01,
            'model': 'test-model',
        }
        mock_analyzer_cls.return_value = mock_analyzer

        result = runner.invoke(cli, ['ai', 'analyze-config', '--type', 'build'])
        assert result.exit_code == 0
        assert 'Build Pipeline' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('config.CollectorConfig')
    @patch('analyzers.config_analyzer.ConfigAnalyzer')
    def test_analyze_config_not_analyzed(self, mock_analyzer_cls,
                                          mock_config_cls, mock_db, runner):
        mock_config = MagicMock()
        mock_config.llm = MagicMock(model='test-model')
        mock_config_cls.from_env.return_value = mock_config

        mock_analyzer = MagicMock()
        mock_analyzer.run.return_value = {'analyzed': False}
        mock_analyzer_cls.return_value = mock_analyzer

        result = runner.invoke(cli, ['ai', 'analyze-config'])
        assert result.exit_code == 0
        assert 'could not run' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('config.CollectorConfig')
    @patch('analyzers.config_analyzer.ConfigAnalyzer')
    def test_analyze_config_exception(self, mock_analyzer_cls,
                                       mock_config_cls, mock_db, runner):
        mock_config = MagicMock()
        mock_config.llm = MagicMock(model='test-model')
        mock_config_cls.from_env.return_value = mock_config

        mock_analyzer = MagicMock()
        mock_analyzer.run.side_effect = RuntimeError('boom')
        mock_analyzer_cls.return_value = mock_analyzer

        result = runner.invoke(cli, ['ai', 'analyze-config'])
        assert result.exit_code == 0
        assert 'Analysis failed' in result.output


class TestAiRegression:
    """Tests for `ic ai regression`."""

    @patch('cli.db.require_db', return_value=False)
    def test_regression_no_db(self, mock_db, runner):
        result = runner.invoke(cli, ['ai', 'regression'])
        assert result.exit_code == 0

    @patch('cli.db.require_db', return_value=True)
    @patch('config.CollectorConfig')
    @patch('analyzers.conforma_regression.ConformaRegressionTester')
    def test_regression_conforma_no_data(self, mock_tester_cls, mock_config_cls,
                                          mock_db, runner):
        mock_config_cls.from_env.return_value = MagicMock()
        mock_tester = MagicMock()
        mock_tester.run.return_value = {'analyzed': False}
        mock_tester_cls.return_value = mock_tester

        result = runner.invoke(cli, ['ai', 'regression'])
        assert result.exit_code == 0
        assert 'No resolved violations' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('config.CollectorConfig')
    @patch('analyzers.conforma_regression.ConformaRegressionTester')
    def test_regression_conforma_with_data(self, mock_tester_cls, mock_config_cls,
                                            mock_db, runner):
        mock_config_cls.from_env.return_value = MagicMock()
        mock_tester = MagicMock()
        mock_tester.run.return_value = {
            'analyzed': True,
            'evaluations_count': 10,
            'metrics': {
                'total_resolved': 20,
                'with_ai_analysis': 15,
                'ai_coverage_pct': 75,
                'accuracy': 0.85,
                'calibration_score': 0.9,
                'auto_fix_accuracy': 0.7,
            },
        }
        mock_tester_cls.return_value = mock_tester

        result = runner.invoke(cli, ['ai', 'regression'])
        assert result.exit_code == 0
        assert 'Regression Report' in result.output
        assert '85%' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('config.CollectorConfig')
    @patch('analyzers.build_regression.BuildRegressionTester')
    def test_regression_build_domain(self, mock_tester_cls, mock_config_cls,
                                      mock_db, runner):
        mock_config_cls.from_env.return_value = MagicMock()
        mock_tester = MagicMock()
        mock_tester.run.return_value = {'analyzed': False}
        mock_tester_cls.return_value = mock_tester

        result = runner.invoke(cli, ['ai', 'regression', 'build'])
        assert result.exit_code == 0
        assert 'No resolved build failures' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('config.CollectorConfig')
    @patch('analyzers.release_regression.ReleaseRegressionTester')
    def test_regression_release_domain(self, mock_tester_cls, mock_config_cls,
                                        mock_db, runner):
        mock_config_cls.from_env.return_value = MagicMock()
        mock_tester = MagicMock()
        mock_tester.run.return_value = {'analyzed': False}
        mock_tester_cls.return_value = mock_tester

        result = runner.invoke(cli, ['ai', 'regression', 'release'])
        assert result.exit_code == 0
        assert 'No release analyses' in result.output


class TestAiQuality:
    """Tests for `ic ai quality`."""

    @patch('cli.mode.is_cluster', return_value=True)
    @patch('cli.api_client.get_client')
    def test_quality_cluster_no_verdicts(self, mock_get_client, mock_cluster, runner):
        mock_client = MagicMock()
        mock_client.get.return_value = {'total_with_verdict': 0}
        mock_get_client.return_value = mock_client

        result = runner.invoke(cli, ['ai', 'quality'])
        assert result.exit_code == 0
        assert 'No verdicts recorded' in result.output

    @patch('cli.mode.is_cluster', return_value=True)
    @patch('cli.api_client.get_client')
    def test_quality_cluster_with_verdicts(self, mock_get_client, mock_cluster, runner):
        mock_client = MagicMock()
        mock_client.get.return_value = {
            'total_with_verdict': 10,
            'accuracy': 0.8,
            'correct': 6,
            'partial': 2,
            'incorrect': 2,
            'avg_confidence_correct': 0.9,
            'avg_confidence_incorrect': 0.5,
            'by_category': {
                'build_error': {'correct': 3, 'partial': 1, 'total': 5},
            },
        }
        mock_get_client.return_value = mock_client

        result = runner.invoke(cli, ['ai', 'quality'])
        assert result.exit_code == 0
        assert 'Accuracy' in result.output
        assert '80%' in result.output

    @patch('cli.mode.is_cluster', return_value=False)
    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_quality_local_no_verdicts(self, mock_get_repo, mock_db,
                                       mock_cluster, runner):
        mock_repo = MagicMock()
        mock_repo.get_quality_metrics.return_value = {'total_with_verdict': 0}
        mock_get_repo.return_value = mock_repo

        result = runner.invoke(cli, ['ai', 'quality'])
        assert result.exit_code == 0
        assert 'No verdicts recorded' in result.output


class TestAiStats:
    """Tests for `ic ai stats`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.api_client.get_client')
    def test_stats_cluster_with_data(self, mock_get_client, mock_cluster, runner):
        mock_client = MagicMock()
        mock_client.get.return_value = {
            'total_analyses': 42,
            'total_cost': 2.50,
        }
        mock_get_client.return_value = mock_client

        result = runner.invoke(cli, ['ai', 'stats'])
        assert result.exit_code == 0
        assert 'AI Analysis Statistics' in result.output
        assert '42' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.api_client.get_client')
    def test_stats_cluster_no_data(self, mock_get_client, mock_cluster, runner):
        mock_client = MagicMock()
        mock_client.get.return_value = None
        mock_get_client.return_value = mock_client

        result = runner.invoke(cli, ['ai', 'stats'])
        assert result.exit_code == 0
        assert 'No AI analysis data' in result.output


class TestAiReview:
    """Tests for `ic ai review`."""

    @patch('cli.db.require_db', return_value=False)
    def test_review_no_db(self, mock_db, runner):
        result = runner.invoke(cli, ['ai', 'review', 'comp1', '--jira', 'TEST-1'])
        assert result.exit_code == 0

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_review_no_analysis(self, mock_get_repo, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.get_full_analysis.return_value = None
        mock_get_repo.return_value = mock_repo

        result = runner.invoke(cli, ['ai', 'review', 'comp1', '--jira', 'TEST-1'])
        assert result.exit_code == 0
        assert 'No AI analysis found' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_review_no_ground_truth(self, mock_get_repo, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.get_full_analysis.return_value = {'root_cause': 'test'}
        mock_get_repo.return_value = mock_repo

        result = runner.invoke(cli, ['ai', 'review', 'comp1'])
        assert result.exit_code == 0
        assert 'ground truth' in result.output


# ===================================================================
#  CONFIG GROUP
# ===================================================================

class TestConfigGroup:
    """Tests for `ic config` commands."""

    @patch('cli.ic_config.get_mode', return_value='cluster')
    @patch('cli.ic_config.get_api_url', return_value='http://test:8000')
    @patch('cli.ic_config.get_api_key', return_value='test-key')
    def test_config_no_subcommand_cluster_mode(self, mock_key, mock_url,
                                                 mock_mode, runner):
        result = runner.invoke(cli, ['config'])
        assert result.exit_code == 0
        assert 'Current Configuration' in result.output
        assert 'cluster' in result.output

    @patch('cli.ic_config.get_mode', return_value='local')
    @patch('cli.db.check_db', return_value=False)
    def test_config_no_subcommand_local_mode_no_db(self, mock_db,
                                                     mock_mode, runner):
        result = runner.invoke(cli, ['config'])
        assert result.exit_code == 0
        assert 'Current Configuration' in result.output
        assert 'local' in result.output


class TestConfigSetApp:
    """Tests for `ic config set-app`."""

    @patch('cli.db.check_db', return_value=False)
    def test_set_app_no_db(self, mock_db, runner, tmp_path):
        env_file = tmp_path / '.env'
        env_file.write_text('NAMESPACE=test-ns\n')
        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.PROJECT_DIR = str(tmp_path)
            mock_cfg.APPLICATION_NAME = 'old-app'
            result = runner.invoke(cli, ['config', 'set-app', 'new-app'])
        assert result.exit_code == 0
        assert 'Application set to' in result.output

    @patch('cli.db.check_db', return_value=False)
    def test_set_app_force_flag(self, mock_db, runner, tmp_path):
        env_file = tmp_path / '.env'
        env_file.write_text('')
        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.PROJECT_DIR = str(tmp_path)
            mock_cfg.APPLICATION_NAME = 'old'
            result = runner.invoke(cli, ['config', 'set-app', 'unknown-app', '--force'])
        assert result.exit_code == 0
        assert 'Application set to' in result.output


class TestConfigUseCluster:
    """Tests for `ic config use-cluster`."""

    @patch('cli.ic_config.set_cluster')
    @patch('cli.ic_config.load', return_value={'cluster': {'verify_tls': True}})
    @patch('cli.ic_config.get_api_url', return_value='http://test:8000')
    @patch('cli.api_client.APIClient')
    def test_use_cluster_with_url(self, mock_client_cls, mock_url,
                                   mock_load, mock_set, runner):
        mock_client = MagicMock()
        mock_client.get.return_value = {'status': 'healthy'}
        mock_client_cls.return_value = mock_client

        result = runner.invoke(cli, ['config', 'use-cluster', 'http://test:8000'])
        assert result.exit_code == 0
        assert 'Mode: cluster' in result.output

    @patch('cli.ic_config.load', return_value={'cluster': {'api_url': ''}})
    def test_use_cluster_no_url_no_existing(self, mock_load, runner):
        result = runner.invoke(cli, ['config', 'use-cluster'])
        assert result.exit_code == 0
        assert 'Usage' in result.output


class TestConfigUseLocal:
    """Tests for `ic config use-local`."""

    @patch('cli.ic_config.set_mode')
    @patch('cli.mode.has_api', return_value=True)
    def test_use_local_api_running(self, mock_api, mock_set, runner):
        result = runner.invoke(cli, ['config', 'use-local'])
        assert result.exit_code == 0
        assert 'Mode: local' in result.output
        assert 'running' in result.output

    @patch('cli.ic_config.set_mode')
    @patch('cli.mode.has_api', return_value=False)
    def test_use_local_api_not_running(self, mock_api, mock_set, runner):
        result = runner.invoke(cli, ['config', 'use-local'])
        assert result.exit_code == 0
        assert 'not running' in result.output


# ===================================================================
#  CONFIG WATCH
# ===================================================================

class TestConfigWatchList:
    """Tests for `ic config watch list`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_watched_applications', return_value=['app1', 'app2'])
    @patch('config.ALL_WATCHERS', ['builds', 'tests', 'conforma', 'jira', 'components'])
    def test_watch_list_cluster(self, mock_apps, mock_cluster, runner):
        result = runner.invoke(cli, ['config', 'watch', 'list'])
        assert result.exit_code == 0
        assert 'Watch Daemon Configuration' in result.output
        assert 'app1' in result.output

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('config.ALL_WATCHERS', ['builds', 'tests'])
    def test_watch_list_local_no_apps(self, mock_cluster, runner):
        result = runner.invoke(cli, ['config', 'watch', 'list'],
                               env={'WATCH_APPLICATIONS': '', 'APPLICATION_NAME': ''})
        assert result.exit_code == 0
        assert 'Watch Daemon Configuration' in result.output


class TestConfigWatchAdd:
    """Tests for `ic config watch add`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.add_watched_application', return_value=['app1', 'new-app'])
    def test_watch_add_cluster(self, mock_add, mock_cluster, runner):
        result = runner.invoke(cli, ['config', 'watch', 'add', 'new-app'])
        assert result.exit_code == 0
        assert 'Added to watch list' in result.output

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.db.check_db', return_value=False)
    def test_watch_add_local_no_db(self, mock_db, mock_cluster, runner, tmp_path):
        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.PROJECT_DIR = str(tmp_path)
            env_file = tmp_path / '.env'
            env_file.write_text('')
            result = runner.invoke(cli, ['config', 'watch', 'add', 'new-app'],
                                   env={'WATCH_APPLICATIONS': ''})
        assert result.exit_code == 0
        assert 'Added to watch list' in result.output


class TestConfigWatchRemove:
    """Tests for `ic config watch remove`."""

    def test_watch_remove_not_in_list(self, runner):
        result = runner.invoke(cli, ['config', 'watch', 'remove', 'missing'],
                               env={'WATCH_APPLICATIONS': 'app1 app2'})
        assert result.exit_code == 1
        assert 'Not in watch list' in result.output

    def test_watch_remove_success(self, runner, tmp_path):
        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.PROJECT_DIR = str(tmp_path)
            env_file = tmp_path / '.env'
            env_file.write_text('')
            result = runner.invoke(cli, ['config', 'watch', 'remove', 'app1'],
                                   env={'WATCH_APPLICATIONS': 'app1 app2'})
        assert result.exit_code == 0
        assert 'Removed from watch list' in result.output


class TestConfigWatchEnable:
    """Tests for `ic config watch enable`."""

    @patch('config.ALL_WATCHERS', ['builds', 'tests', 'conforma'])
    def test_enable_unknown_watcher(self, runner):
        result = runner.invoke(cli, ['config', 'watch', 'enable', 'unknown'])
        assert result.exit_code == 1
        assert 'Unknown watcher' in result.output

    @patch('config.ALL_WATCHERS', ['builds', 'tests', 'conforma'])
    def test_enable_already_enabled(self, runner):
        result = runner.invoke(cli, ['config', 'watch', 'enable', 'builds'],
                               env={'WATCH_DISABLE': ''})
        assert result.exit_code == 0
        assert 'Already enabled' in result.output

    @patch('config.ALL_WATCHERS', ['builds', 'tests', 'conforma'])
    def test_enable_disabled_watcher(self, runner, tmp_path):
        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.PROJECT_DIR = str(tmp_path)
            env_file = tmp_path / '.env'
            env_file.write_text('')
            result = runner.invoke(cli, ['config', 'watch', 'enable', 'builds'],
                                   env={'WATCH_DISABLE': 'builds conforma'})
        assert result.exit_code == 0
        assert 'Enabled watcher' in result.output


class TestConfigWatchDisable:
    """Tests for `ic config watch disable`."""

    @patch('config.ALL_WATCHERS', ['builds', 'tests', 'conforma'])
    def test_disable_unknown_watcher(self, runner):
        result = runner.invoke(cli, ['config', 'watch', 'disable', 'unknown'])
        assert result.exit_code == 1
        assert 'Unknown watcher' in result.output

    @patch('config.ALL_WATCHERS', ['builds', 'tests', 'conforma'])
    def test_disable_already_disabled(self, runner):
        result = runner.invoke(cli, ['config', 'watch', 'disable', 'builds'],
                               env={'WATCH_DISABLE': 'builds'})
        assert result.exit_code == 0
        assert 'Already disabled' in result.output

    @patch('config.ALL_WATCHERS', ['builds', 'tests', 'conforma'])
    def test_disable_enabled_watcher(self, runner, tmp_path):
        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.PROJECT_DIR = str(tmp_path)
            env_file = tmp_path / '.env'
            env_file.write_text('')
            result = runner.invoke(cli, ['config', 'watch', 'disable', 'conforma'],
                                   env={'WATCH_DISABLE': ''})
        assert result.exit_code == 0
        assert 'Disabled watcher' in result.output


# ===================================================================
#  SKILLS GROUP
# ===================================================================

class TestSkillsList:
    """Tests for `ic skills list`."""

    @patch('skills.db_registry.get_registry')
    def test_skills_list_empty(self, mock_get_registry, runner):
        mock_registry = MagicMock()
        mock_registry.list_skills.return_value = []
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'list'])
        assert result.exit_code == 0
        assert 'No skills registered' in result.output

    @patch('skills.db_registry.get_registry')
    def test_skills_list_with_skills(self, mock_get_registry, runner):
        skill1 = MagicMock()
        skill1.name = 'rebuild'
        skill1.source = 'aiops-infra'
        skill1.status = 'active'
        skill1.tags = ['build', 'fix']

        skill2 = MagicMock()
        skill2.name = 'analyze'
        skill2.source = 'aiops-infra'
        skill2.status = 'active'
        skill2.tags = []

        mock_registry = MagicMock()
        mock_registry.list_skills.return_value = [skill1, skill2]
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'list'])
        assert result.exit_code == 0
        assert 'rebuild' in result.output
        assert 'analyze' in result.output
        assert '2 skill(s) total' in result.output

    @patch('skills.db_registry.get_registry')
    def test_skills_list_with_tag_filter(self, mock_get_registry, runner):
        skill1 = MagicMock()
        skill1.name = 'rebuild'
        skill1.source = 'aiops-infra'
        skill1.status = 'active'
        skill1.tags = ['build']

        mock_registry = MagicMock()
        mock_registry.list_skills.return_value = [skill1]
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'list', '--tag', 'build'])
        assert result.exit_code == 0
        assert 'rebuild' in result.output

    @patch('skills.db_registry.get_registry')
    def test_skills_list_json(self, mock_get_registry, runner):
        skill1 = MagicMock()
        skill1.name = 'rebuild'
        skill1.to_dict.return_value = {'name': 'rebuild', 'source': 'test'}

        mock_registry = MagicMock()
        mock_registry.list_skills.return_value = [skill1]
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'list', '--json'])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]['name'] == 'rebuild'


class TestSkillsInfo:
    """Tests for `ic skills info`."""

    @patch('skills.db_registry.get_registry')
    def test_skills_info_not_found(self, mock_get_registry, runner):
        mock_registry = MagicMock()
        mock_registry.get_skill.side_effect = KeyError("Skill 'missing' not found")
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'info', 'missing'])
        assert result.exit_code == 1
        assert 'not found' in result.output

    @patch('skills.db_registry.get_registry')
    def test_skills_info_success(self, mock_get_registry, runner):
        mock_skill = MagicMock()
        mock_skill.name = 'rebuild'
        mock_skill.qualified_name = 'aiops-infra/rebuild'
        mock_skill.source = 'aiops-infra'
        mock_skill.status = 'active'
        mock_skill.path = '/tmp/skills/rebuild'
        mock_skill.tags = ['build']
        mock_skill.metadata = MagicMock()
        mock_skill.metadata.description = 'Rebuild a failing component'
        mock_skill.metadata.allowed_tools = 'bash,kubectl'
        mock_skill.metadata.user_invocable = True
        mock_skill.metadata.ic_metadata = None

        mock_source = MagicMock()
        mock_source.name = 'aiops-infra'
        mock_source.url = 'https://github.com/test/repo'
        mock_source.branch = 'main'
        mock_source.commit = 'abc12345'
        mock_source.added_at = '2026-01-15T00:00:00'

        mock_registry = MagicMock()
        mock_registry.get_skill.return_value = mock_skill
        mock_registry.sources = {'aiops-infra': mock_source}
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'info', 'rebuild'])
        assert result.exit_code == 0
        assert 'aiops-infra/rebuild' in result.output
        assert 'Rebuild a failing component' in result.output


class TestSkillsRun:
    """Tests for `ic skills run`."""

    @patch('skills.db_registry.get_registry')
    def test_skills_run_not_found(self, mock_get_registry, runner):
        mock_registry = MagicMock()
        mock_registry.get_skill.return_value = None
        mock_registry.list_skills.return_value = []
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'run', 'missing'])
        assert result.exit_code == 1
        assert 'Skill not found' in result.output

    @patch('skills.db_registry.get_registry')
    @patch('skills.type_detector.should_use_agent', return_value=False)
    @patch('skills.executor.SkillExecutor')
    def test_skills_run_dry_run(self, mock_executor_cls, mock_agent_check,
                                 mock_get_registry, runner):
        mock_skill = MagicMock()
        mock_skill.name = 'rebuild'
        mock_skill.qualified_name = 'src/rebuild'
        mock_skill.status = 'active'
        mock_skill.metadata = MagicMock()
        mock_skill.metadata.execution_mode = 'script'

        mock_registry = MagicMock()
        mock_registry.get_skill.return_value = mock_skill
        mock_get_registry.return_value = mock_registry

        mock_result = MagicMock()
        mock_result.status = 'dry_run'
        mock_result.to_dict.return_value = {'status': 'dry_run'}
        mock_result.dry_run_steps = ['step1: kubectl get pods', 'step2: build image']
        mock_result.steps_total = 2
        mock_result.steps_executed = 0
        mock_result.duration_seconds = 0.0
        mock_result.stdout = ''
        mock_result.stderr = ''

        mock_executor = MagicMock()
        mock_executor.assess.return_value = MagicMock(
            level='low', reasons=[], security_warnings=[])
        mock_executor.execute.return_value = mock_result
        mock_executor_cls.return_value = mock_executor

        result = runner.invoke(cli, ['skills', 'run', 'rebuild', '--dry-run'])
        assert result.exit_code == 0
        assert 'dry_run' in result.output


class TestSkillsSources:
    """Tests for `ic skills sources`."""

    @patch('skills.db_registry.get_registry')
    @patch('skills.known_sources.KNOWN_SOURCES', {
        'test-source': {'url': 'https://example.com', 'description': 'Test source'},
    })
    def test_skills_sources_none_registered(self, mock_get_registry, runner):
        mock_registry = MagicMock()
        mock_registry.sources = {}
        mock_registry.list_sources.return_value = []
        mock_registry.skills = {}
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'sources'])
        assert result.exit_code == 0
        assert 'Registered Sources' in result.output
        assert 'None registered' in result.output
        assert 'Known Sources' in result.output

    @patch('skills.db_registry.get_registry')
    @patch('skills.known_sources.KNOWN_SOURCES', {
        'aiops-infra': {'url': 'https://example.com', 'description': 'AIOPS infra'},
    })
    def test_skills_sources_with_registered(self, mock_get_registry, runner):
        mock_source = MagicMock()
        mock_source.name = 'aiops-infra'
        mock_source.commit = 'abc12345def'
        mock_source.branch = 'main'

        mock_registry = MagicMock()
        mock_registry.sources = {'aiops-infra': mock_source}
        mock_registry.list_sources.return_value = [mock_source]
        mock_registry.skills = {
            'aiops-infra/rebuild': MagicMock(source='aiops-infra'),
        }
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'sources'])
        assert result.exit_code == 0
        assert 'aiops-infra' in result.output
        assert '1 skill(s)' in result.output


# ===================================================================
#  ONBOARD GROUP
# ===================================================================

class TestOnboardStatus:
    """Tests for `ic onboard status`."""

    @patch('cli.data.get_onboarding_status', return_value=None)
    def test_onboard_status_no_data(self, mock_status, runner):
        result = runner.invoke(cli, ['onboard', 'status'])
        assert result.exit_code == 0
        assert 'Could not fetch' in result.output

    @patch('cli.data.get_onboarding_status', return_value={'error': 'API down'})
    def test_onboard_status_api_error(self, mock_status, runner):
        result = runner.invoke(cli, ['onboard', 'status'])
        assert result.exit_code == 0
        assert 'API down' in result.output

    @patch('cli.data.get_onboarding_status', return_value={
        'application': 'test-app',
        'total': 5,
        'complete': 3,
        'partial': 1,
        'incomplete': 1,
        'components': [
            {
                'component': 'comp-a',
                'score': 100,
                'overall': 'complete',
                'failing': [],
                'warnings': [],
                'checks': {},
            },
            {
                'component': 'comp-b',
                'score': 60,
                'overall': 'partial',
                'failing': ['build'],
                'warnings': ['nudge'],
                'checks': {
                    'build': {'detail': 'build failing', 'fix': 'fix build'},
                    'nudge': {'detail': 'nudge missing', 'fix': 'add nudge'},
                },
            },
            {
                'component': 'comp-c',
                'score': 20,
                'overall': 'incomplete',
                'failing': ['component', 'application'],
                'warnings': [],
                'checks': {
                    'component': {'detail': 'not created', 'fix': 'create it'},
                    'application': {'detail': 'missing', 'fix': 'add it'},
                },
            },
        ],
    })
    def test_onboard_status_with_data(self, mock_status, runner):
        result = runner.invoke(cli, ['onboard', 'status'])
        assert result.exit_code == 0
        assert 'Onboarding Status' in result.output
        assert 'comp-b' in result.output
        assert 'comp-c' in result.output
        # complete components hidden by default
        assert 'comp-a' not in result.output

    @patch('cli.data.get_onboarding_status', return_value={
        'application': 'test-app',
        'total': 1,
        'complete': 1,
        'partial': 0,
        'incomplete': 0,
        'components': [
            {
                'component': 'comp-a',
                'score': 100,
                'overall': 'complete',
                'failing': [],
                'warnings': [],
                'checks': {},
            },
        ],
    })
    def test_onboard_status_all_flag(self, mock_status, runner):
        result = runner.invoke(cli, ['onboard', 'status', '--all'])
        assert result.exit_code == 0
        assert 'comp-a' in result.output

    @patch('cli.data.get_onboarding_status', return_value={
        'application': 'test-app',
        'total': 1,
        'complete': 0,
        'partial': 1,
        'incomplete': 0,
        'components': [],
    })
    def test_onboard_status_json(self, mock_status, runner):
        result = runner.invoke(cli, ['onboard', 'status', '--json'])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['application'] == 'test-app'


class TestOnboardDescribe:
    """Tests for `ic onboard describe`."""

    @patch('cli.data.get_onboarding_describe', return_value=None)
    def test_describe_no_data(self, mock_describe, runner):
        result = runner.invoke(cli, ['onboard', 'describe', 'comp1'])
        assert result.exit_code == 0
        assert 'Could not fetch' in result.output

    @patch('cli.data.get_onboarding_describe', return_value={'error': 'Not found'})
    def test_describe_error(self, mock_describe, runner):
        result = runner.invoke(cli, ['onboard', 'describe', 'comp1'])
        assert result.exit_code == 0
        assert 'Not found' in result.output

    @patch('cli.data.get_onboarding_describe', return_value={
        'component': 'comp1',
        'type': 'odh',
        'phase': 'in_progress',
        'progress': 60,
        'steps_done': 3,
        'steps_total': 5,
        'automation_progress': '',
        'created_at': '2026-01-01T00:00:00',
        'steps': [
            {'step': 'component', 'label': 'Component CR', 'status': 'done'},
            {'step': 'application', 'label': 'Application', 'status': 'done'},
            {'step': 'build', 'label': 'Build', 'status': 'done',
             'detail': 'Pipeline passed'},
            {'step': 'conforma', 'label': 'Conforma', 'status': 'pending',
             'detail': 'Not started', 'fix': 'run conforma'},
            {'step': 'nudge', 'label': 'Nudge PR', 'status': 'pending'},
        ],
        'jira_tickets': [],
        'jira_available': True,
    })
    def test_describe_success(self, mock_describe, runner):
        result = runner.invoke(cli, ['onboard', 'describe', 'comp1'])
        assert result.exit_code == 0
        assert 'Onboarding: comp1' in result.output
        assert 'ODH (upstream)' in result.output
        assert 'Component CR' in result.output

    @patch('cli.data.get_onboarding_describe', return_value={
        'component': 'comp1',
        'type': 'rhoai',
        'phase': 'complete',
        'progress': 100,
        'steps_done': 5,
        'steps_total': 5,
        'automation_progress': '8/8',
        'steps': [],
        'jira_tickets': [],
    })
    def test_describe_json_output(self, mock_describe, runner):
        result = runner.invoke(cli, ['onboard', 'describe', 'comp1', '--json'])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['component'] == 'comp1'


# ===================================================================
#  DB GROUP
# ===================================================================

class TestDbStatus:
    """Tests for `ic db status`."""

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.sql', return_value='PostgreSQL 15.2')
    def test_db_status_connected(self, mock_sql, mock_db, runner):
        result = runner.invoke(cli, ['db', 'status'])
        assert result.exit_code == 0
        assert 'Database connected' in result.output
        assert 'PostgreSQL' in result.output

    @patch('cli.db.require_db', return_value=False)
    def test_db_status_not_connected(self, mock_db, runner):
        result = runner.invoke(cli, ['db', 'status'])
        assert result.exit_code == 0


class TestDbQuery:
    """Tests for `ic db query`."""

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.sql_table')
    def test_db_query_success(self, mock_sql_table, mock_db, runner):
        result = runner.invoke(cli, ['db', 'query', 'SELECT 1'])
        assert result.exit_code == 0
        mock_sql_table.assert_called_once_with('SELECT 1')

    @patch('cli.db.require_db', return_value=False)
    def test_db_query_no_db(self, mock_db, runner):
        result = runner.invoke(cli, ['db', 'query', 'SELECT 1'])
        assert result.exit_code == 0


# ===================================================================
#  REBUILD COMMAND
# ===================================================================

class TestRebuild:
    """Tests for `ic rebuild`."""

    def test_rebuild_dry_run(self, runner):
        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.NAMESPACE = 'test-ns'
            result = runner.invoke(cli, ['rebuild', 'comp1', '--dry-run'])
        assert result.exit_code == 0
        assert 'dry run' in result.output.lower()
        assert 'kubectl annotate' in result.output

    def test_rebuild_no_namespace(self, runner):
        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.NAMESPACE = ''
            result = runner.invoke(cli, ['rebuild', 'comp1'])
        assert result.exit_code != 0

    @patch('clients.kubernetes.KubernetesClient')
    def test_rebuild_component_not_found(self, mock_kc_cls, runner):
        mock_kc = MagicMock()
        mock_kc.get_component_metadata.return_value = None
        mock_kc_cls.return_value = mock_kc

        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.NAMESPACE = 'test-ns'
            result = runner.invoke(cli, ['rebuild', 'missing-comp'])
        assert result.exit_code != 0
        assert 'not found' in (result.output + (result.stderr_bytes or b'').decode()).lower()

    @patch('clients.kubernetes.KubernetesClient')
    def test_rebuild_success(self, mock_kc_cls, runner):
        mock_kc = MagicMock()
        mock_kc.get_component_metadata.return_value = {
            'repository_url': 'https://github.com/test/repo',
            'branch': 'main',
        }
        mock_kc.trigger_rebuild.return_value = None
        mock_kc_cls.return_value = mock_kc

        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.NAMESPACE = 'test-ns'
            result = runner.invoke(cli, ['rebuild', 'comp1'])
        assert result.exit_code == 0
        assert 'Build triggered successfully' in result.output

    @patch('clients.kubernetes.KubernetesClient')
    def test_rebuild_exception(self, mock_kc_cls, runner):
        mock_kc = MagicMock()
        mock_kc.get_component_metadata.return_value = {
            'repository_url': 'https://github.com/test/repo',
            'branch': 'main',
        }
        mock_kc.trigger_rebuild.side_effect = RuntimeError('K8s error')
        mock_kc_cls.return_value = mock_kc

        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.NAMESPACE = 'test-ns'
            result = runner.invoke(cli, ['rebuild', 'comp1'])
        assert result.exit_code != 0
        assert 'Error triggering rebuild' in result.output

    @patch('clients.kubernetes.KubernetesClient')
    def test_rebuild_with_wait_found(self, mock_kc_cls, runner):
        mock_kc = MagicMock()
        mock_kc.get_component_metadata.return_value = {
            'repository_url': 'https://github.com/test/repo',
            'branch': 'main',
        }
        mock_kc.trigger_rebuild.return_value = None
        mock_kc.list_recent_pipelineruns.return_value = [
            {'name': 'pr-12345', 'status': 'running'},
        ]
        mock_kc_cls.return_value = mock_kc

        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.NAMESPACE = 'test-ns'
            with patch('time.sleep'):
                result = runner.invoke(cli, ['rebuild', 'comp1', '--wait'])
        assert result.exit_code == 0
        assert 'PipelineRun started' in result.output

    @patch('clients.kubernetes.KubernetesClient')
    def test_rebuild_with_custom_namespace(self, mock_kc_cls, runner):
        mock_kc = MagicMock()
        mock_kc.get_component_metadata.return_value = {
            'repository_url': 'https://github.com/test/repo',
            'branch': 'main',
        }
        mock_kc.trigger_rebuild.return_value = None
        mock_kc_cls.return_value = mock_kc

        result = runner.invoke(cli, ['rebuild', 'comp1', '-n', 'custom-ns'])
        assert result.exit_code == 0
        assert 'Build triggered successfully' in result.output


# ===================================================================
#  AI ANALYZE ONBOARDING
# ===================================================================

class TestAiAnalyzeOnboarding:
    """Tests for `ic ai analyze onboarding <comp>`."""

    @patch('cli.mode.is_cluster', return_value=False)
    def test_analyze_onboarding_not_cluster(self, mock_cluster, runner):
        result = runner.invoke(cli, ['ai', 'analyze', 'onboarding', 'comp1'])
        assert result.exit_code == 0
        assert 'cluster mode' in result.output

    @patch('cli.mode.is_cluster', return_value=True)
    @patch('cli.data.require_data', return_value=True)
    @patch('cli.data.get_onboarding_describe', return_value={'error': 'not found'})
    def test_analyze_onboarding_no_data(self, mock_describe, mock_req,
                                         mock_cluster, runner):
        result = runner.invoke(cli, ['ai', 'analyze', 'onboarding', 'comp1'])
        assert result.exit_code == 0
        assert 'not found' in result.output

    @patch('cli.mode.is_cluster', return_value=True)
    @patch('cli.data.require_data', return_value=True)
    @patch('cli.data.get_onboarding_describe', return_value={
        'phase': 'complete',
        'progress': 100,
    })
    def test_analyze_onboarding_already_complete(self, mock_describe, mock_req,
                                                   mock_cluster, runner):
        result = runner.invoke(cli, ['ai', 'analyze', 'onboarding', 'comp1'])
        assert result.exit_code == 0
        assert 'complete' in result.output.lower()


# ===================================================================
#  EDGE CASES AND HELPERS
# ===================================================================

class TestFormatSince:
    """Tests for the _format_since helper."""

    def test_format_since_iso_datetime(self):
        from cli.main import _format_since
        result = _format_since('2026-06-30T08:04:15.123456')
        assert '30 Jun' in result
        assert '08:04' in result

    def test_format_since_iso_date(self):
        from cli.main import _format_since
        result = _format_since('2026-06-30')
        assert '30 Jun' in result

    def test_format_since_empty(self):
        from cli.main import _format_since
        assert _format_since('') == ''
        assert _format_since(None) == ''

    def test_format_since_garbage(self):
        from cli.main import _format_since
        result = _format_since('not-a-date')
        assert isinstance(result, str)


class TestPolicyFilterToIncludeFuture:
    """Tests for _policy_filter_to_include_future helper."""

    def test_future_returns_true(self):
        from cli.main import _policy_filter_to_include_future
        assert _policy_filter_to_include_future('future') is True

    def test_all_returns_all(self):
        from cli.main import _policy_filter_to_include_future
        assert _policy_filter_to_include_future('all') == 'all'

    def test_current_returns_none(self):
        from cli.main import _policy_filter_to_include_future
        assert _policy_filter_to_include_future('current') is None


class TestSkillsTags:
    """Tests for skill tag management."""

    @patch('skills.db_registry.get_registry')
    def test_tags_list_empty(self, mock_get_registry, runner):
        mock_registry = MagicMock()
        mock_registry.list_tags.return_value = {}
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'tags'])
        assert result.exit_code == 0
        assert 'No tags in use' in result.output

    @patch('skills.db_registry.get_registry')
    def test_tags_list_with_tags(self, mock_get_registry, runner):
        mock_registry = MagicMock()
        mock_registry.list_tags.return_value = {'build': 3, 'fix': 1}
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'tags'])
        assert result.exit_code == 0
        assert 'build' in result.output
        assert '3 skill(s)' in result.output

    @patch('skills.db_registry.get_registry')
    def test_tag_add_success(self, mock_get_registry, runner):
        mock_registry = MagicMock()
        mock_registry.add_tag.return_value = True
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'tag', 'add', 'my-skill', 'new-tag'])
        assert result.exit_code == 0
        assert 'Added tag' in result.output

    @patch('skills.db_registry.get_registry')
    def test_tag_add_skill_not_found(self, mock_get_registry, runner):
        mock_registry = MagicMock()
        mock_registry.add_tag.return_value = False
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'tag', 'add', 'missing', 'tag'])
        assert result.exit_code == 1
        assert 'not found' in result.output.lower()

    @patch('skills.db_registry.get_registry')
    def test_tag_remove_success(self, mock_get_registry, runner):
        mock_registry = MagicMock()
        mock_registry.remove_tag.return_value = True
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'tag', 'remove', 'my-skill', 'old-tag'])
        assert result.exit_code == 0
        assert 'Removed tag' in result.output


class TestSkillsRuns:
    """Tests for `ic skills runs`."""

    @patch('skills.db_registry.get_registry')
    def test_runs_no_history(self, mock_get_registry, runner):
        mock_registry = MagicMock()
        mock_registry.get_run_history.return_value = []
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'runs'])
        assert result.exit_code == 0
        assert 'no runs' in result.output

    @patch('skills.db_registry.get_registry')
    def test_runs_with_history(self, mock_get_registry, runner):
        mock_registry = MagicMock()
        mock_registry.get_run_history.return_value = [
            {
                'started_at': '2026-06-30T10:00:00',
                'skill_name': 'rebuild',
                'status': 'success',
                'duration_seconds': 12.5,
                'triggered_by': 'user',
            },
        ]
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'runs'])
        assert result.exit_code == 0
        assert 'rebuild' in result.output

    @patch('skills.db_registry.get_registry')
    def test_runs_exception_graceful(self, mock_get_registry, runner):
        mock_registry = MagicMock()
        mock_registry.get_run_history.side_effect = Exception('table missing')
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'runs'])
        assert result.exit_code == 0
        assert 'No execution history' in result.output


class TestSkillsValidate:
    """Tests for `ic skills validate`."""

    @patch('skills.db_registry.get_registry')
    def test_validate_skill_not_found(self, mock_get_registry, runner):
        mock_registry = MagicMock()
        mock_registry.get_skill.side_effect = KeyError("Skill 'missing' not found")
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'validate', 'missing'])
        assert result.exit_code == 1
        assert 'not found' in result.output

    @patch('skills.db_registry.get_registry')
    @patch('skills.validator.SkillValidator')
    def test_validate_no_issues(self, mock_validator_cls, mock_get_registry, runner):
        mock_skill = MagicMock()
        mock_skill.path = '/tmp/skills/test'
        mock_skill.metadata = MagicMock()

        mock_registry = MagicMock()
        mock_registry.get_skill.return_value = mock_skill
        mock_get_registry.return_value = mock_registry

        mock_result = MagicMock()
        mock_result.findings = []
        mock_result.skill_name = 'test'

        mock_validator = MagicMock()
        mock_validator.validate.return_value = mock_result
        mock_validator_cls.return_value = mock_validator

        result = runner.invoke(cli, ['skills', 'validate', 'test'])
        assert result.exit_code == 0
        assert 'No issues found' in result.output


class TestAiAnalyzeConfigRelease:
    """Test `ic ai analyze-config --type release`."""

    @patch('cli.db.require_db', return_value=True)
    @patch('config.CollectorConfig')
    @patch('analyzers.release_config_analyzer.ReleaseConfigAnalyzer')
    def test_analyze_config_release_type(self, mock_analyzer_cls,
                                          mock_config_cls, mock_db, runner):
        mock_config = MagicMock()
        mock_config.llm = MagicMock(model='test-model')
        mock_config_cls.from_env.return_value = mock_config

        mock_analyzer = MagicMock()
        mock_analyzer.run.return_value = {
            'analyzed': True,
            'analysis': {
                'overall_severity': 'critical',
                'confidence_score': 0.95,
                'summary': 'Release blockers found',
                'findings': [],
                'auto_rebuild_candidates': [],
                'release_blockers': ['Missing SBOM for comp1'],
            },
            'cost_usd': 0.02,
            'model': 'test-model',
        }
        mock_analyzer_cls.return_value = mock_analyzer

        result = runner.invoke(cli, ['ai', 'analyze-config', '--type', 'release'])
        assert result.exit_code == 0
        assert 'Release Configuration' in result.output
        assert 'CRITICAL' in result.output
        assert 'Release Blockers' in result.output
        assert 'Missing SBOM' in result.output


class TestAiAnalyzeConfigWithApp:
    """Test `ic ai analyze-config --app <app>`."""

    @patch('cli.db.require_db', return_value=True)
    @patch('config.CollectorConfig')
    @patch('analyzers.config_analyzer.ConfigAnalyzer')
    def test_analyze_config_custom_app(self, mock_analyzer_cls,
                                        mock_config_cls, mock_db, runner):
        mock_config = MagicMock()
        mock_config.llm = MagicMock(model='test-model')
        mock_config_cls.from_env.return_value = mock_config

        mock_analyzer = MagicMock()
        mock_analyzer.run.return_value = {
            'analyzed': True,
            'analysis': {
                'overall_severity': 'info',
                'confidence_score': 0.7,
                'summary': 'OK',
                'findings': [],
                'auto_rebuild_candidates': [],
                'release_blockers': [],
            },
            'cost_usd': 0.01,
            'model': 'test-model',
        }
        mock_analyzer_cls.return_value = mock_analyzer

        result = runner.invoke(cli, ['ai', 'analyze-config', '--app', 'custom-app'])
        assert result.exit_code == 0
        assert 'custom-app' in result.output


class TestOnboardNoSubcommand:
    """Test `ic onboard` with no subcommand defaults to status."""

    @patch('cli.data.get_onboarding_status', return_value={
        'application': 'test-app',
        'total': 0,
        'complete': 0,
        'partial': 0,
        'incomplete': 0,
        'components': [],
    })
    def test_onboard_defaults_to_status(self, mock_status, runner):
        result = runner.invoke(cli, ['onboard'])
        assert result.exit_code == 0
        assert 'Onboarding Status' in result.output


class TestDbMissingArg:
    """Test db query requires SQL argument."""

    def test_db_query_missing_arg(self, runner):
        result = runner.invoke(cli, ['db', 'query'])
        assert result.exit_code != 0


class TestRebuildMissingArg:
    """Test rebuild requires component argument."""

    def test_rebuild_missing_arg(self, runner):
        result = runner.invoke(cli, ['rebuild'])
        assert result.exit_code != 0


class TestSkillsInfoKeyError:
    """Test skills info with KeyError from registry."""

    @patch('skills.db_registry.get_registry')
    def test_skills_info_key_error_message(self, mock_get_registry, runner):
        mock_registry = MagicMock()
        mock_registry.get_skill.side_effect = KeyError(
            "Skill 'foo' not found. Did you mean 'aiops-infra/foo'?")
        mock_get_registry.return_value = mock_registry

        result = runner.invoke(cli, ['skills', 'info', 'foo'])
        assert result.exit_code == 1
        assert 'Did you mean' in result.output


class TestAiBatchSuccess:
    """Test ai batch with successful analysis cycle."""

    @patch('cli.mode.is_cluster', return_value=True)
    @patch('cli.data.require_data', return_value=True)
    @patch('cli.data.get_alerts', return_value={
        'build_failures': [
            {'component': 'comp1', 'has_analysis': False},
        ],
    })
    @patch('cli.data.get_failure_details', return_value={
        'component': 'comp1',
        'build_logs': 'error in build',
        'error_message': 'failed',
    })
    @patch('cli.data.submit_analysis', return_value={'action': 'stored'})
    @patch('analyzers.build_failure_analyzer.BuildFailureAnalyzer')
    @patch('clients.llm_provider.create_llm_provider')
    @patch('config.CollectorConfig')
    def test_batch_analyzes_one(self, mock_config_cls, mock_llm_fn,
                                 mock_analyzer_cls, mock_submit,
                                 mock_details, mock_alerts, mock_req,
                                 mock_cluster, runner):
        mock_config = MagicMock()
        mock_config.llm = MagicMock(model='test-model')
        mock_config_cls.from_env.return_value = mock_config

        mock_llm = MagicMock()
        mock_llm.model_name.return_value = 'test-model'
        mock_response = MagicMock()
        mock_response.input_tokens = 100
        mock_response.output_tokens = 50
        mock_llm.create_message.return_value = mock_response
        mock_llm_fn.return_value = mock_llm

        mock_analyzer = MagicMock()
        mock_analyzer.build_analysis_prompt.return_value = ('sys', 'user')
        mock_analyzer.parse_analysis_response.return_value = {
            'root_cause': 'test error',
            'failure_category': 'build',
            'confidence_score': 0.9,
            'recommended_fix': 'fix it',
            'can_auto_fix': False,
        }
        mock_analyzer_cls.return_value = mock_analyzer

        result = runner.invoke(cli, ['ai', 'batch'])
        assert result.exit_code == 0
        assert 'Batch analyzing 1 failure(s)' in result.output
        assert 'Done: 1/1 analyzed' in result.output


class TestConfigWatchNoSubcommand:
    """Test `ic config watch` with no subcommand defaults to list."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_watched_applications', return_value=['app1'])
    @patch('config.ALL_WATCHERS', ['builds', 'tests'])
    def test_config_watch_defaults_to_list(self, mock_apps, mock_cluster, runner):
        result = runner.invoke(cli, ['config', 'watch'])
        assert result.exit_code == 0
        assert 'Watch Daemon Configuration' in result.output
