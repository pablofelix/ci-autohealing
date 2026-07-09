"""Comprehensive tests for enrichment sources: build_history, related_failures, dependency_context."""

import json
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Mock heavy dependencies before importing modules under test.
sys.modules.setdefault('logger', MagicMock(setup_logger=MagicMock(return_value=MagicMock())))
sys.modules.setdefault('repositories.connection', MagicMock())
sys.modules.setdefault('psycopg2', MagicMock())
sys.modules.setdefault('psycopg2.pool', MagicMock())


from enrichment.sources.build_history import BuildHistorySource
from enrichment.sources.dependency_context import DependencyContextSource
from enrichment.sources.related_failures import RelatedFailuresSource

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(app_name='rhoai-v3-5', namespace='rhoai-v3-5-tenant'):
    """Build a minimal mock CollectorConfig."""
    config = MagicMock()
    config.k8s.application_name = app_name
    config.k8s.namespace = namespace
    config.db = MagicMock()
    config.github_token = None
    return config


def _make_db_with_rows(rows):
    """Build a mock DatabaseConnection that yields rows from cursor.fetchall()."""
    db = MagicMock()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_conn.cursor.return_value = mock_cursor

    # Make connection() a context manager
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    db.connection.return_value = mock_ctx

    return db, mock_cursor


# ===========================================================================
# DependencyContextSource
# ===========================================================================

class TestDependencyContextSourceProperties:
    """Test DependencyContextSource metadata properties."""

    def test_source_name(self):
        src = DependencyContextSource(_make_config())
        assert src.source_name() == 'dependency_changes'

    def test_requires_external_api(self):
        src = DependencyContextSource(_make_config())
        assert src.requires_external_api is False

    def test_timeout_seconds(self):
        src = DependencyContextSource(_make_config())
        assert src.timeout_seconds == 5


