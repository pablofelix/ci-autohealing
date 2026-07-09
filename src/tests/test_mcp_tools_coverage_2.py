"""Extended coverage tests (batch 2) for MCP server tools.

Covers tools NOT tested in ``test_mcp_tools_coverage.py``:
  get_component_status, get_component_prs, get_health, get_snapshot_status,
  get_release_status, get_release_vulnerabilities, get_test_configuration,
  get_health_warnings, get_conforma_report, lookup_image, get_snapshot_freshness,
  get_stale_components, get_nightly_status, get_nightly_history,
  add_watch_application, remove_watch_application,
  get_skill_info, list_skill_sources, validate_skill, check_skill_prerequisites,
  run_skill, get_ec_policy_summary, get_scenario_coverage, get_resolved_patterns,
  get_config_analysis, get_build_config_analysis, get_release_config_analysis,
  get_regression_report, trigger_rebuild, get_release_readiness,
  get_onboarding_status, get_onboarding_describe, analyze_onboarding
"""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_mcp_tools_coverage.py)
# ---------------------------------------------------------------------------

def _stub_mcp():
    m = MagicMock()
    m.tool.return_value = lambda fn: fn
    return m


@pytest.fixture(autouse=True)
def _patch_mcp(monkeypatch):
    stub = _stub_mcp()
    monkeypatch.setattr('mcp_server.mcp', stub)


def _run(coro_or_func, *args, **kwargs):
    result = coro_or_func(*args, **kwargs)
    if asyncio.iscoroutine(result):
        return asyncio.get_event_loop().run_until_complete(result)
    return result


def _now():
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# get_component_status
# ---------------------------------------------------------------------------

class TestGetComponentStatus:

    def test_component_found(self):
        with patch('clients.kubernetes.KubernetesClient') as mock_kc_cls, \
             patch('openshift_auth._ensure_k8s_config'):
            mock_kc_cls.return_value.get_component_metadata.return_value = {
                'repository_url': 'https://github.com/org/repo',
                'branch': 'rhoai-v3.5',
                'container_image': 'quay.io/org/img:sha',
            }
            from mcp_server.tools import get_component_status
            result = _run(get_component_status, 'my-comp')
        assert result['component'] == 'my-comp'
        assert result['repository_url'] == 'https://github.com/org/repo'

    def test_component_not_found(self):
        with patch('clients.kubernetes.KubernetesClient') as mock_kc_cls, \
             patch('openshift_auth._ensure_k8s_config'):
            mock_kc_cls.return_value.get_component_metadata.return_value = None
            from mcp_server.tools import get_component_status
            result = _run(get_component_status, 'ghost')
        assert 'error' in result


# ---------------------------------------------------------------------------
# get_component_prs
# ---------------------------------------------------------------------------

class TestGetComponentPrs:

    def test_prs_returned(self):
        with patch('clients.kubernetes.KubernetesClient') as mock_kc_cls, \
             patch('openshift_auth._ensure_k8s_config'), \
             patch('clients.github_client.parse_github_repo', return_value=('org', 'repo')), \
             patch('clients.github_client.GitHubClient') as mock_gh_cls, \
             patch.dict(os.environ, {'GITHUB_TOKEN': 'tok'}):
            mock_kc_cls.return_value.get_component_metadata.return_value = {
                'repository_url': 'https://github.com/org/repo',
                'branch': 'main',
            }
            mock_gh_cls.return_value.list_pull_requests.return_value = [
                {'number': 1, 'title': 'Fix build', 'url': 'http://pr/1', 'author': 'dev'},
            ]
            from mcp_server.tools import get_component_prs
            result = _run(get_component_prs, 'comp-a')
        assert result['component'] == 'comp-a'
        assert len(result['prs']) == 1

    def test_no_repo_url(self):
        with patch('clients.kubernetes.KubernetesClient') as mock_kc_cls, \
             patch('openshift_auth._ensure_k8s_config'):
            mock_kc_cls.return_value.get_component_metadata.return_value = None
            from mcp_server.tools import get_component_prs
            result = _run(get_component_prs, 'ghost')
        assert 'error' in result

    def test_unparseable_repo(self):
        with patch('clients.kubernetes.KubernetesClient') as mock_kc_cls, \
             patch('openshift_auth._ensure_k8s_config'), \
             patch('clients.github_client.parse_github_repo', return_value=None):
            mock_kc_cls.return_value.get_component_metadata.return_value = {
                'repository_url': 'not-a-github-url',
                'branch': 'main',
            }
            from mcp_server.tools import get_component_prs
            result = _run(get_component_prs, 'comp-b')
        assert 'error' in result


# ---------------------------------------------------------------------------
# get_health
# ---------------------------------------------------------------------------

class TestGetHealth:

    def test_returns_summary(self):
        with patch('proactive.health_monitor.HealthMonitor') as mock_hm_cls, \
             patch('repositories.repository_factory.get_pool'):
            mock_hm_cls.return_value.get_component_health_summary.return_value = [
                {'component': 'c1', 'score': 80},
            ]
            from mcp_server.tools import get_health
            result = _run(get_health)
        assert len(result) == 1
        assert result[0]['score'] == 80

    def test_empty(self):
        with patch('proactive.health_monitor.HealthMonitor') as mock_hm_cls, \
             patch('repositories.repository_factory.get_pool'):
            mock_hm_cls.return_value.get_component_health_summary.return_value = None
            from mcp_server.tools import get_health
            result = _run(get_health)
        assert result == []


# ---------------------------------------------------------------------------
# get_snapshot_status
# ---------------------------------------------------------------------------

class TestGetSnapshotStatus:

    def test_no_snapshots(self):
        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls:
            mock_kc_cls.return_value.get_snapshots.return_value = []
            from mcp_server.tools import get_snapshot_status
            result = _run(get_snapshot_status, application='app')
        assert 'error' in result

    def test_snapshots_with_override(self):
        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls:
            mock_kc_cls.return_value.get_snapshots.return_value = [
                {'metadata': {'name': 'snap-1'}},
                {'metadata': {'name': 'snap-2'}},
            ]
            mock_kc_cls.return_value.extract_snapshot_status.side_effect = [
                {
                    'is_override': True,
                    'event_type': 'override',
                    'warnings': ['warn1'],
                },
                {
                    'is_override': False,
                    'event_type': 'push',
                    'warnings': [],
                },
            ]
            from mcp_server.tools import get_snapshot_status
            result = _run(get_snapshot_status, application='app')
        assert result['snapshot_count'] == 2
        assert result['override_snapshots'] == 1
        assert result['total_warnings'] == 1


