"""Comprehensive tests for check_sync_status module."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(applications=None, namespace='test-ns', app_name='test-app'):
    """Build a mock CollectorConfig."""
    cfg = MagicMock()
    cfg.k8s.namespace = namespace
    cfg.k8s.application_name = app_name
    if applications is not None:
        cfg.watcher.applications = applications
    else:
        cfg.watcher.applications = [app_name]
    return cfg


def _make_cluster_result(failing=None, retriggered=None, running=None, details=None):
    """Build a dict matching the shape of get_failing_build_components output."""
    return {
        'failing': failing or set(),
        'retriggered': retriggered or set(),
        'running': running or {},
        'details': details or {},
    }


# ---------------------------------------------------------------------------
# _empty_status tests
# ---------------------------------------------------------------------------

class TestEmptyStatus:
    """Tests for _empty_status helper."""

    def test_returns_dict(self):
        from check_sync_status import _empty_status
        result = _empty_status()
        assert isinstance(result, dict)

    def test_all_expected_keys_present(self):
        from check_sync_status import _empty_status
        result = _empty_status()
        expected_keys = {
            'cluster_connected', 'in_sync', 'cluster_components',
            'db_components', 'missing_in_db', 'extra_in_db',
            'retriggered_components', 'running_builds', 'error',
        }
        assert set(result.keys()) == expected_keys

    def test_default_values(self):
        from check_sync_status import _empty_status
        result = _empty_status()
        assert result['cluster_connected'] is False
        assert result['in_sync'] is False
        assert result['cluster_components'] == []
        assert result['db_components'] == []
        assert result['missing_in_db'] == []
        assert result['extra_in_db'] == []
        assert result['retriggered_components'] == []
        assert result['running_builds'] == []
        assert result['error'] is None

    def test_returns_new_dict_each_call(self):
        from check_sync_status import _empty_status
        a = _empty_status()
        b = _empty_status()
        assert a is not b
        a['error'] = 'mutated'
        assert b['error'] is None


# ---------------------------------------------------------------------------
# _batch_get_component_metadata tests
# ---------------------------------------------------------------------------

class TestBatchGetComponentMetadata:
    """Tests for _batch_get_component_metadata."""

    def test_successful_fetch(self):
        """Fetching metadata for components returns repo URL and branch."""
        mock_k8s_mod = MagicMock()
        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_k8s_mod.client = mock_client
        mock_k8s_mod.config = mock_config

        api_instance = MagicMock()
        mock_client.CustomObjectsApi.return_value = api_instance
        api_instance.get_namespaced_custom_object.return_value = {
            'spec': {
                'source': {
                    'git': {
                        'url': 'https://github.com/org/repo',
                        'revision': 'main',
                    }
                }
            }
        }

        with patch.dict('sys.modules', {
            'kubernetes': mock_k8s_mod,
            'kubernetes.client': mock_client,
            'kubernetes.config': mock_config,
        }):
            from check_sync_status import _batch_get_component_metadata
            result = _batch_get_component_metadata(['comp-a'], 'my-ns')

        assert result == {
            'comp-a': {
                'repository_url': 'https://github.com/org/repo',
                'branch': 'main',
            }
        }
        mock_config.load_kube_config.assert_called_once()
        api_instance.get_namespaced_custom_object.assert_called_once_with(
            group='appstudio.redhat.com', version='v1alpha1',
            namespace='my-ns', plural='components', name='comp-a',
        )

    def test_component_not_found_exception_caught(self):
        """When one component raises, it is skipped and others still succeed."""
        mock_k8s_mod = MagicMock()
        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_k8s_mod.client = mock_client
        mock_k8s_mod.config = mock_config

        api_instance = MagicMock()
        mock_client.CustomObjectsApi.return_value = api_instance

        def side_effect(group, version, namespace, plural, name):
            if name == 'comp-bad':
                raise Exception("404 Not Found")
            return {'spec': {'source': {'git': {'url': 'https://u', 'revision': 'b'}}}}

        api_instance.get_namespaced_custom_object.side_effect = side_effect

        with patch.dict('sys.modules', {
            'kubernetes': mock_k8s_mod,
            'kubernetes.client': mock_client,
            'kubernetes.config': mock_config,
        }):
            from check_sync_status import _batch_get_component_metadata
            result = _batch_get_component_metadata(['comp-bad', 'comp-ok'], 'ns')

        assert 'comp-bad' not in result
        assert result['comp-ok'] == {'repository_url': 'https://u', 'branch': 'b'}

    def test_kubernetes_import_fails(self):
        """When the kubernetes package is not installed, returns empty dict."""
        import check_sync_status as mod

        # Remove kubernetes modules from sys.modules so the import inside the
        # function fails with a real ImportError.
        saved = {}
        for key in list(sys.modules):
            if key == 'kubernetes' or key.startswith('kubernetes.'):
                saved[key] = sys.modules.pop(key)

        # Patch the import mechanism so 'from kubernetes import ...' raises.
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def fail_import(name, *args, **kwargs):
            if name == 'kubernetes':
                raise ImportError("No module named 'kubernetes'")
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=fail_import):
            result = mod._batch_get_component_metadata(['comp-x'], 'ns')

        # Restore modules
        sys.modules.update(saved)
        assert result == {}


# ---------------------------------------------------------------------------
# main() tests
# ---------------------------------------------------------------------------

_MAIN_PATCHES = {
    'config': 'check_sync_status.CollectorConfig',
    'db': 'check_sync_status.DatabaseConnection',
    'build_repo': 'check_sync_status.BuildFailureRepository',
    'sync_repo': 'check_sync_status.SyncStatusRepository',
    'is_logged_in': 'check_sync_status.is_logged_in',
    'get_failing': 'check_sync_status.get_failing_build_components',
    'tr_client': 'check_sync_status.TektonResultsClient',
    'batch_meta': 'check_sync_status._batch_get_component_metadata',
}


class TestMainNotLoggedIn:
    """Tests for main() when the user is not logged into OpenShift."""

    @patch(_MAIN_PATCHES['batch_meta'])
    @patch(_MAIN_PATCHES['get_failing'])
    @patch(_MAIN_PATCHES['is_logged_in'], return_value=False)
    @patch(_MAIN_PATCHES['sync_repo'])
    @patch(_MAIN_PATCHES['build_repo'])
    @patch(_MAIN_PATCHES['db'])
    @patch(_MAIN_PATCHES['config'])
    def test_saves_error_status_for_each_app(self, mock_cfg_cls, mock_db_cls,
                                             mock_build_cls, mock_sync_cls,
                                             mock_login, mock_get_fail,
                                             mock_batch, capsys):
        cfg = _make_config(applications=['app-a', 'app-b'])
        mock_cfg_cls.from_env.return_value = cfg
        sync_repo = MagicMock()
        mock_sync_cls.return_value = sync_repo

        with patch('sys.argv', ['check_sync_status.py']):
            from check_sync_status import main
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        assert sync_repo.save_build_sync_status.call_count == 2
        for c in sync_repo.save_build_sync_status.call_args_list:
            status = c[0][1]
            assert status['error'] == 'Not logged into OpenShift cluster'
            assert status['cluster_connected'] is False

    @patch(_MAIN_PATCHES['batch_meta'])
    @patch(_MAIN_PATCHES['get_failing'])
    @patch(_MAIN_PATCHES['is_logged_in'], return_value=False)
    @patch(_MAIN_PATCHES['sync_repo'])
    @patch(_MAIN_PATCHES['build_repo'])
    @patch(_MAIN_PATCHES['db'])
    @patch(_MAIN_PATCHES['config'])
    def test_prints_json_error(self, mock_cfg_cls, mock_db_cls,
                                mock_build_cls, mock_sync_cls,
                                mock_login, mock_get_fail,
                                mock_batch, capsys):
        cfg = _make_config(applications=['app-x'])
        mock_cfg_cls.from_env.return_value = cfg
        mock_sync_cls.return_value = MagicMock()

        with patch('sys.argv', ['check_sync_status.py']):
            from check_sync_status import main
            with pytest.raises(SystemExit):
                main()

        output = json.loads(capsys.readouterr().out)
        assert output['error'] == 'Not logged into OpenShift cluster'
        assert 'app-x' in output['applications']


class TestMainInSync:
    """Tests for main() when cluster and DB are in sync."""

    @patch(_MAIN_PATCHES['batch_meta'])
    @patch(_MAIN_PATCHES['tr_client'])
    @patch(_MAIN_PATCHES['get_failing'])
    @patch(_MAIN_PATCHES['is_logged_in'], return_value=True)
    @patch(_MAIN_PATCHES['sync_repo'])
    @patch(_MAIN_PATCHES['build_repo'])
    @patch(_MAIN_PATCHES['db'])
    @patch(_MAIN_PATCHES['config'])
    def test_no_missing_no_extra(self, mock_cfg_cls, mock_db_cls,
                                  mock_build_cls, mock_sync_cls,
                                  mock_login, mock_get_fail, mock_tr_cls,
                                  mock_batch, capsys):
        cfg = _make_config(applications=['myapp'])
        mock_cfg_cls.from_env.return_value = cfg
        build_repo = MagicMock()
        build_repo.find_failing_component_names.return_value = {'comp-a', 'comp-b'}
        mock_build_cls.return_value = build_repo
        sync_repo = MagicMock()
        mock_sync_cls.return_value = sync_repo

        mock_get_fail.return_value = _make_cluster_result(
            failing={'comp-a', 'comp-b'},
        )

        with patch('sys.argv', ['check_sync_status.py']):
            from check_sync_status import main
            main()

        output = json.loads(capsys.readouterr().out)
        status = output['myapp']
        assert status['in_sync'] is True
        assert status['missing_in_db'] == []
        assert status['extra_in_db'] == []
        build_repo.upsert_failure.assert_not_called()
        build_repo.mark_resolved.assert_not_called()


class TestMainMissingInDb:
    """Tests for main() when components are failing in cluster but missing from DB."""

    @patch(_MAIN_PATCHES['batch_meta'])
    @patch(_MAIN_PATCHES['tr_client'])
    @patch(_MAIN_PATCHES['get_failing'])
    @patch(_MAIN_PATCHES['is_logged_in'], return_value=True)
    @patch(_MAIN_PATCHES['sync_repo'])
    @patch(_MAIN_PATCHES['build_repo'])
    @patch(_MAIN_PATCHES['db'])
    @patch(_MAIN_PATCHES['config'])
    def test_upsert_failure_called(self, mock_cfg_cls, mock_db_cls,
                                    mock_build_cls, mock_sync_cls,
                                    mock_login, mock_get_fail, mock_tr_cls,
                                    mock_batch, capsys):
        cfg = _make_config(applications=['myapp'], namespace='test-ns')
        mock_cfg_cls.from_env.return_value = cfg
        build_repo = MagicMock()
        build_repo.find_failing_component_names.return_value = set()
        mock_build_cls.return_value = build_repo
        sync_repo = MagicMock()
        mock_sync_cls.return_value = sync_repo

        mock_get_fail.return_value = _make_cluster_result(
            failing={'new-comp'},
            details={
                'new-comp': {
                    'pipelinerun_name': 'pr-123',
                    'pipelinerun_uid': 'uid-abc',
                },
            },
        )
        mock_batch.return_value = {
            'new-comp': {
                'repository_url': 'https://github.com/org/new-comp',
                'branch': 'main',
            },
        }

        with patch('sys.argv', ['check_sync_status.py']):
            from check_sync_status import main
            main()

        build_repo.upsert_failure.assert_called_once_with(
            pr_name='pr-123',
            pr_uid='uid-abc',
            component_name='new-comp',
            application='myapp',
            namespace='test-ns',
            repo_url='https://github.com/org/new-comp',
            branch='main',
            status='Failed',
        )

        output = json.loads(capsys.readouterr().out)
        assert 'new-comp' in output['myapp']['missing_in_db']

    @patch(_MAIN_PATCHES['batch_meta'])
    @patch(_MAIN_PATCHES['tr_client'])
    @patch(_MAIN_PATCHES['get_failing'])
    @patch(_MAIN_PATCHES['is_logged_in'], return_value=True)
    @patch(_MAIN_PATCHES['sync_repo'])
    @patch(_MAIN_PATCHES['build_repo'])
    @patch(_MAIN_PATCHES['db'])
    @patch(_MAIN_PATCHES['config'])
    def test_missing_with_no_cluster_details_skips_upsert(self, mock_cfg_cls, mock_db_cls,
                                                           mock_build_cls, mock_sync_cls,
                                                           mock_login, mock_get_fail,
                                                           mock_tr_cls, mock_batch, capsys):
        """When a missing component has no detail entry, upsert is skipped."""
        cfg = _make_config(applications=['myapp'])
        mock_cfg_cls.from_env.return_value = cfg
        build_repo = MagicMock()
        build_repo.find_failing_component_names.return_value = set()
        mock_build_cls.return_value = build_repo
        mock_sync_cls.return_value = MagicMock()

        mock_get_fail.return_value = _make_cluster_result(
            failing={'orphan-comp'},
            details={},  # no details for orphan-comp
        )
        mock_batch.return_value = {}

        with patch('sys.argv', ['check_sync_status.py']):
            from check_sync_status import main
            main()

        build_repo.upsert_failure.assert_not_called()


class TestMainExtraInDb:
    """Tests for main() when DB has components no longer failing in cluster."""

    @patch(_MAIN_PATCHES['batch_meta'])
    @patch(_MAIN_PATCHES['tr_client'])
    @patch(_MAIN_PATCHES['get_failing'])
    @patch(_MAIN_PATCHES['is_logged_in'], return_value=True)
    @patch(_MAIN_PATCHES['sync_repo'])
    @patch(_MAIN_PATCHES['build_repo'])
    @patch(_MAIN_PATCHES['db'])
    @patch(_MAIN_PATCHES['config'])
    def test_tekton_results_check_and_mark_resolved(self, mock_cfg_cls, mock_db_cls,
                                                     mock_build_cls, mock_sync_cls,
                                                     mock_login, mock_get_fail,
                                                     mock_tr_cls, mock_batch, capsys):
        cfg = _make_config(applications=['myapp'], namespace='test-ns')
        mock_cfg_cls.from_env.return_value = cfg
        build_repo = MagicMock()
        build_repo.find_failing_component_names.return_value = {'old-comp'}
        mock_build_cls.return_value = build_repo
        sync_repo = MagicMock()
        mock_sync_cls.return_value = sync_repo

        mock_get_fail.return_value = _make_cluster_result(failing=set())

        tr_instance = MagicMock()
        mock_tr_cls.return_value = tr_instance
        tr_instance.query_component_build_history.return_value = [
            {
                'metadata': {
                    'name': 'pr-resolved-001',
                    'creationTimestamp': '2026-01-02T00:00:00Z',
                    'annotations': {
                        'build.appstudio.redhat.com/commit_sha': 'abc123',
                    },
                },
                'status': {
                    'conditions': [{'status': 'True'}],
                },
            },
        ]

        with patch('sys.argv', ['check_sync_status.py']):
            from check_sync_status import main
            main()

        build_repo.mark_resolved.assert_called_once_with(
            'old-comp', 'myapp', 'test-ns', 'pr-resolved-001',
            resolution_commit_sha='abc123',
        )
        output = json.loads(capsys.readouterr().out)
        assert 'old-comp' in output['myapp']['extra_in_db']

    @patch(_MAIN_PATCHES['batch_meta'])
    @patch(_MAIN_PATCHES['tr_client'])
    @patch(_MAIN_PATCHES['get_failing'])
    @patch(_MAIN_PATCHES['is_logged_in'], return_value=True)
    @patch(_MAIN_PATCHES['sync_repo'])
    @patch(_MAIN_PATCHES['build_repo'])
    @patch(_MAIN_PATCHES['db'])
    @patch(_MAIN_PATCHES['config'])
    def test_extra_comp_removed_when_still_failing_in_tekton(self, mock_cfg_cls,
                                                              mock_db_cls,
                                                              mock_build_cls,
                                                              mock_sync_cls,
                                                              mock_login,
                                                              mock_get_fail,
                                                              mock_tr_cls,
                                                              mock_batch, capsys):
        """When TR shows the newest build is NOT successful, component is
        removed from extra_in_db (should_resolve stays False)."""
        cfg = _make_config(applications=['myapp'])
        mock_cfg_cls.from_env.return_value = cfg
        build_repo = MagicMock()
        build_repo.find_failing_component_names.return_value = {'still-failing'}
        mock_build_cls.return_value = build_repo
        mock_sync_cls.return_value = MagicMock()

        mock_get_fail.return_value = _make_cluster_result(failing=set())

        tr_instance = MagicMock()
        mock_tr_cls.return_value = tr_instance
        tr_instance.query_component_build_history.return_value = [
            {
                'metadata': {
                    'name': 'pr-still-bad',
                    'creationTimestamp': '2026-01-01T00:00:00Z',
                    'annotations': {},
                },
                'status': {
                    'conditions': [{'status': 'False'}],
                },
            },
        ]

        with patch('sys.argv', ['check_sync_status.py']):
            from check_sync_status import main
            main()

        build_repo.mark_resolved.assert_not_called()
        output = json.loads(capsys.readouterr().out)
        # Component removed from extra_in_db because should_resolve was False
        assert 'still-failing' not in output['myapp']['extra_in_db']

    @patch(_MAIN_PATCHES['batch_meta'])
    @patch(_MAIN_PATCHES['tr_client'])
    @patch(_MAIN_PATCHES['get_failing'])
    @patch(_MAIN_PATCHES['is_logged_in'], return_value=True)
    @patch(_MAIN_PATCHES['sync_repo'])
    @patch(_MAIN_PATCHES['build_repo'])
    @patch(_MAIN_PATCHES['db'])
    @patch(_MAIN_PATCHES['config'])
    def test_extra_uses_pipelinesascode_sha_fallback(self, mock_cfg_cls, mock_db_cls,
                                                      mock_build_cls, mock_sync_cls,
                                                      mock_login, mock_get_fail,
                                                      mock_tr_cls, mock_batch, capsys):
        """When build.appstudio commit_sha is absent, falls back to
        pipelinesascode.tekton.dev/sha."""
        cfg = _make_config(applications=['myapp'], namespace='ns')
        mock_cfg_cls.from_env.return_value = cfg
        build_repo = MagicMock()
        build_repo.find_failing_component_names.return_value = {'comp-pac'}
        mock_build_cls.return_value = build_repo
        mock_sync_cls.return_value = MagicMock()

        mock_get_fail.return_value = _make_cluster_result(failing=set())

        tr_instance = MagicMock()
        mock_tr_cls.return_value = tr_instance
        tr_instance.query_component_build_history.return_value = [
            {
                'metadata': {
                    'name': 'pr-pac',
                    'creationTimestamp': '2026-01-01T00:00:00Z',
                    'annotations': {
                        'pipelinesascode.tekton.dev/sha': 'sha-pac-999',
                    },
                },
                'status': {'conditions': [{'status': 'True'}]},
            },
        ]

        with patch('sys.argv', ['check_sync_status.py']):
            from check_sync_status import main
            main()

        build_repo.mark_resolved.assert_called_once_with(
            'comp-pac', 'myapp', 'ns', 'pr-pac',
            resolution_commit_sha='sha-pac-999',
        )


class TestMainRetriggered:
    """Tests for main() when components have been retriggered."""

    @patch(_MAIN_PATCHES['batch_meta'])
    @patch(_MAIN_PATCHES['tr_client'])
    @patch(_MAIN_PATCHES['get_failing'])
    @patch(_MAIN_PATCHES['is_logged_in'], return_value=True)
    @patch(_MAIN_PATCHES['sync_repo'])
    @patch(_MAIN_PATCHES['build_repo'])
    @patch(_MAIN_PATCHES['db'])
    @patch(_MAIN_PATCHES['config'])
    def test_mark_resolved_called_for_retriggered(self, mock_cfg_cls, mock_db_cls,
                                                    mock_build_cls, mock_sync_cls,
                                                    mock_login, mock_get_fail,
                                                    mock_tr_cls, mock_batch, capsys):
        cfg = _make_config(applications=['myapp'], namespace='test-ns')
        mock_cfg_cls.from_env.return_value = cfg
        build_repo = MagicMock()
        build_repo.find_failing_component_names.return_value = {'comp-r'}
        mock_build_cls.return_value = build_repo
        mock_sync_cls.return_value = MagicMock()

        mock_get_fail.return_value = _make_cluster_result(
            failing={'comp-r'},
            retriggered={'comp-r'},
        )

        with patch('sys.argv', ['check_sync_status.py']):
            from check_sync_status import main
            main()

        build_repo.mark_resolved.assert_called_once_with(
            'comp-r', 'myapp', 'test-ns', 'retrigger-resolved',
        )

        output = json.loads(capsys.readouterr().out)
        assert 'comp-r' in output['myapp']['retriggered_components']


class TestMainDbConnectionFailure:
    """Tests for main() when the database connection fails."""

    @patch(_MAIN_PATCHES['batch_meta'])
    @patch(_MAIN_PATCHES['tr_client'])
    @patch(_MAIN_PATCHES['get_failing'])
    @patch(_MAIN_PATCHES['is_logged_in'], return_value=True)
    @patch(_MAIN_PATCHES['sync_repo'])
    @patch(_MAIN_PATCHES['build_repo'])
    @patch(_MAIN_PATCHES['db'])
    @patch(_MAIN_PATCHES['config'])
    def test_find_failing_returns_none(self, mock_cfg_cls, mock_db_cls,
                                       mock_build_cls, mock_sync_cls,
                                       mock_login, mock_get_fail,
                                       mock_tr_cls, mock_batch, capsys):
        cfg = _make_config(applications=['myapp'])
        mock_cfg_cls.from_env.return_value = cfg
        build_repo = MagicMock()
        build_repo.find_failing_component_names.return_value = None
        mock_build_cls.return_value = build_repo
        sync_repo = MagicMock()
        mock_sync_cls.return_value = sync_repo

        with patch('sys.argv', ['check_sync_status.py']):
            from check_sync_status import main
            main()

        # Should save error status and continue to next app
        sync_repo.save_build_sync_status.assert_called_once()
        saved_status = sync_repo.save_build_sync_status.call_args[0][1]
        assert saved_status['error'] == 'Database connection failed'
        assert saved_status['cluster_connected'] is True

        # get_failing_build_components should NOT be called
        mock_get_fail.assert_not_called()

        output = json.loads(capsys.readouterr().out)
        assert output['myapp']['error'] == 'Database connection failed'


class TestMainTektonResultsClientFailure:
    """Tests for main() when TektonResultsClient creation fails."""

    @patch(_MAIN_PATCHES['batch_meta'])
    @patch(_MAIN_PATCHES['tr_client'], side_effect=Exception("TR init failed"))
    @patch(_MAIN_PATCHES['get_failing'])
    @patch(_MAIN_PATCHES['is_logged_in'], return_value=True)
    @patch(_MAIN_PATCHES['sync_repo'])
    @patch(_MAIN_PATCHES['build_repo'])
    @patch(_MAIN_PATCHES['db'])
    @patch(_MAIN_PATCHES['config'])
    def test_tr_none_extra_in_db_removed(self, mock_cfg_cls, mock_db_cls,
                                          mock_build_cls, mock_sync_cls,
                                          mock_login, mock_get_fail,
                                          mock_tr_cls, mock_batch, capsys):
        """When TektonResultsClient fails to create (tr=None), extra_in_db
        components cannot be verified so they are removed from the set."""
        cfg = _make_config(applications=['myapp'])
        mock_cfg_cls.from_env.return_value = cfg
        build_repo = MagicMock()
        build_repo.find_failing_component_names.return_value = {'gone-comp'}
        mock_build_cls.return_value = build_repo
        mock_sync_cls.return_value = MagicMock()

        mock_get_fail.return_value = _make_cluster_result(failing=set())

        with patch('sys.argv', ['check_sync_status.py']):
            from check_sync_status import main
            main()

        # tr is None, so should_resolve stays False, comp removed from extra_in_db
        build_repo.mark_resolved.assert_not_called()
        output = json.loads(capsys.readouterr().out)
        assert output['myapp']['extra_in_db'] == []


class TestMainApplicationsFallback:
    """Tests for main() application list fallback behavior."""

    @patch(_MAIN_PATCHES['batch_meta'])
    @patch(_MAIN_PATCHES['tr_client'])
    @patch(_MAIN_PATCHES['get_failing'])
    @patch(_MAIN_PATCHES['is_logged_in'], return_value=True)
    @patch(_MAIN_PATCHES['sync_repo'])
    @patch(_MAIN_PATCHES['build_repo'])
    @patch(_MAIN_PATCHES['db'])
    @patch(_MAIN_PATCHES['config'])
    def test_empty_applications_uses_k8s_app_name(self, mock_cfg_cls, mock_db_cls,
                                                    mock_build_cls, mock_sync_cls,
                                                    mock_login, mock_get_fail,
                                                    mock_tr_cls, mock_batch, capsys):
        """When config.watcher.applications is empty, falls back to
        config.k8s.application_name."""
        cfg = _make_config(applications=[], app_name='fallback-app')
        mock_cfg_cls.from_env.return_value = cfg
        build_repo = MagicMock()
        build_repo.find_failing_component_names.return_value = set()
        mock_build_cls.return_value = build_repo
        mock_sync_cls.return_value = MagicMock()

        mock_get_fail.return_value = _make_cluster_result()

        with patch('sys.argv', ['check_sync_status.py']):
            from check_sync_status import main
            main()

        output = json.loads(capsys.readouterr().out)
        assert 'fallback-app' in output


class TestMainRunningBuilds:
    """Tests for main() running builds reporting."""

    @patch(_MAIN_PATCHES['batch_meta'])
    @patch(_MAIN_PATCHES['tr_client'])
    @patch(_MAIN_PATCHES['get_failing'])
    @patch(_MAIN_PATCHES['is_logged_in'], return_value=True)
    @patch(_MAIN_PATCHES['sync_repo'])
    @patch(_MAIN_PATCHES['build_repo'])
    @patch(_MAIN_PATCHES['db'])
    @patch(_MAIN_PATCHES['config'])
    def test_running_builds_included_in_status(self, mock_cfg_cls, mock_db_cls,
                                                mock_build_cls, mock_sync_cls,
                                                mock_login, mock_get_fail,
                                                mock_tr_cls, mock_batch, capsys):
        cfg = _make_config(applications=['myapp'])
        mock_cfg_cls.from_env.return_value = cfg
        build_repo = MagicMock()
        build_repo.find_failing_component_names.return_value = set()
        mock_build_cls.return_value = build_repo
        mock_sync_cls.return_value = MagicMock()

        mock_get_fail.return_value = _make_cluster_result(
            running={'comp-running': {'pipelinerun': 'pr-run-1', 'started': '2026-01-01'}},
        )

        with patch('sys.argv', ['check_sync_status.py']):
            from check_sync_status import main
            main()

        output = json.loads(capsys.readouterr().out)
        running = output['myapp']['running_builds']
        assert len(running) == 1
        assert running[0]['component'] == 'comp-running'
        assert running[0]['pipelinerun'] == 'pr-run-1'


class TestMainSyncStatusSaved:
    """Tests verifying sync status is always saved."""

    @patch(_MAIN_PATCHES['batch_meta'])
    @patch(_MAIN_PATCHES['tr_client'])
    @patch(_MAIN_PATCHES['get_failing'])
    @patch(_MAIN_PATCHES['is_logged_in'], return_value=True)
    @patch(_MAIN_PATCHES['sync_repo'])
    @patch(_MAIN_PATCHES['build_repo'])
    @patch(_MAIN_PATCHES['db'])
    @patch(_MAIN_PATCHES['config'])
    def test_sync_status_saved_per_application(self, mock_cfg_cls, mock_db_cls,
                                                mock_build_cls, mock_sync_cls,
                                                mock_login, mock_get_fail,
                                                mock_tr_cls, mock_batch, capsys):
        cfg = _make_config(applications=['app-1', 'app-2'])
        mock_cfg_cls.from_env.return_value = cfg
        build_repo = MagicMock()
        build_repo.find_failing_component_names.return_value = set()
        mock_build_cls.return_value = build_repo
        sync_repo = MagicMock()
        mock_sync_cls.return_value = sync_repo

        mock_get_fail.return_value = _make_cluster_result()

        with patch('sys.argv', ['check_sync_status.py']):
            from check_sync_status import main
            main()

        assert sync_repo.save_build_sync_status.call_count == 2
        saved_apps = [c[0][0] for c in sync_repo.save_build_sync_status.call_args_list]
        assert 'app-1' in saved_apps
        assert 'app-2' in saved_apps