class TestDependencyContextFetch:
    """Test DependencyContextSource.fetch()."""

    def test_no_commit_context(self):
        src = DependencyContextSource(_make_config())
        assert src.fetch({'id': 1}) is None

    def test_commit_context_is_none(self):
        src = DependencyContextSource(_make_config())
        assert src.fetch({'id': 1, 'commit_context': None}) is None

    def test_commit_context_string_valid_json(self):
        src = DependencyContextSource(_make_config())
        ctx = json.dumps({
            'commit': {
                'files': [
                    {'filename': 'requirements.txt', 'status': 'modified',
                     'additions': 2, 'deletions': 1, 'patch': '+new-dep==1.0'},
                ]
            }
        })
        result = src.fetch({'id': 1, 'commit_context': ctx})
        assert result is not None
        assert 'requirements.txt' in result['dependency_changes']
        assert result['dependency_changes']['requirements.txt']['status'] == 'modified'
        assert result['dependency_changes']['requirements.txt']['additions'] == 2

    def test_commit_context_string_invalid_json(self):
        src = DependencyContextSource(_make_config())
        result = src.fetch({'id': 1, 'commit_context': 'not json'})
        assert result is None

    def test_commit_context_dict(self):
        src = DependencyContextSource(_make_config())
        ctx = {
            'commit': {
                'files': [
                    {'filename': 'go.mod', 'status': 'modified',
                     'additions': 5, 'deletions': 3, 'patch': '+require foo v1.2'},
                ]
            }
        }
        result = src.fetch({'id': 1, 'commit_context': ctx})
        assert result is not None
        assert 'go.mod' in result['dependency_changes']

    def test_no_files_in_commit(self):
        src = DependencyContextSource(_make_config())
        result = src.fetch({'id': 1, 'commit_context': {'commit': {'files': []}}})
        assert result is None

    def test_no_commit_key(self):
        src = DependencyContextSource(_make_config())
        result = src.fetch({'id': 1, 'commit_context': {'other': 'data'}})
        assert result is None

    def test_no_dependency_files(self):
        src = DependencyContextSource(_make_config())
        ctx = {
            'commit': {
                'files': [
                    {'filename': 'main.py', 'status': 'modified'},
                    {'filename': 'README.md', 'status': 'modified'},
                ]
            }
        }
        result = src.fetch({'id': 1, 'commit_context': ctx})
        assert result is None

    def test_multiple_dependency_files(self):
        src = DependencyContextSource(_make_config())
        ctx = {
            'commit': {
                'files': [
                    {'filename': 'requirements.txt', 'status': 'modified',
                     'additions': 1, 'deletions': 0, 'patch': '+dep==1.0'},
                    {'filename': 'go.sum', 'status': 'modified',
                     'additions': 10, 'deletions': 5},
                    {'filename': 'main.go', 'status': 'modified'},
                    {'filename': 'Cargo.toml', 'status': 'added',
                     'additions': 20, 'deletions': 0, 'patch': '[dependencies]\nfoo = "1.0"'},
                ]
            }
        }
        result = src.fetch({'id': 1, 'commit_context': ctx})
        assert result is not None
        changes = result['dependency_changes']
        assert len(changes) == 3
        assert 'requirements.txt' in changes
        assert 'go.sum' in changes
        assert 'Cargo.toml' in changes
        assert 'main.go' not in changes

    def test_patch_truncation(self):
        src = DependencyContextSource(_make_config())
        long_patch = 'x' * 5000
        ctx = {
            'commit': {
                'files': [
                    {'filename': 'package.json', 'status': 'modified',
                     'additions': 1, 'deletions': 0, 'patch': long_patch},
                ]
            }
        }
        result = src.fetch({'id': 1, 'commit_context': ctx})
        assert len(result['dependency_changes']['package.json']['patch']) == 3000

    def test_file_without_patch(self):
        src = DependencyContextSource(_make_config())
        ctx = {
            'commit': {
                'files': [
                    {'filename': 'package-lock.json', 'status': 'modified',
                     'additions': 100, 'deletions': 50},
                ]
            }
        }
        result = src.fetch({'id': 1, 'commit_context': ctx})
        assert 'patch' not in result['dependency_changes']['package-lock.json']

    def test_all_dependency_patterns(self):
        """Verify each dependency pattern is recognized."""
        src = DependencyContextSource(_make_config())
        patterns = [
            'requirements.txt', 'requirements/dev.txt', 'Pipfile',
            'pyproject.toml', 'package.json', 'package-lock.json',
            'go.mod', 'go.sum', 'Gemfile', 'pom.xml', 'Cargo.toml',
        ]
        for pattern in patterns:
            assert src._is_dependency_file(pattern), \
                "{} should be recognized as dependency file".format(pattern)

    def test_non_dependency_patterns(self):
        src = DependencyContextSource(_make_config())
        non_deps = ['main.py', 'Dockerfile', 'Makefile', 'README.md', '.gitignore']
        for fname in non_deps:
            assert not src._is_dependency_file(fname), \
                "{} should NOT be a dependency file".format(fname)

    def test_yarn_lock_in_subdir(self):
        src = DependencyContextSource(_make_config())
        # yarn.lock is not in the patterns list, so it won't match
        assert not src._is_dependency_file('yarn.lock')

    def test_exception_returns_none(self):
        src = DependencyContextSource(_make_config())
        # Force an exception by passing something that breaks iteration
        failure = MagicMock()
        failure.get.side_effect = Exception("unexpected")
        assert src.fetch(failure) is None

    def test_defaults_for_missing_fields(self):
        src = DependencyContextSource(_make_config())
        ctx = {
            'commit': {
                'files': [
                    {'filename': 'go.mod'},  # no status, additions, deletions, patch
                ]
            }
        }
        result = src.fetch({'id': 1, 'commit_context': ctx})
        change = result['dependency_changes']['go.mod']
        assert change['status'] == 'modified'
        assert change['additions'] == 0
        assert change['deletions'] == 0


# ===========================================================================
# BuildHistorySource
# ===========================================================================

class TestBuildHistorySourceProperties:
    """Test BuildHistorySource metadata properties."""

    def test_source_name(self):
        src = BuildHistorySource(_make_config())
        assert src.source_name() == 'build_history'

    def test_requires_external_api(self):
        src = BuildHistorySource(_make_config())
        assert src.requires_external_api is True

    def test_timeout_seconds(self):
        src = BuildHistorySource(_make_config())
        assert src.timeout_seconds == 60