# ---------------------------------------------------------------------------
# get_release_status
# ---------------------------------------------------------------------------

class TestGetReleaseStatus:

    def test_no_releases(self):
        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls:
            mock_kc_cls.return_value.get_releases.return_value = []
            from mcp_server.tools import get_release_status
            result = _run(get_release_status, application='app')
        assert 'error' in result

    def test_releases_returned(self):
        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls:
            mock_kc_cls.return_value.get_releases.return_value = [
                {'metadata': {'name': 'rel-1'}},
            ]
            mock_kc_cls.return_value.extract_release_status.return_value = {
                'name': 'rel-1', 'status': 'succeeded',
            }
            from mcp_server.tools import get_release_status
            result = _run(get_release_status, application='app')
        assert result['release_count'] == 1
        assert result['releases'][0]['status'] == 'succeeded'


# ---------------------------------------------------------------------------
# get_release_vulnerabilities
# ---------------------------------------------------------------------------

class TestGetReleaseVulnerabilities:

    def test_no_snapshots(self):
        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls:
            mock_kc_cls.return_value.get_snapshots.return_value = []
            from mcp_server.tools import get_release_vulnerabilities
            result = _run(get_release_vulnerabilities, application='app')
        assert 'error' in result

    def test_with_sarif_results(self):
        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls, \
             patch('clients.registry_client.RegistryClient') as mock_rc_cls:
            mock_kc_cls.return_value.get_snapshots.return_value = [{
                'metadata': {'name': 'snap-1'},
                'spec': {
                    'components': [
                        {'name': 'comp-a', 'containerImage': 'quay.io/org/a@sha256:abc'},
                        {'name': 'comp-b', 'containerImage': 'quay.io/org/b@sha256:def'},
                    ],
                },
            }]
            mock_rc_cls.return_value.fetch_sarif_batch.return_value = {
                'comp-a': [
                    {'level': 'error', 'ruleId': 'CVE-2024-001'},
                    {'level': 'warning', 'ruleId': 'CVE-2024-002'},
                ],
                'comp-b': [],
            }
            from mcp_server.tools import get_release_vulnerabilities
            result = _run(get_release_vulnerabilities, application='app')
        assert result['components_scanned'] == 1  # comp-b had empty results
        assert result['total_critical'] == 1
        assert result['total_high'] == 1


# ---------------------------------------------------------------------------
# get_test_configuration
# ---------------------------------------------------------------------------

class TestGetTestConfiguration:

    def test_no_scenarios(self):
        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls:
            mock_kc_cls.return_value.get_integration_test_scenarios.return_value = []
            from mcp_server.tools import get_test_configuration
            result = _run(get_test_configuration, application='app')
        assert 'error' in result

    def test_scenarios_with_issues(self):
        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls:
            mock_kc_cls.return_value.get_integration_test_scenarios.return_value = [
                {'metadata': {'name': 's1'}},
                {'metadata': {'name': 's2'}},
            ]
            mock_kc_cls.return_value.extract_its_metadata.side_effect = [
                {
                    'name': 's1', 'is_disabled': True, 'is_conforma': False,
                    'is_future': False, 'contexts': [],
                },
                {
                    'name': 's2', 'is_disabled': False, 'is_conforma': False,
                    'is_future': True, 'contexts': ['component_foo'],
                },
            ]
            from mcp_server.tools import get_test_configuration
            result = _run(get_test_configuration, application='app')
        assert result['total_scenarios'] == 2
        assert result['disabled'] == 1
        assert result['conforma_scenarios'] == 0
        assert any('DISABLED' in i for i in result['issues'])
        assert any('NO CONFORMA' in i for i in result['issues'])
        assert any('SCOPED' in i for i in result['issues'])
        assert any('FUTURE' in i for i in result['issues'])


# ---------------------------------------------------------------------------
# get_health_warnings
# ---------------------------------------------------------------------------

class TestGetHealthWarnings:

    def test_returns_warnings(self):
        mock_check = MagicMock()
        mock_check.signal_type = 'failure_spike'
        mock_check.component_name = 'comp-a'
        mock_check.message = 'Failure rate increased'
        mock_check.severity = 'high'

        with patch('proactive.health_monitor.HealthMonitor') as mock_hm_cls, \
             patch('repositories.repository_factory.get_pool'):
            mock_hm_cls.return_value.run_checks.return_value = [mock_check]
            from mcp_server.tools import get_health_warnings
            result = _run(get_health_warnings)
        assert len(result) == 1
        assert result[0].type == 'failure_spike'
        assert result[0].severity == 'high'

    def test_no_warnings(self):
        with patch('proactive.health_monitor.HealthMonitor') as mock_hm_cls, \
             patch('repositories.repository_factory.get_pool'):
            mock_hm_cls.return_value.run_checks.return_value = []
            from mcp_server.tools import get_health_warnings
            result = _run(get_health_warnings)
        assert result == []


# ---------------------------------------------------------------------------
# get_conforma_report
# ---------------------------------------------------------------------------

class TestGetConformaReport:

    def test_reporter_path(self):
        with patch('cli.config.app_to_reporter_branch', return_value='rhoai-v3.5'), \
             patch('clients.conforma_reporter_client.fetch_reporter_violations', return_value=[
                 {'component': 'c1', 'unique_violations': 2},
                 {'component': 'c2', 'unique_violations': 1},
             ]):
            from mcp_server.tools import get_conforma_report
            result = _run(get_conforma_report, application='app',
                          reporter_env='stage', reporter_build_type='nightly')
        assert result['source'] == 'stage/nightly'
        assert result['total_violations'] == 2

    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._db_connection')
    def test_db_path(self, mock_db, mock_conforma):
        mock_conforma.return_value.find_unresolved_component_names.return_value = ['comp-a']
        mock_conforma.return_value.get_violation_details.return_value = {
            'component_name': 'comp-a',
            'scenario': 'verify-conforma',
            'violations_count': 3,
            'warnings_count': 0,
            'violation_summary': 'FAIL: hermetic_task.hermetic',
        }

        with patch('conforma.policy_tools.fetch_exceptions_by_policy', return_value={}), \
             patch('conforma.policy_tools.extract_policy_from_scenario', return_value='stage'), \
             patch('conforma.policy_tools.policy_url', return_value='http://p'), \
             patch('conforma.policy_tools.categorize_policy', return_value='Components'), \
             patch('conforma.policy_tools.count_unique_violations', return_value=(1, [
                 {'rule': 'hermetic_task.hermetic', 'detail': ''}
             ])), \
             patch('conforma.policy_tools.enrich_with_coverage'):
            from mcp_server.tools import get_conforma_report
            result = _run(get_conforma_report, application='app')
        assert result['total_violations'] == 1
        assert 'Components' in result['groups']


# ---------------------------------------------------------------------------
# lookup_image
# ---------------------------------------------------------------------------

class TestLookupImage:

    @patch('mcp_server.tools._build_repo')
    def test_db_match(self, mock_build):
        mock_build.return_value.find_by_image.return_value = [
            {'component': 'comp-a', 'image': 'quay.io/org/comp-a@sha256:abc'},
        ]
        with patch('clients.kubernetes.KubernetesClient') as mock_kc_cls:
            mock_kc_cls.return_value.list_components.return_value = []
            from mcp_server.tools import lookup_image
            result = _run(lookup_image, 'quay.io/org/comp-a@sha256:abc')
        assert result['total_matches'] >= 1
        assert len(result['db_matches']) == 1

    @patch('mcp_server.tools._build_repo')
    def test_no_match(self, mock_build):
        mock_build.return_value.find_by_image.return_value = []
        with patch('clients.kubernetes.KubernetesClient') as mock_kc_cls:
            mock_kc_cls.return_value.list_components.return_value = []
            from mcp_server.tools import lookup_image
            result = _run(lookup_image, 'sha256:nonexistent')
        assert result['total_matches'] == 0

    @patch('mcp_server.tools._build_repo')
    def test_cluster_match(self, mock_build):
        mock_build.return_value.find_by_image.return_value = []
        with patch('clients.kubernetes.KubernetesClient') as mock_kc_cls:
            mock_kc_cls.return_value.list_components.return_value = [
                {'name': 'comp-x', 'container_image': 'quay.io/org/comp-x@sha256:abc123'},
            ]
            from mcp_server.tools import lookup_image
            result = _run(lookup_image, 'sha256:abc123')
        assert result['total_matches'] >= 1

    @patch('mcp_server.tools._build_repo')
    def test_url_component_extraction(self, mock_build):
        mock_build.return_value.find_by_image.return_value = []
        with patch('clients.kubernetes.KubernetesClient') as mock_kc_cls:
            mock_kc_cls.return_value.list_components.return_value = [
                {'name': 'comp-y', 'container_image': 'quay.io/org/comp-y@sha256:def'},
            ]
            from mcp_server.tools import lookup_image
            result = _run(lookup_image, 'quay.io/org/comp-y@sha256:def')
        assert result['total_matches'] >= 1


# ---------------------------------------------------------------------------
# get_snapshot_freshness
# ---------------------------------------------------------------------------

class TestGetSnapshotFreshness:

    def test_delegates(self):
        with patch('proactive.health_monitor.HealthMonitor') as mock_hm_cls:
            mock_hm_cls.return_value.check_snapshot_freshness.return_value = {
                'stale_count': 2, 'fresh_count': 98,
            }
            from mcp_server.tools import get_snapshot_freshness
            result = _run(get_snapshot_freshness, application='app')
        assert result['stale_count'] == 2


# ---------------------------------------------------------------------------
# get_stale_components
# ---------------------------------------------------------------------------

class TestGetStaleComponents:

    def test_delegates(self):
        with patch('proactive.health_monitor.HealthMonitor') as mock_hm_cls:
            mock_hm_cls.return_value.get_stale_components.return_value = {
                'stale': ['comp-a'], 'total_checked': 50,
            }
            from mcp_server.tools import get_stale_components
            result = _run(get_stale_components, application='app')
        assert result['stale'] == ['comp-a']


# ---------------------------------------------------------------------------
# get_nightly_status
# ---------------------------------------------------------------------------

class TestGetNightlyStatus:

    def test_delegates(self):
        with patch('config.CollectorConfig') as mock_cfg_cls, \
             patch('repositories.connection.DatabaseConnection') as mock_db_cls, \
             patch('proactive.health_monitor.HealthMonitor') as mock_hm_cls:
            mock_cfg_cls.from_env.return_value.db = MagicMock()
            mock_hm_cls.return_value.get_nightly_status.return_value = {
                'fbc_healthy': True, 'blockers': [],
            }
            from mcp_server.tools import get_nightly_status
            result = _run(get_nightly_status, application='app')
        assert result['fbc_healthy'] is True


# ---------------------------------------------------------------------------
# get_nightly_history
# ---------------------------------------------------------------------------

class TestGetNightlyHistory:

    def test_delegates(self):
        with patch('config.CollectorConfig') as mock_cfg_cls, \
             patch('repositories.connection.DatabaseConnection') as mock_db_cls, \
             patch('repositories.build_failure_repository.BuildFailureRepository') as mock_repo_cls:
            mock_cfg_cls.from_env.return_value.db = MagicMock()
            mock_repo_cls.return_value.get_nightly_history.return_value = {
                'days': 14, 'builds': [],
            }
            from mcp_server.tools import get_nightly_history
            result = get_nightly_history(application='app', days=7)
        mock_repo_cls.return_value.get_nightly_history.assert_called_once_with('app', days=7)


# ---------------------------------------------------------------------------
# add_watch_application / remove_watch_application
# ---------------------------------------------------------------------------