class TestBuildHistoryFetch:
    """Test BuildHistorySource.fetch()."""

    def test_no_commit_sha(self):
        src = BuildHistorySource(_make_config())
        result = src.fetch({
            'component_name': 'comp',
            'repository_url': 'https://github.com/org/repo',
        })
        assert result is None

    def test_no_repo_url(self):
        src = BuildHistorySource(_make_config())
        result = src.fetch({
            'component_name': 'comp',
            'commit_sha': 'abc123',
        })
        assert result is None

    def test_empty_commit_sha(self):
        src = BuildHistorySource(_make_config())
        result = src.fetch({
            'component_name': 'comp',
            'commit_sha': '',
            'repository_url': 'https://github.com/org/repo',
        })
        assert result is None

    def test_no_last_success(self):
        src = BuildHistorySource(_make_config())
        with patch.object(src, '_find_last_success', return_value=None):
            result = src.fetch({
                'component_name': 'comp',
                'commit_sha': 'abc123',
                'repository_url': 'https://github.com/org/repo',
            })
        assert result is None

    def test_no_comparison(self):
        src = BuildHistorySource(_make_config())
        last_success = {
            'commit_sha': 'def456',
            'pipelinerun_name': 'pr-123',
            'completed_at': '2024-01-01T00:00:00Z',
        }
        with patch.object(src, '_find_last_success', return_value=last_success):
            with patch.object(src, '_compare_commits', return_value=None):
                result = src.fetch({
                    'component_name': 'comp',
                    'commit_sha': 'abc123',
                    'repository_url': 'https://github.com/org/repo',
                })
        assert result is None

    def test_successful_fetch(self):
        src = BuildHistorySource(_make_config())
        last_success = {
            'commit_sha': 'def456def456def456',
            'pipelinerun_name': 'pr-123',
            'completed_at': '2024-01-01T00:00:00Z',
        }
        comparison = {
            'total_commits': 3,
            'files_changed': [{'filename': 'main.go'}],
            'summary': '3 commits, 1 files changed',
        }
        with patch.object(src, '_find_last_success', return_value=last_success):
            with patch.object(src, '_compare_commits', return_value=comparison):
                result = src.fetch({
                    'component_name': 'comp',
                    'commit_sha': 'abc123abc123abc123',
                    'repository_url': 'https://github.com/org/repo',
                })

        assert result is not None
        assert result['last_successful_build']['commit_sha'] == 'def456def456def456'
        assert result['last_successful_build']['pipelinerun'] == 'pr-123'
        assert result['changes_since_success']['total_commits'] == 3

    def test_uses_application_from_failure(self):
        src = BuildHistorySource(_make_config())
        with patch.object(src, '_find_last_success', return_value=None) as mock_fls:
            src.fetch({
                'component_name': 'comp',
                'commit_sha': 'abc123',
                'repository_url': 'https://github.com/org/repo',
                'application': 'custom-app',
            })
        mock_fls.assert_called_once_with('custom-app', 'comp', 'abc123')

    def test_falls_back_to_config_application(self):
        config = _make_config(app_name='fallback-app')
        src = BuildHistorySource(config)
        with patch.object(src, '_find_last_success', return_value=None) as mock_fls:
            src.fetch({
                'component_name': 'comp',
                'commit_sha': 'abc123',
                'repository_url': 'https://github.com/org/repo',
            })
        mock_fls.assert_called_once_with('fallback-app', 'comp', 'abc123')