class TestWatchApplicationTools:

    def test_add(self):
        with patch('config.CollectorConfig') as mock_cfg_cls, \
             patch('repositories.config_repository.ConfigRepository') as mock_repo_cls, \
             patch('repositories.connection.DatabaseConnection'):
            mock_cfg_cls.from_env.return_value.db = MagicMock()
            mock_repo_cls.return_value.add_watched_application.return_value = [
                'rhoai-v3-5', 'rhoai-v3-6',
            ]
            from mcp_server.tools import add_watch_application
            result = add_watch_application(application='rhoai-v3-6')
        assert 'rhoai-v3-6' in result['applications']
        mock_repo_cls.return_value.add_watched_application.assert_called_once_with(
            'rhoai-v3-6', updated_by='mcp',
        )

    def test_remove(self):
        with patch('config.CollectorConfig') as mock_cfg_cls, \
             patch('repositories.config_repository.ConfigRepository') as mock_repo_cls, \
             patch('repositories.connection.DatabaseConnection'):
            mock_cfg_cls.from_env.return_value.db = MagicMock()
            mock_repo_cls.return_value.remove_watched_application.return_value = ['rhoai-v3-5']
            from mcp_server.tools import remove_watch_application
            result = remove_watch_application(application='rhoai-v3-6')
        assert result['applications'] == ['rhoai-v3-5']
        mock_repo_cls.return_value.remove_watched_application.assert_called_once_with(
            'rhoai-v3-6', updated_by='mcp',
        )


# ---------------------------------------------------------------------------
# get_skill_info
# ---------------------------------------------------------------------------

class TestGetSkillInfo:

    @patch('mcp_server.tools._skill_registry')
    def test_found(self, mock_registry):
        mock_entry = MagicMock()
        mock_entry.qualified_name = 'aiops-infra/fix-hermetic'
        mock_entry.name = 'fix-hermetic'
        mock_entry.source = 'aiops-infra'
        mock_entry.metadata.description = 'Fix hermetic task'
        mock_entry.status = 'active'
        mock_entry.tags = ['conforma']
        mock_entry.metadata.category = 'fix'
        mock_entry.metadata.allowed_tools = 'bash'
        mock_entry.metadata.user_invocable = True
        mock_registry.return_value.get_skill.return_value = mock_entry
        from mcp_server.tools import get_skill_info
        result = _run(get_skill_info, 'fix-hermetic')
        assert result.qualified_name == 'aiops-infra/fix-hermetic'

    @patch('mcp_server.tools._skill_registry')
    def test_not_found(self, mock_registry):
        mock_registry.return_value.get_skill.return_value = None
        from mcp_server.tools import get_skill_info
        result = _run(get_skill_info, 'nonexistent')
        assert result is None


# ---------------------------------------------------------------------------
# list_skill_sources
# ---------------------------------------------------------------------------

class TestListSkillSources:

    @patch('mcp_server.tools._skill_registry')
    def test_returns_sources(self, mock_registry):
        mock_source = MagicMock()
        mock_source.name = 'aiops-infra'
        mock_source.url = 'https://github.com/org/aiops-infra'
        mock_source.commit = 'abc123'
        mock_source.branch = 'main'

        mock_skill = MagicMock()
        mock_skill.source = 'aiops-infra'

        mock_registry.return_value.list_sources.return_value = [mock_source]
        mock_registry.return_value.list_skills.return_value = [mock_skill, mock_skill]

        from mcp_server.tools import list_skill_sources
        result = _run(list_skill_sources)
        assert len(result) == 1
        assert result[0].name == 'aiops-infra'
        assert result[0].skill_count == 2
        assert result[0].commit == 'abc123'

    @patch('mcp_server.tools._skill_registry')
    def test_empty(self, mock_registry):
        mock_registry.return_value.list_sources.return_value = []
        mock_registry.return_value.list_skills.return_value = []
        from mcp_server.tools import list_skill_sources
        result = _run(list_skill_sources)
        assert result == []


# ---------------------------------------------------------------------------
# validate_skill
# ---------------------------------------------------------------------------

class TestValidateSkill:

    @patch('mcp_server.tools._skill_registry')
    def test_skill_not_found(self, mock_registry):
        mock_registry.return_value.get_skill.return_value = None
        from mcp_server.tools import validate_skill
        result = _run(validate_skill, 'ghost')
        assert result.passed is False
        assert result.critical_count == 1
        assert result.findings[0].check == 'not_found'

    @patch('mcp_server.tools._skill_registry')
    def test_skill_passes_validation(self, mock_registry):
        mock_skill = MagicMock()
        mock_skill.path = '/tmp/skill'
        mock_skill.metadata = MagicMock()
        mock_registry.return_value.get_skill.return_value = mock_skill

        mock_result = MagicMock()
        mock_result.skill_name = 'test-skill'
        mock_result.passed = True
        mock_result.critical_count = 0
        mock_result.warning_count = 0
        mock_result.findings = []

        with patch('skills.validator.SkillValidator') as mock_validator_cls:
            mock_validator_cls.return_value.validate.return_value = mock_result
            from mcp_server.tools import validate_skill
            result = _run(validate_skill, 'test-skill')
        assert result.passed is True
        assert result.findings == []

    @patch('mcp_server.tools._skill_registry')
    def test_skill_with_findings(self, mock_registry):
        mock_skill = MagicMock()
        mock_skill.path = '/tmp/skill'
        mock_skill.metadata = MagicMock()
        mock_registry.return_value.get_skill.return_value = mock_skill

        mock_finding = MagicMock()
        mock_finding.severity = 'critical'
        mock_finding.check = 'hardcoded_secret'
        mock_finding.message = 'Found secret'
        mock_finding.file = 'run.sh'
        mock_finding.line = 10

        mock_result = MagicMock()
        mock_result.skill_name = 'risky-skill'
        mock_result.passed = False
        mock_result.critical_count = 1
        mock_result.warning_count = 0
        mock_result.findings = [mock_finding]

        with patch('skills.validator.SkillValidator') as mock_validator_cls:
            mock_validator_cls.return_value.validate.return_value = mock_result
            from mcp_server.tools import validate_skill
            result = _run(validate_skill, 'risky-skill')
        assert result.passed is False
        assert result.critical_count == 1
        assert result.findings[0].severity == 'critical'


# ---------------------------------------------------------------------------
# check_skill_prerequisites
# ---------------------------------------------------------------------------

class TestCheckSkillPrerequisites:

    @patch('mcp_server.tools._skill_registry')
    def test_single_skill_not_found(self, mock_registry):
        mock_registry.return_value.get_skill.return_value = None
        from mcp_server.tools import check_skill_prerequisites
        result = _run(check_skill_prerequisites, name='ghost')
        assert len(result) == 1
        assert result[0].status == 'fail'

    @patch('mcp_server.tools._skill_registry')
    def test_single_skill_pass(self, mock_registry):
        mock_skill = MagicMock()
        mock_skill.qualified_name = 'src/test-skill'
        mock_skill.metadata = MagicMock()
        mock_registry.return_value.get_skill.return_value = mock_skill

        with patch('skills.validator.check_prerequisites', return_value={
            'status': 'pass', 'tools': {'bash': True}, 'env': {},
        }):
            from mcp_server.tools import check_skill_prerequisites
            result = _run(check_skill_prerequisites, name='test-skill')
        assert len(result) == 1
        assert result[0].status == 'pass'

    @patch('mcp_server.tools._skill_registry')
    def test_all_skills(self, mock_registry):
        mock_skill = MagicMock()
        mock_skill.qualified_name = 'src/s1'
        mock_skill.metadata = MagicMock()
        mock_registry.return_value.list_skills.return_value = [mock_skill]

        with patch('skills.validator.check_prerequisites', return_value={
            'status': 'pass', 'tools': {}, 'env': {},
        }):
            from mcp_server.tools import check_skill_prerequisites
            result = _run(check_skill_prerequisites)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# run_skill
# ---------------------------------------------------------------------------

class TestRunSkill:

    @patch('mcp_server.tools._skill_registry')
    def test_not_found(self, mock_registry):
        mock_registry.return_value.get_skill.return_value = None
        mock_registry.return_value.list_skills.return_value = []
        from mcp_server.tools import run_skill
        result = run_skill(name='ghost')
        assert 'error' in result

    @patch('mcp_server.tools._skill_registry')
    def test_dry_run(self, mock_registry):
        mock_entry = MagicMock()
        mock_entry.name = 'test-skill'
        mock_registry.return_value.get_skill.return_value = mock_entry

        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            'status': 'dry_run', 'steps': [],
        }

        mock_assessment = MagicMock()
        mock_assessment.reasons = ['read-only']
        mock_assessment.security_warnings = []

        with patch('skills.executor.SkillExecutor') as mock_exec_cls:
            mock_exec_cls.return_value.assess.return_value = mock_assessment
            mock_exec_cls.return_value.execute.return_value = mock_result
            from mcp_server.tools import run_skill
            result = run_skill(name='test-skill', dry_run=True)
        assert result['status'] == 'dry_run'
        assert result['risk_reasons'] == ['read-only']

    @patch('mcp_server.tools._skill_registry')
    def test_fallback_to_list(self, mock_registry):
        """When get_skill returns None, run_skill tries list_skills fallback."""
        mock_entry = MagicMock()
        mock_entry.name = 'fix-hermetic'
        mock_registry.return_value.get_skill.return_value = None
        mock_registry.return_value.list_skills.return_value = [mock_entry]

        mock_result = MagicMock()
        mock_result.to_dict.return_value = {'status': 'ok'}

        mock_assessment = MagicMock()
        mock_assessment.reasons = []
        mock_assessment.security_warnings = []

        with patch('skills.executor.SkillExecutor') as mock_exec_cls:
            mock_exec_cls.return_value.assess.return_value = mock_assessment
            mock_exec_cls.return_value.execute.return_value = mock_result
            from mcp_server.tools import run_skill
            result = run_skill(name='fix-hermetic')
        assert result['status'] == 'ok'


# ---------------------------------------------------------------------------
# get_ec_policy_summary
# ---------------------------------------------------------------------------

class TestGetEcPolicySummary:

    def test_basic(self):
        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls, \
             patch.dict(os.environ, {'RELENG_NAMESPACE': ''}, clear=False):
            mock_kc_cls.return_value.get_ec_policies.return_value = [
                {'metadata': {'name': 'rhoai-stage'}},
            ]
            mock_kc_cls.return_value.extract_exceptions.return_value = [
                {'value': 'hermetic_task.hermetic', 'days_left': 5},
                {'value': 'trusted_task.trusted', 'days_left': None},
                {'value': 'old_rule', 'days_left': -3},
            ]
            from mcp_server.tools import get_ec_policy_summary
            result = _run(get_ec_policy_summary, application='app')
        assert result['policies_count'] == 1
        assert result['total_exceptions'] == 3
        assert result['active_exceptions'] == 2
        assert result['expired_exceptions'] == 1
        assert result['expiring_within_30d'] == 1

    def test_with_releng_namespace(self):
        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls, \
             patch.dict(os.environ, {'RELENG_NAMESPACE': 'releng-ns'}, clear=False):
            mock_kc_cls.return_value.get_ec_policies.return_value = []
            mock_kc_cls.return_value.extract_exceptions.return_value = []
            mock_kc_cls.return_value.get_release_plan_admissions.return_value = []
            mock_kc_cls.extract_rpa_bindings.return_value = []
            from mcp_server.tools import get_ec_policy_summary
            result = _run(get_ec_policy_summary, application='app')
        assert result['total_exceptions'] == 0


# ---------------------------------------------------------------------------
# get_scenario_coverage
# ---------------------------------------------------------------------------

class TestGetScenarioCoverage:

    def test_full_coverage(self):
        scenario_meta = [
            {
                'name': 'verify-conforma', 'application': 'app-1',
                'is_disabled': False, 'is_conforma': True, 'is_future': False,
                'policy_ref': 'policy-1',
            },
            {
                'name': 'e2e-test', 'application': 'app-1',
                'is_disabled': False, 'is_conforma': False, 'is_future': False,
                'policy_ref': '',
            },
        ]
        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls:
            mock_kc_cls.return_value.get_integration_test_scenarios.return_value = [
                {'metadata': {'name': 's1'}},
                {'metadata': {'name': 's2'}},
            ]
            mock_kc_cls.extract_its_metadata.side_effect = scenario_meta
            from mcp_server.tools import get_scenario_coverage
            result = _run(get_scenario_coverage, application='app-1')
        assert result['total_scenarios'] == 2
        assert result['gaps'] == []

    def test_missing_conforma(self):
        scenario_meta = [
            {
                'name': 'e2e-test', 'application': 'app-1',
                'is_disabled': False, 'is_conforma': False, 'is_future': False,
                'policy_ref': '',
            },
        ]
        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls:
            mock_kc_cls.return_value.get_integration_test_scenarios.return_value = [
                {'metadata': {'name': 's1'}},
            ]
            mock_kc_cls.extract_its_metadata.side_effect = scenario_meta
            from mcp_server.tools import get_scenario_coverage
            result = _run(get_scenario_coverage, application='app-1')
        assert len(result['gaps']) == 1
        assert result['gaps'][0]['issue'] == 'no_active_conforma_scenario'