class TestBuildHistoryFindLastSuccess:
    """Test BuildHistorySource._find_last_success()."""

    def test_tekton_client_error(self):
        src = BuildHistorySource(_make_config())
        mock_tr = MagicMock()
        mock_tr.query_component_build_history.side_effect = Exception("timeout")

        with patch.dict(sys.modules, {'clients.tekton_results': MagicMock(
            TektonResultsClient=MagicMock(return_value=mock_tr)
        )}):
            result = src._find_last_success('app', 'comp', 'fail_sha')
        assert result is None

    def test_no_successful_builds(self):
        src = BuildHistorySource(_make_config())
        history = [
            {
                'status': {'conditions': [{'status': 'False', 'type': 'Succeeded'}]},
                'metadata': {'name': 'pr-1', 'annotations': {}},
            },
        ]
        mock_tr = MagicMock()
        mock_tr.query_component_build_history.return_value = history

        with patch.dict(sys.modules, {'clients.tekton_results': MagicMock(
            TektonResultsClient=MagicMock(return_value=mock_tr)
        )}):
            result = src._find_last_success('app', 'comp', 'fail_sha')
        assert result is None

    def test_skips_same_sha(self):
        src = BuildHistorySource(_make_config())
        history = [
            {
                'status': {'conditions': [{'status': 'True', 'type': 'Succeeded'}]},
                'metadata': {
                    'name': 'pr-1',
                    'annotations': {'build.appstudio.redhat.com/commit_sha': 'fail_sha'},
                },
            },
        ]
        mock_tr = MagicMock()
        mock_tr.query_component_build_history.return_value = history

        with patch.dict(sys.modules, {'clients.tekton_results': MagicMock(
            TektonResultsClient=MagicMock(return_value=mock_tr)
        )}):
            result = src._find_last_success('app', 'comp', 'fail_sha')
        assert result is None

    def test_finds_successful_build(self):
        src = BuildHistorySource(_make_config())
        history = [
            {
                'status': {'conditions': [{'status': 'False', 'type': 'Succeeded'}]},
                'metadata': {'name': 'pr-fail', 'annotations': {}},
            },
            {
                'status': {'conditions': [
                    {'status': 'True', 'type': 'Succeeded', 'lastTransitionTime': '2024-01-01'},
                ]},
                'metadata': {
                    'name': 'pr-success',
                    'annotations': {
                        'build.appstudio.redhat.com/commit_sha': 'good_sha',
                    },
                },
            },
        ]
        mock_tr = MagicMock()
        mock_tr.query_component_build_history.return_value = history

        with patch.dict(sys.modules, {'clients.tekton_results': MagicMock(
            TektonResultsClient=MagicMock(return_value=mock_tr)
        )}):
            result = src._find_last_success('app', 'comp', 'fail_sha')

        assert result is not None
        assert result['commit_sha'] == 'good_sha'
        assert result['pipelinerun_name'] == 'pr-success'
        assert result['completed_at'] == '2024-01-01'

    def test_tries_fallback_annotations(self):
        """Test that it checks multiple annotation keys for SHA."""
        src = BuildHistorySource(_make_config())
        history = [
            {
                'status': {'conditions': [
                    {'status': 'True', 'type': 'Succeeded', 'lastTransitionTime': 'T1'},
                ]},
                'metadata': {
                    'name': 'pr-1',
                    'annotations': {
                        'pipelinesascode.tekton.dev/sha': 'pac_sha',
                    },
                },
            },
        ]
        mock_tr = MagicMock()
        mock_tr.query_component_build_history.return_value = history

        with patch.dict(sys.modules, {'clients.tekton_results': MagicMock(
            TektonResultsClient=MagicMock(return_value=mock_tr)
        )}):
            result = src._find_last_success('app', 'comp', 'other_sha')

        assert result['commit_sha'] == 'pac_sha'

    def test_skips_builds_without_conditions(self):
        src = BuildHistorySource(_make_config())
        history = [
            {
                'status': {'conditions': []},
                'metadata': {'name': 'pr-no-cond', 'annotations': {}},
            },
            {
                'status': {},
                'metadata': {'name': 'pr-no-status', 'annotations': {}},
            },
        ]
        mock_tr = MagicMock()
        mock_tr.query_component_build_history.return_value = history

        with patch.dict(sys.modules, {'clients.tekton_results': MagicMock(
            TektonResultsClient=MagicMock(return_value=mock_tr)
        )}):
            result = src._find_last_success('app', 'comp', 'sha')
        assert result is None