# ---------------------------------------------------------------------------
# get_resolved_patterns
# ---------------------------------------------------------------------------

class TestGetResolvedPatterns:

    @patch('mcp_server.tools._db_connection')
    def test_basic(self, mock_db):
        mock_cursor = MagicMock()
        # First query: rule group results
        mock_cursor.fetchall.return_value = [
            ('hermetic_task', 10, 5, 24.5, _now() - timedelta(days=30), _now()),
            ('trusted_task', 5, 2, 12.0, _now() - timedelta(days=20), _now()),
        ]
        # Second query: totals
        mock_cursor.fetchone.return_value = (15, 7)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_ctx = MagicMock()
        mock_conn_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn_ctx.__exit__ = MagicMock(return_value=False)
        mock_db.return_value.connection.return_value = mock_conn_ctx

        from mcp_server.tools import get_resolved_patterns
        result = get_resolved_patterns(application='app', days=90)
        assert result['total_resolved'] == 15
        assert result['with_ai_analysis'] == 7
        assert len(result['patterns']) == 2
        # trusted_task should be transient
        trusted = [p for p in result['patterns'] if p['rule_group'] == 'trusted_task'][0]
        assert trusted['likely_transient'] is True
        # hermetic_task should not be transient
        hermetic = [p for p in result['patterns'] if p['rule_group'] == 'hermetic_task'][0]
        assert hermetic['likely_transient'] is False


# ---------------------------------------------------------------------------
# get_config_analysis
# ---------------------------------------------------------------------------

class TestGetConfigAnalysis:

    @patch('mcp_server.tools._db_connection')
    def test_no_llm_configured(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg_cls:
            mock_cfg_cls.from_env.return_value.llm = None
            from mcp_server.tools import get_config_analysis
            result = _run(get_config_analysis, application='app')
        assert 'error' in result
        assert 'LLM' in result['error']

    @patch('mcp_server.tools._db_connection')
    def test_analysis_success(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg_cls, \
             patch('analyzers.config_analyzer.ConfigAnalyzer') as mock_analyzer_cls:
            mock_cfg_cls.from_env.return_value.llm = MagicMock()
            mock_analyzer_cls.return_value.run.return_value = {
                'analyzed': True,
                'analysis': {
                    'findings': [{'severity': 'high', 'message': 'gap'}],
                    'overall_severity': 'high',
                    'confidence_score': 0.8,
                    'summary': 'Issues found',
                    'auto_rebuild_candidates': ['comp-a'],
                },
                'cost_usd': 0.05,
                'model': 'claude',
            }
            from mcp_server.tools import get_config_analysis
            result = _run(get_config_analysis, application='app')
        assert result['findings_count'] == 1
        assert result['overall_severity'] == 'high'

    @patch('mcp_server.tools._db_connection')
    def test_analysis_failure(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg_cls, \
             patch('analyzers.config_analyzer.ConfigAnalyzer') as mock_analyzer_cls:
            mock_cfg_cls.from_env.return_value.llm = MagicMock()
            mock_analyzer_cls.return_value.run.return_value = {'analyzed': False}
            from mcp_server.tools import get_config_analysis
            result = _run(get_config_analysis, application='app')
        assert 'error' in result


# ---------------------------------------------------------------------------
# get_build_config_analysis
# ---------------------------------------------------------------------------

class TestGetBuildConfigAnalysis:

    @patch('mcp_server.tools._db_connection')
    def test_no_llm(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg_cls:
            mock_cfg_cls.from_env.return_value.llm = None
            from mcp_server.tools import get_build_config_analysis
            result = _run(get_build_config_analysis, application='app')
        assert 'error' in result

    @patch('mcp_server.tools._db_connection')
    def test_success(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg_cls, \
             patch('analyzers.build_config_analyzer.BuildConfigAnalyzer') as mock_cls:
            mock_cfg_cls.from_env.return_value.llm = MagicMock()
            mock_cls.return_value.run.return_value = {
                'analyzed': True,
                'analysis': {
                    'findings': [],
                    'overall_severity': 'low',
                    'confidence_score': 0.9,
                    'summary': 'All good',
                    'auto_rebuild_candidates': [],
                },
                'cost_usd': 0.03,
                'model': 'claude',
            }
            from mcp_server.tools import get_build_config_analysis
            result = _run(get_build_config_analysis, application='app')
        assert result['findings_count'] == 0
        assert result['overall_severity'] == 'low'


# ---------------------------------------------------------------------------
# get_release_config_analysis
# ---------------------------------------------------------------------------

class TestGetReleaseConfigAnalysis:

    @patch('mcp_server.tools._db_connection')
    def test_no_llm(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg_cls:
            mock_cfg_cls.from_env.return_value.llm = None
            from mcp_server.tools import get_release_config_analysis
            result = _run(get_release_config_analysis, application='app')
        assert 'error' in result

    @patch('mcp_server.tools._db_connection')
    def test_success(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg_cls, \
             patch('analyzers.release_config_analyzer.ReleaseConfigAnalyzer') as mock_cls:
            mock_cfg_cls.from_env.return_value.llm = MagicMock()
            mock_cls.return_value.run.return_value = {
                'analyzed': True,
                'analysis': {
                    'findings': [{'severity': 'medium'}],
                    'overall_severity': 'medium',
                    'confidence_score': 0.7,
                    'summary': 'Some issues',
                    'release_blockers': ['conforma'],
                },
                'cost_usd': 0.04,
                'model': 'claude',
            }
            from mcp_server.tools import get_release_config_analysis
            result = _run(get_release_config_analysis, application='app')
        assert result['findings_count'] == 1
        assert result['release_blockers'] == ['conforma']


# ---------------------------------------------------------------------------
# get_regression_report
# ---------------------------------------------------------------------------

class TestGetRegressionReport:

    @patch('mcp_server.tools._db_connection')
    def test_conforma_domain(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg_cls, \
             patch('analyzers.conforma_regression.ConformaRegressionTester') as mock_cls:
            mock_cfg_cls.from_env.return_value = MagicMock()
            mock_cls.return_value.run.return_value = {
                'accuracy': 0.85, 'total': 50,
            }
            from mcp_server.tools import get_regression_report
            result = _run(get_regression_report, domain='conforma',
                          application='app', limit=50)
        assert result['accuracy'] == 0.85

    @patch('mcp_server.tools._db_connection')
    def test_build_domain(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg_cls, \
             patch('analyzers.build_regression.BuildRegressionTester') as mock_cls:
            mock_cfg_cls.from_env.return_value = MagicMock()
            mock_cls.return_value.run.return_value = {
                'accuracy': 0.7, 'total': 30,
            }
            from mcp_server.tools import get_regression_report
            result = _run(get_regression_report, domain='build',
                          application='app', limit=30)
        assert result['accuracy'] == 0.7

    @patch('mcp_server.tools._db_connection')
    def test_release_domain(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg_cls, \
             patch('analyzers.release_regression.ReleaseRegressionTester') as mock_cls:
            mock_cfg_cls.from_env.return_value = MagicMock()
            mock_cls.return_value.run.return_value = {
                'accuracy': 0.9, 'total': 10,
            }
            from mcp_server.tools import get_regression_report
            result = _run(get_regression_report, domain='release', limit=10)
        assert result['accuracy'] == 0.9


# ---------------------------------------------------------------------------
# trigger_rebuild
# ---------------------------------------------------------------------------

class TestTriggerRebuild:

    def test_component_not_found(self):
        with patch('clients.kubernetes.KubernetesClient') as mock_kc_cls:
            mock_kc_cls.return_value.get_component_metadata.return_value = None
            from mcp_server.tools import trigger_rebuild
            result = _run(trigger_rebuild, 'ghost')
        assert result['success'] is False
        assert 'not found' in result['error']

    def test_success(self):
        with patch('clients.kubernetes.KubernetesClient') as mock_kc_cls:
            mock_kc_cls.return_value.get_component_metadata.return_value = {
                'repository_url': 'https://github.com/org/repo',
                'branch': 'rhoai-v3.5',
            }
            mock_kc_cls.return_value.trigger_rebuild.return_value = None
            from mcp_server.tools import trigger_rebuild
            result = _run(trigger_rebuild, 'comp-a')
        assert result['success'] is True
        assert result['component'] == 'comp-a'

    def test_trigger_fails(self):
        with patch('clients.kubernetes.KubernetesClient') as mock_kc_cls:
            mock_kc_cls.return_value.get_component_metadata.return_value = {
                'repository_url': 'https://github.com/org/repo',
                'branch': 'main',
            }
            mock_kc_cls.return_value.trigger_rebuild.side_effect = Exception('API error')
            from mcp_server.tools import trigger_rebuild
            result = _run(trigger_rebuild, 'comp-b')
        assert result['success'] is False
        assert 'API error' in result['error']
        assert 'manual_command' in result

    def test_custom_namespace(self):
        with patch('clients.kubernetes.KubernetesClient') as mock_kc_cls:
            mock_kc_cls.return_value.get_component_metadata.return_value = {
                'repository_url': 'https://github.com/org/repo',
                'branch': 'main',
            }
            mock_kc_cls.return_value.trigger_rebuild.return_value = None
            from mcp_server.tools import trigger_rebuild
            result = _run(trigger_rebuild, 'comp-c', namespace='custom-ns')
        assert result['success'] is True
        assert result['namespace'] == 'custom-ns'


# ---------------------------------------------------------------------------
# get_release_readiness
# ---------------------------------------------------------------------------

class TestGetReleaseReadiness:

    @patch('mcp_server.tools.get_release_schedule')
    @patch('mcp_server.tools.get_active_freeze')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_ready(self, mock_build, mock_conforma, mock_freeze, mock_schedule):
        mock_build.return_value.find_failing_component_names.return_value = set()
        mock_conforma.return_value.find_unresolved_component_names.return_value = []
        mock_freeze.__wrapped__ = MagicMock(return_value=None)
        mock_schedule.__wrapped__ = MagicMock(return_value=None)

        with patch('api.routes.releases._run_readiness_checks', return_value=[]):
            from mcp_server.tools import get_release_readiness
            result = _run(get_release_readiness, application='app')
        assert result['verdict'] == 'READY'
        assert result['blockers'] == []

    @patch('mcp_server.tools.get_release_schedule')
    @patch('mcp_server.tools.get_active_freeze')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_not_ready_conforma(self, mock_build, mock_conforma, mock_freeze, mock_schedule):
        mock_build.return_value.find_failing_component_names.return_value = set()
        mock_conforma.return_value.find_unresolved_component_names.return_value = [
            'comp-a', 'comp-b',
        ]
        mock_freeze.__wrapped__ = MagicMock(return_value=None)
        mock_schedule.__wrapped__ = MagicMock(return_value=None)

        with patch('api.routes.releases._run_readiness_checks', return_value=[]):
            from mcp_server.tools import get_release_readiness
            result = _run(get_release_readiness, application='app')
        assert result['verdict'] == 'NOT_READY'
        assert any('conforma' in b for b in result['blockers'])

    @patch('mcp_server.tools.get_release_schedule')
    @patch('mcp_server.tools.get_active_freeze')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_at_risk_build_failures(self, mock_build, mock_conforma, mock_freeze, mock_schedule):
        mock_build.return_value.find_failing_component_names.return_value = {'comp-x'}
        mock_conforma.return_value.find_unresolved_component_names.return_value = []
        mock_freeze.__wrapped__ = MagicMock(return_value=None)
        mock_schedule.__wrapped__ = MagicMock(return_value=None)

        with patch('api.routes.releases._run_readiness_checks', return_value=[]):
            from mcp_server.tools import get_release_readiness
            result = _run(get_release_readiness, application='app')
        assert result['verdict'] == 'AT_RISK'

    @patch('mcp_server.tools.get_release_schedule')
    @patch('mcp_server.tools.get_active_freeze')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_frozen(self, mock_build, mock_conforma, mock_freeze, mock_schedule):
        mock_build.return_value.find_failing_component_names.return_value = set()
        mock_conforma.return_value.find_unresolved_component_names.return_value = []
        mock_freeze.__wrapped__ = MagicMock(return_value={
            'end_date': '2026-07-15', 'reason': 'RC freeze',
        })
        mock_schedule.__wrapped__ = MagicMock(return_value=None)

        with patch('api.routes.releases._run_readiness_checks', return_value=[]):
            from mcp_server.tools import get_release_readiness
            result = _run(get_release_readiness, application='app')
        assert result['verdict'] == 'NOT_READY'
        assert any('frozen' in b.lower() for b in result['blockers'])

    @patch('mcp_server.tools.get_release_schedule')
    @patch('mcp_server.tools.get_active_freeze')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_readiness_checks_contribute(self, mock_build, mock_conforma, mock_freeze, mock_schedule):
        mock_build.return_value.find_failing_component_names.return_value = set()
        mock_conforma.return_value.find_unresolved_component_names.return_value = []
        mock_freeze.__wrapped__ = MagicMock(return_value=None)
        mock_schedule.__wrapped__ = MagicMock(return_value=None)

        checks = [
            {'name': 'FBC health', 'status': 'FAIL', 'detail': 'FBC stale'},
            {'name': 'PCC cache', 'status': 'WARN', 'detail': 'Stale cache'},
            {'name': 'GHA nightly', 'status': 'PASS', 'detail': 'OK'},
        ]
        with patch('api.routes.releases._run_readiness_checks', return_value=checks):
            from mcp_server.tools import get_release_readiness
            result = _run(get_release_readiness, application='app')
        assert result['verdict'] == 'NOT_READY'
        assert any('FBC' in b for b in result['blockers'])
        assert any('PCC' in r for r in result['risks'])


# ---------------------------------------------------------------------------
# get_onboarding_status
# ---------------------------------------------------------------------------

class TestGetOnboardingStatus:

    def test_delegates(self):
        mock_result = {'application': 'app', 'components': []}
        with patch('api.routes.onboarding.get_onboarding_status', return_value=mock_result) as mock_fn:
            from mcp_server.tools import get_onboarding_status
            result = _run(get_onboarding_status, application='app')
        mock_fn.assert_called_once_with('app')
        assert result['application'] == 'app'


# ---------------------------------------------------------------------------
# get_onboarding_describe
# ---------------------------------------------------------------------------

class TestGetOnboardingDescribe:

    def test_no_component(self):
        from mcp_server.tools import get_onboarding_describe
        result = _run(get_onboarding_describe, component='')
        assert 'error' in result

    def test_delegates(self):
        mock_result = {'component': 'comp-a', 'steps': []}
        with patch('api.routes.onboarding.get_component_onboarding', return_value=mock_result) as mock_fn:
            from mcp_server.tools import get_onboarding_describe
            result = _run(get_onboarding_describe, component='comp-a', diff=True)
        mock_fn.assert_called_once()
        assert result['component'] == 'comp-a'


# ---------------------------------------------------------------------------
# analyze_onboarding
# ---------------------------------------------------------------------------

class TestAnalyzeOnboarding:

    def test_no_component(self):
        from mcp_server.tools import analyze_onboarding
        result = _run(analyze_onboarding, component='')
        assert 'error' in result

    def test_delegates(self):
        mock_result = {'component': 'comp-a', 'analysis': {}}
        with patch('api.routes.onboarding.analyze_component_onboarding', return_value=mock_result) as mock_fn:
            from mcp_server.tools import analyze_onboarding
            result = _run(analyze_onboarding, component='comp-a')
        mock_fn.assert_called_once()
        assert result['component'] == 'comp-a'


# ---------------------------------------------------------------------------
# _db_connection helper
# ---------------------------------------------------------------------------

class TestDbConnection:

    def test_creates_connection(self):
        with patch('config.CollectorConfig') as mock_cfg_cls, \
             patch('repositories.connection.DatabaseConnection') as mock_db_cls:
            mock_cfg_cls.from_env.return_value.db = MagicMock()
            from mcp_server.tools import _db_connection
            result = _db_connection()
        mock_db_cls.assert_called_once()


# ---------------------------------------------------------------------------
# _get_repo helper
# ---------------------------------------------------------------------------

class TestGetRepoHelper:

    def test_delegates_to_factory(self):
        with patch('repositories.repository_factory.get_repository', return_value='repo_instance') as mock_get:
            from mcp_server.tools import _get_repo
            result = _get_repo('SomeClass')
        mock_get.assert_called_once_with('SomeClass')
        assert result == 'repo_instance'


# ---------------------------------------------------------------------------
# _entry_to_info helper
# ---------------------------------------------------------------------------

class TestEntryToInfo:

    def test_converts_entry(self):
        mock_entry = MagicMock()
        mock_entry.qualified_name = 'src/test'
        mock_entry.name = 'test'
        mock_entry.source = 'src'
        mock_entry.metadata.description = 'Test skill'
        mock_entry.status = 'active'
        mock_entry.tags = ['tag1']
        mock_entry.metadata.category = 'utility'
        mock_entry.metadata.allowed_tools = 'bash,curl'
        mock_entry.metadata.user_invocable = False

        from mcp_server.tools import _entry_to_info
        result = _entry_to_info(mock_entry)
        assert result.qualified_name == 'src/test'
        assert result.tags == ['tag1']
        assert result.user_invocable is False
        assert result.category == 'utility'


# ---------------------------------------------------------------------------
# _skill_registry helper
# ---------------------------------------------------------------------------

class TestSkillRegistryHelper:

    def test_delegates(self):
        with patch('skills.db_registry.get_registry', return_value='registry_instance') as mock_get:
            from mcp_server.tools import _skill_registry
            result = _skill_registry()
        mock_get.assert_called_once()
        assert result == 'registry_instance'