class TestBuildHistoryCompareCommits:
    """Test BuildHistorySource._compare_commits()."""

    def test_no_github_client_no_token(self):
        config = _make_config()
        config.github_token = None
        src = BuildHistorySource(config)
        with patch.dict(os.environ, {}, clear=True):
            result = src._compare_commits(
                'https://github.com/org/repo', 'base', 'head')
        assert result is None

    def test_creates_github_client_from_env_token(self):
        config = _make_config()
        config.github_token = None
        src = BuildHistorySource(config)

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'commits': [{'commit': {'message': 'fix: thing'}}],
            'files': [{'filename': 'main.go', 'status': 'modified',
                        'additions': 5, 'deletions': 2, 'patch': '+line'}],
        }
        mock_client._session.get.return_value = mock_response

        with patch.dict(os.environ, {'GITHUB_TOKEN': 'ghp_test'}):
            with patch.dict(sys.modules, {'clients.github_client': MagicMock(
                GitHubClient=MagicMock(return_value=mock_client)
            )}):
                result = src._compare_commits(
                    'https://github.com/org/repo', 'basesha1', 'headsha1')

        assert result is not None
        assert result['total_commits'] == 1
        assert len(result['files_changed']) == 1

    def test_github_api_non_200(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client._session.get.return_value = mock_response

        src = BuildHistorySource(_make_config(), github_client=mock_client)
        result = src._compare_commits(
            'https://github.com/org/repo', 'base', 'head')
        assert result is None

    def test_successful_comparison(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'commits': [
                {'commit': {'message': 'feat: add X\ndetailed description'}},
                {'commit': {'message': 'fix: Y'}},
            ],
            'files': [
                {'filename': 'a.go', 'status': 'modified',
                 'additions': 10, 'deletions': 2, 'patch': '+new code'},
                {'filename': 'b.go', 'status': 'added',
                 'additions': 50, 'deletions': 0},
            ],
        }
        mock_client._session.get.return_value = mock_response

        src = BuildHistorySource(_make_config(), github_client=mock_client)
        result = src._compare_commits(
            'https://github.com/org/repo', 'aaaa1111', 'bbbb2222')

        assert result['total_commits'] == 2
        assert result['commit_messages'] == ['feat: add X', 'fix: Y']
        assert len(result['files_changed']) == 2
        assert result['files_changed'][0]['patch'] == '+new code'
        assert 'patch' not in result['files_changed'][1]
        assert 'aaaa1111' in result['summary']
        assert 'bbbb2222' in result['summary']

    def test_truncates_patch(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        long_patch = 'x' * 5000
        mock_response.json.return_value = {
            'commits': [],
            'files': [
                {'filename': 'big.go', 'status': 'modified',
                 'additions': 0, 'deletions': 0, 'patch': long_patch},
            ],
        }
        mock_client._session.get.return_value = mock_response

        src = BuildHistorySource(_make_config(), github_client=mock_client)
        result = src._compare_commits(
            'https://github.com/org/repo', 'base', 'head')

        assert len(result['files_changed'][0]['patch']) == 2000

    def test_limits_files_to_max(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        files = [{'filename': 'file{}.go'.format(i), 'status': 'modified'}
                 for i in range(30)]
        mock_response.json.return_value = {
            'commits': [],
            'files': files,
        }
        mock_client._session.get.return_value = mock_response

        src = BuildHistorySource(_make_config(), github_client=mock_client)
        result = src._compare_commits(
            'https://github.com/org/repo', 'base', 'head')

        # MAX_COMPARE_FILES = 20
        assert len(result['files_changed']) == 20

    def test_exception_returns_none(self):
        mock_client = MagicMock()
        mock_client._session.get.side_effect = Exception("network error")

        src = BuildHistorySource(_make_config(), github_client=mock_client)
        result = src._compare_commits(
            'https://github.com/org/repo', 'base', 'head')
        assert result is None


class TestBuildHistoryParseRepo:
    """Test BuildHistorySource._parse_repo()."""

    def test_standard_github_url(self):
        src = BuildHistorySource(_make_config())
        owner, repo = src._parse_repo('https://github.com/org/my-repo')
        assert owner == 'org'
        assert repo == 'my-repo'

    def test_url_with_git_suffix(self):
        src = BuildHistorySource(_make_config())
        owner, repo = src._parse_repo('https://github.com/org/my-repo.git')
        assert owner == 'org'
        assert repo == 'my-repo'

    def test_url_with_trailing_slash(self):
        src = BuildHistorySource(_make_config())
        owner, repo = src._parse_repo('https://github.com/org/my-repo/')
        assert owner == 'org'
        assert repo == 'my-repo'

    def test_short_url(self):
        src = BuildHistorySource(_make_config())
        owner, repo = src._parse_repo('org/repo')
        assert owner == 'org'
        assert repo == 'repo'

    def test_too_short(self):
        src = BuildHistorySource(_make_config())
        owner, repo = src._parse_repo('singlepart')
        assert owner is None
        assert repo is None


# ===========================================================================
# RelatedFailuresSource
# ===========================================================================

class TestRelatedFailuresSourceProperties:
    """Test RelatedFailuresSource metadata properties."""

    def test_source_name(self):
        src = RelatedFailuresSource(_make_config(), db=MagicMock())
        assert src.source_name() == 'related_failures'

    def test_requires_external_api(self):
        src = RelatedFailuresSource(_make_config(), db=MagicMock())
        assert src.requires_external_api is False

    def test_timeout_seconds(self):
        src = RelatedFailuresSource(_make_config(), db=MagicMock())
        assert src.timeout_seconds == 10


class TestRelatedFailuresStripVersionSuffix:
    """Test _strip_version_suffix static method."""

    def test_standard_version(self):
        assert RelatedFailuresSource._strip_version_suffix('odh-dashboard-v3-4') == 'odh-dashboard-'

    def test_ea_version(self):
        assert RelatedFailuresSource._strip_version_suffix('odh-dashboard-v3-5-ea-1') == 'odh-dashboard-'

    def test_no_version(self):
        assert RelatedFailuresSource._strip_version_suffix('some-component') == 'some-component'

    def test_version_at_start(self):
        # Unusual but should still work
        result = RelatedFailuresSource._strip_version_suffix('v3-component')
        # -v3-component -> the regex matches -v\d+, but 'v3-component' starts with v
        # The regex is r'-v\d+.*$' so it needs a '-v' prefix
        assert result == 'v3-component'


class TestRelatedFailuresFetch:
    """Test RelatedFailuresSource.fetch()."""

    def test_missing_component_name(self):
        src = RelatedFailuresSource(_make_config(), db=MagicMock())
        assert src.fetch({'id': 1}) is None

    def test_missing_failure_id(self):
        src = RelatedFailuresSource(_make_config(), db=MagicMock())
        assert src.fetch({'component_name': 'comp'}) is None

    def test_no_related_no_resolved(self):
        src = RelatedFailuresSource(_make_config(), db=MagicMock())
        with patch.object(src, '_query_related_failures', return_value=[]):
            with patch.object(src, '_query_cross_app_failures', return_value=[]):
                with patch.object(src, '_query_resolved_examples', return_value=[]):
                    result = src.fetch({
                        'id': 1,
                        'component_name': 'comp',
                        'error_type': 'build_error',
                    })
        assert result is None

    def test_only_same_app_failures(self):
        same_app = [{'id': 2, 'component_name': 'comp', 'similarity_score': 1.0}]
        # With 1 same-app result (< 2), cross-app is also queried
        cross_app = [{'id': 3, 'component_name': 'comp', 'similarity_score': 0.5, 'cross_app': True}]
        src = RelatedFailuresSource(_make_config(), db=MagicMock())
        with patch.object(src, '_query_related_failures', return_value=same_app):
            with patch.object(src, '_query_cross_app_failures', return_value=cross_app):
                with patch.object(src, '_query_resolved_examples', return_value=[]):
                    result = src.fetch({
                        'id': 1,
                        'component_name': 'comp',
                        'error_type': 'build_error',
                    })

        assert result is not None
        assert len(result['related_failures']) == 2
        assert result['cross_app_count'] == 1

    def test_enough_same_app_skips_cross_app(self):
        same_app = [
            {'id': 2, 'similarity_score': 1.0},
            {'id': 3, 'similarity_score': 0.9},
        ]
        src = RelatedFailuresSource(_make_config(), db=MagicMock())
        with patch.object(src, '_query_related_failures', return_value=same_app):
            with patch.object(src, '_query_cross_app_failures') as mock_cross:
                with patch.object(src, '_query_resolved_examples', return_value=[]):
                    result = src.fetch({
                        'id': 1,
                        'component_name': 'comp',
                        'error_type': 'err',
                    })

        # Cross-app should NOT be called when same-app has >= 2 results
        mock_cross.assert_not_called()
        assert result['cross_app_count'] == 0

    def test_resolved_examples_included(self):
        resolved = [{'id': 10, 'resolution_commit_sha': 'abc', 'relevance': 1.0}]
        src = RelatedFailuresSource(_make_config(), db=MagicMock())
        with patch.object(src, '_query_related_failures', return_value=[]):
            with patch.object(src, '_query_cross_app_failures', return_value=[]):
                with patch.object(src, '_query_resolved_examples', return_value=resolved):
                    result = src.fetch({
                        'id': 1,
                        'component_name': 'comp',
                        'error_type': 'err',
                    })

        assert result is not None
        assert 'resolved_examples' in result
        assert len(result['resolved_examples']) == 1

    def test_exception_returns_none(self):
        src = RelatedFailuresSource(_make_config(), db=MagicMock())
        with patch.object(src, '_query_related_failures', side_effect=Exception("db down")):
            result = src.fetch({
                'id': 1,
                'component_name': 'comp',
                'error_type': 'err',
            })
        assert result is None

    def test_uses_failure_application(self):
        src = RelatedFailuresSource(_make_config(), db=MagicMock())
        with patch.object(src, '_query_related_failures', return_value=[]) as mock_qr:
            with patch.object(src, '_query_cross_app_failures', return_value=[]):
                with patch.object(src, '_query_resolved_examples', return_value=[]):
                    src.fetch({
                        'id': 1,
                        'component_name': 'comp',
                        'error_type': 'err',
                        'application': 'my-app',
                    })

        assert mock_qr.call_args.kwargs['application'] == 'my-app'


class TestRelatedFailuresQueryRelated:
    """Test RelatedFailuresSource._query_related_failures()."""

    def test_returns_formatted_results(self):
        dt = datetime(2024, 6, 15, 10, 30, 0)
        rows = [
            (42, 'comp-v3-5', 'build_error', 'Error message here' * 20,
             'pr-abc', dt, True, 'Root cause text' * 30,
             0.92, 'dependency_issue', 1.0),
        ]
        db, cursor = _make_db_with_rows(rows)
        src = RelatedFailuresSource(_make_config(), db=db)

        results = src._query_related_failures('comp-v3-5', 'build_error', 1, 'app', 3)

        assert len(results) == 1
        r = results[0]
        assert r['id'] == 42
        assert r['component_name'] == 'comp-v3-5'
        assert r['ai_analyzed'] is True
        assert r['similarity_score'] == 1.0
        assert r['failure_category'] == 'dependency_issue'
        # Error message should be truncated to MAX_ERROR_MESSAGE_LENGTH
        assert len(r['error_message']) <= 200
        # Root cause truncated to MAX_ROOT_CAUSE_LENGTH
        assert len(r['root_cause']) <= 300

    def test_handles_none_fields(self):
        rows = [
            (1, 'comp', None, None, 'pr-1', None, False, None, None, None, 0.5),
        ]
        db, cursor = _make_db_with_rows(rows)
        src = RelatedFailuresSource(_make_config(), db=db)

        results = src._query_related_failures('comp', None, 99, 'app', 3)

        assert len(results) == 1
        r = results[0]
        assert r['error_message'] == ''
        assert r['first_detected_at'] is None
        assert r['root_cause'] is None
        assert r['confidence_score'] is None

    def test_empty_results(self):
        db, cursor = _make_db_with_rows([])
        src = RelatedFailuresSource(_make_config(), db=db)

        results = src._query_related_failures('comp', 'err', 1, 'app', 3)
        assert results == []


class TestRelatedFailuresQueryCrossApp:
    """Test RelatedFailuresSource._query_cross_app_failures()."""

    def test_returns_cross_app_results(self):
        dt = datetime(2024, 6, 15, 10, 30, 0)
        rows = [
            (50, 'comp-v3-4', 'build_error', 'Error msg',
             'pr-xyz', dt, True, 'Root cause',
             0.88, 'dependency_issue', 'rhoai-v3-4', 0.8),
        ]
        db, cursor = _make_db_with_rows(rows)
        src = RelatedFailuresSource(_make_config(), db=db)

        results = src._query_cross_app_failures('comp-v3-5', 'build_error', 1, 'rhoai-v3-5', 3)

        assert len(results) == 1
        r = results[0]
        assert r['source_application'] == 'rhoai-v3-4'
        assert r['cross_app'] is True
        assert r['similarity_score'] == 0.8

    def test_empty_results(self):
        db, cursor = _make_db_with_rows([])
        src = RelatedFailuresSource(_make_config(), db=db)

        results = src._query_cross_app_failures('comp', 'err', 1, 'app', 3)
        assert results == []

    def test_handles_none_fields(self):
        rows = [
            (1, 'comp', None, None, 'pr-1', None, True, None, None, None, 'other-app', 0.4),
        ]
        db, cursor = _make_db_with_rows(rows)
        src = RelatedFailuresSource(_make_config(), db=db)

        results = src._query_cross_app_failures('comp', None, 99, 'app', 3)
        assert len(results) == 1
        assert results[0]['error_message'] == ''
        assert results[0]['root_cause'] is None


class TestRelatedFailuresQueryResolved:
    """Test RelatedFailuresSource._query_resolved_examples()."""

    def test_returns_resolved_with_commit_url(self):
        dt = datetime(2024, 5, 20, 8, 0, 0)
        rows = [
            (100, 'comp-v3-5', 'build_error', 'Error msg',
             'abc123sha', dt, 'Dependency mismatch',
             'dependency_issue', 'Update go.mod',
             'https://github.com/org/repo', 1.0),
        ]
        db, cursor = _make_db_with_rows(rows)
        src = RelatedFailuresSource(_make_config(), db=db)

        results = src._query_resolved_examples('comp-v3-5', 'build_error', 1, 'app', 2)

        assert len(results) == 1
        r = results[0]
        assert r['resolution_commit_sha'] == 'abc123sha'
        assert r['commit_url'] == 'https://github.com/org/repo/commit/abc123sha'
        assert r['relevance'] == 1.0
        assert r['recommended_fix'] == 'Update go.mod'

    def test_commit_url_empty_when_no_repo(self):
        dt = datetime(2024, 5, 20, 8, 0, 0)
        rows = [
            (100, 'comp', 'err', 'msg', 'sha', dt, 'cause', 'cat', 'fix', None, 0.8),
        ]
        db, cursor = _make_db_with_rows(rows)
        src = RelatedFailuresSource(_make_config(), db=db)

        results = src._query_resolved_examples('comp', 'err', 1, 'app', 2)

        assert results[0]['commit_url'] == ''

    def test_commit_url_empty_when_no_sha(self):
        dt = datetime(2024, 5, 20, 8, 0, 0)
        rows = [
            (100, 'comp', 'err', 'msg', None, dt, 'cause', 'cat', 'fix',
             'https://github.com/org/repo', 0.6),
        ]
        db, cursor = _make_db_with_rows(rows)
        src = RelatedFailuresSource(_make_config(), db=db)

        results = src._query_resolved_examples('comp', 'err', 1, 'app', 2)
        assert results[0]['commit_url'] == ''

    def test_strips_git_suffix_from_commit_url(self):
        dt = datetime(2024, 5, 20, 8, 0, 0)
        rows = [
            (100, 'comp', 'err', 'msg', 'sha123', dt, 'cause', 'cat', 'fix',
             'https://github.com/org/repo.git', 1.0),
        ]
        db, cursor = _make_db_with_rows(rows)
        src = RelatedFailuresSource(_make_config(), db=db)

        results = src._query_resolved_examples('comp', 'err', 1, 'app', 2)
        assert results[0]['commit_url'] == 'https://github.com/org/repo/commit/sha123'

    def test_handles_none_fields(self):
        rows = [
            (100, 'comp', None, None, 'sha', None, None, None, None, '', 0.4),
        ]
        db, cursor = _make_db_with_rows(rows)
        src = RelatedFailuresSource(_make_config(), db=db)

        results = src._query_resolved_examples('comp', None, 1, 'app', 2)
        r = results[0]
        assert r['error_message'] == ''
        assert r['resolved_at'] is None
        assert r['root_cause'] is None
        assert r['recommended_fix'] is None

    def test_empty_results(self):
        db, cursor = _make_db_with_rows([])
        src = RelatedFailuresSource(_make_config(), db=db)

        results = src._query_resolved_examples('comp', 'err', 1, 'app', 2)
        assert results == []

    def test_truncates_long_fields(self):
        dt = datetime(2024, 5, 20, 8, 0, 0)
        rows = [
            (100, 'comp', 'err', 'x' * 500, 'sha', dt,
             'y' * 500, 'cat', 'z' * 500, '', 1.0),
        ]
        db, cursor = _make_db_with_rows(rows)
        src = RelatedFailuresSource(_make_config(), db=db)

        results = src._query_resolved_examples('comp', 'err', 1, 'app', 2)
        r = results[0]
        assert len(r['error_message']) <= 200
        assert len(r['root_cause']) <= 300
        assert len(r['recommended_fix']) <= 300
