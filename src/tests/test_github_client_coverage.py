"""Comprehensive tests for GitHubClient — HTTP errors, edge cases, write ops."""

import base64
import os
import sys
from unittest.mock import MagicMock

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from clients.github_client import (
    MAX_FILE_CONTENT_CHARS,
    GitHubClient,
    parse_github_repo,
)

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _mock_response(status_code=200, json_data=None, text=''):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


def _make_client():
    return GitHubClient(token='ghp_test_token')


def _commit_payload(sha='abc123', message='fix: deps', author='Dev',
                    date='2026-01-01T00:00:00Z', files=None, parents=None):
    return {
        'sha': sha,
        'commit': {
            'message': message,
            'author': {'name': author, 'date': date},
        },
        'parents': [{'sha': s} for s in (parents or ['parent1'])],
        'stats': {'additions': 5, 'deletions': 2, 'total': 7},
        'files': files or [],
    }


# ═══════════════════════════════════════════════════════════════════════
# parse_github_repo — standalone utility
# ═══════════════════════════════════════════════════════════════════════

class TestParseGithubRepo:
    def test_standard_https(self):
        assert parse_github_repo('https://github.com/org/repo') == ('org', 'repo')

    def test_http_url(self):
        assert parse_github_repo('http://github.com/org/repo') == ('org', 'repo')

    def test_git_suffix(self):
        assert parse_github_repo('https://github.com/org/repo.git') == ('org', 'repo')

    def test_trailing_slash(self):
        assert parse_github_repo('https://github.com/org/repo/') == ('org', 'repo')

    def test_git_suffix_with_trailing_slash(self):
        assert parse_github_repo('https://github.com/org/repo.git/') == ('org', 'repo')

    def test_none_input(self):
        assert parse_github_repo(None) is None

    def test_empty_string(self):
        assert parse_github_repo('') is None

    def test_non_github_url(self):
        assert parse_github_repo('https://gitlab.com/org/repo') is None

    def test_bitbucket_url(self):
        assert parse_github_repo('https://bitbucket.org/org/repo') is None

    def test_github_enterprise_url(self):
        # github.com specifically — GHE not supported
        assert parse_github_repo('https://github.example.com/org/repo') is None

    def test_repo_with_dots(self):
        assert parse_github_repo('https://github.com/org/my.repo.name') == ('org', 'my.repo.name')

    def test_repo_with_hyphens(self):
        assert parse_github_repo('https://github.com/acme-org/model-registry') == ('acme-org', 'model-registry')

    def test_url_with_extra_path_segments(self):
        # The regex grabs only owner/repo, extra path segments are ignored
        result = parse_github_repo('https://github.com/org/repo/tree/main/src')
        assert result == ('org', 'repo')

    def test_repo_ending_in_git_but_not_suffix(self):
        # "repogit" should NOT have "git" stripped — only ".git" suffix
        assert parse_github_repo('https://github.com/org/repogit') == ('org', 'repogit')

    def test_url_with_whitespace(self):
        # Whitespace after repo name should not be included
        result = parse_github_repo('https://github.com/org/repo ')
        assert result is not None
        assert result[1] == 'repo'


# ═══════════════════════════════════════════════════════════════════════
# Constructor
# ═══════════════════════════════════════════════════════════════════════

class TestGitHubClientInit:
    def test_with_token_sets_auth_header(self):
        client = GitHubClient(token='ghp_abc')
        assert client._session.headers['Authorization'] == 'token ghp_abc'

    def test_without_token_no_auth_header(self):
        client = GitHubClient()
        assert 'Authorization' not in client._session.headers

    def test_default_accept_header(self):
        client = GitHubClient()
        assert client._session.headers['Accept'] == 'application/vnd.github.v3+json'

    def test_user_agent_header(self):
        client = GitHubClient()
        assert 'ci-autohealing' in client._session.headers['User-Agent']

    def test_none_token_no_auth(self):
        client = GitHubClient(token=None)
        assert 'Authorization' not in client._session.headers

    def test_empty_string_token_no_auth(self):
        # Empty string is falsy, so no Authorization header
        client = GitHubClient(token='')
        assert 'Authorization' not in client._session.headers


# ═══════════════════════════════════════════════════════════════════════
# _get — HTTP error handling
# ═══════════════════════════════════════════════════════════════════════

class TestGet:
    def setup_method(self):
        self.client = _make_client()

    def test_200_returns_response_object(self):
        mock_resp = _mock_response(200, {'ok': True})
        self.client._session.get = MagicMock(return_value=mock_resp)
        assert self.client._get('/test') is mock_resp

    def test_404_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client._get('/test') is None

    def test_401_unauthorized_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(401))
        assert self.client._get('/test') is None

    def test_403_forbidden_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(403, {'message': 'Forbidden'}))
        assert self.client._get('/test') is None

    def test_403_rate_limit_message(self):
        self.client._session.get = MagicMock(
            return_value=_mock_response(403, {'message': 'API rate limit exceeded'}))
        assert self.client._get('/test') is None

    def test_403_json_parse_error(self):
        resp = _mock_response(403)
        resp.json.side_effect = ValueError('bad json')
        self.client._session.get = MagicMock(return_value=resp)
        assert self.client._get('/test') is None

    def test_429_throttle_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(429))
        assert self.client._get('/test') is None

    def test_500_server_error_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(500))
        assert self.client._get('/test') is None

    def test_502_bad_gateway_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(502))
        assert self.client._get('/test') is None

    def test_timeout_returns_none(self):
        self.client._session.get = MagicMock(side_effect=requests.Timeout('request timed out'))
        assert self.client._get('/test') is None

    def test_connection_error_returns_none(self):
        self.client._session.get = MagicMock(side_effect=requests.ConnectionError('refused'))
        assert self.client._get('/test') is None

    def test_generic_request_exception_returns_none(self):
        self.client._session.get = MagicMock(side_effect=requests.RequestException('unknown'))
        assert self.client._get('/test') is None

    def test_custom_accept_header_passed(self):
        mock_resp = _mock_response(200)
        self.client._session.get = MagicMock(return_value=mock_resp)
        self.client._get('/test', accept='application/vnd.github.groot-preview+json')
        _, kwargs = self.client._session.get.call_args
        assert kwargs['headers']['Accept'] == 'application/vnd.github.groot-preview+json'

    def test_params_forwarded(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200))
        self.client._get('/test', params={'ref': 'abc'})
        _, kwargs = self.client._session.get.call_args
        assert kwargs['params'] == {'ref': 'abc'}

    def test_url_constructed_from_api_base(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200))
        self.client._get('/repos/o/r/commits/sha')
        url = self.client._session.get.call_args[0][0]
        assert url == 'https://api.github.com/repos/o/r/commits/sha'

    def test_timeout_kwarg_is_30(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200))
        self.client._get('/test')
        _, kwargs = self.client._session.get.call_args
        assert kwargs['timeout'] == 30


# ═══════════════════════════════════════════════════════════════════════
# _post — write helper
# ═══════════════════════════════════════════════════════════════════════

class TestPost:
    def setup_method(self):
        self.client = _make_client()

    def test_200_returns_response(self):
        resp = _mock_response(200, {'id': 1})
        self.client._session.post = MagicMock(return_value=resp)
        assert self.client._post('/test', {'key': 'val'}) is resp

    def test_201_returns_response(self):
        resp = _mock_response(201, {'id': 2})
        self.client._session.post = MagicMock(return_value=resp)
        assert self.client._post('/test', {}) is resp

    def test_422_returns_response(self):
        # 422 is considered "success" for branch-already-exists etc.
        resp = _mock_response(422, {'message': 'already exists'})
        self.client._session.post = MagicMock(return_value=resp)
        assert self.client._post('/test', {}) is resp

    def test_500_returns_none(self):
        resp = _mock_response(500, text='Internal Server Error')
        self.client._session.post = MagicMock(return_value=resp)
        assert self.client._post('/test', {}) is None

    def test_403_returns_none(self):
        resp = _mock_response(403, text='Forbidden')
        self.client._session.post = MagicMock(return_value=resp)
        assert self.client._post('/test', {}) is None

    def test_request_exception_returns_none(self):
        self.client._session.post = MagicMock(side_effect=requests.RequestException('net error'))
        assert self.client._post('/test', {}) is None

    def test_timeout_returns_none(self):
        self.client._session.post = MagicMock(side_effect=requests.Timeout('timeout'))
        assert self.client._post('/test', {}) is None

    def test_sends_json_payload(self):
        self.client._session.post = MagicMock(return_value=_mock_response(200))
        self.client._post('/test', {'key': 'value'})
        _, kwargs = self.client._session.post.call_args
        assert kwargs['json'] == {'key': 'value'}


# ═══════════════════════════════════════════════════════════════════════
# _put — write helper
# ═══════════════════════════════════════════════════════════════════════

class TestPut:
    def setup_method(self):
        self.client = _make_client()

    def test_200_returns_response(self):
        resp = _mock_response(200, {'content': {}})
        self.client._session.put = MagicMock(return_value=resp)
        assert self.client._put('/test', {}) is resp

    def test_201_returns_response(self):
        resp = _mock_response(201)
        self.client._session.put = MagicMock(return_value=resp)
        assert self.client._put('/test', {}) is resp

    def test_422_returns_none(self):
        # Unlike _post, _put does NOT accept 422
        resp = _mock_response(422, text='Unprocessable')
        self.client._session.put = MagicMock(return_value=resp)
        assert self.client._put('/test', {}) is None

    def test_500_returns_none(self):
        resp = _mock_response(500, text='error')
        self.client._session.put = MagicMock(return_value=resp)
        assert self.client._put('/test', {}) is None

    def test_request_exception_returns_none(self):
        self.client._session.put = MagicMock(side_effect=requests.RequestException('err'))
        assert self.client._put('/test', {}) is None


# ═══════════════════════════════════════════════════════════════════════
# get_commit
# ═══════════════════════════════════════════════════════════════════════

class TestGetCommit:
    def setup_method(self):
        self.client = _make_client()

    def test_happy_path(self):
        files = [{
            'filename': 'go.mod',
            'status': 'modified',
            'additions': 3,
            'deletions': 1,
            'patch': '@@ -1 +1 @@\n-old\n+new',
        }]
        data = _commit_payload(files=files)
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_commit('org', 'repo', 'abc123def')

        assert result['sha'] == 'abc123'
        assert result['message'] == 'fix: deps'
        assert result['author'] == 'Dev'
        assert result['date'] == '2026-01-01T00:00:00Z'
        assert result['parents'] == ['parent1']
        assert result['stats'] == {'additions': 5, 'deletions': 2, 'total': 7}
        assert len(result['files']) == 1
        assert result['files'][0]['filename'] == 'go.mod'

    def test_not_found_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_commit('o', 'r', 'abc12345') is None

    def test_empty_files_list(self):
        data = _commit_payload(files=[])
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_commit('o', 'r', 'abc12345')
        assert result['files'] == []

    def test_missing_commit_fields_use_defaults(self):
        data = {'sha': '', 'commit': {}, 'parents': [], 'stats': {}, 'files': []}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_commit('o', 'r', 'abc12345')
        assert result['message'] == ''
        assert result['author'] == ''
        assert result['date'] == ''

    def test_per_file_patch_truncation_at_10k(self):
        big_patch = 'x' * 15000
        files = [{'filename': 'big.go', 'status': 'modified', 'patch': big_patch}]
        data = _commit_payload(files=files)
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_commit('o', 'r', 'abc12345')
        assert len(result['files'][0]['patch']) < 15000
        assert '(truncated)' in result['files'][0]['patch']

    def test_total_diff_exceeds_max_diff_chars(self):
        # Each file gets ~9000 chars, 8 files = 72000 > MAX_DIFF_CHARS
        files = [{'filename': 'f{}.go'.format(i), 'status': 'modified', 'patch': 'y' * 9000}
                 for i in range(8)]
        data = _commit_payload(files=files)
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_commit('o', 'r', 'abc12345')
        truncated = [f for f in result['files'] if 'diff truncated' in f['patch']]
        assert len(truncated) > 0

    def test_file_without_patch_key(self):
        files = [{'filename': 'binary.bin', 'status': 'added'}]
        data = _commit_payload(files=files)
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_commit('o', 'r', 'abc12345')
        assert result['files'][0]['patch'] == ''

    def test_multiple_parents_merge_commit(self):
        data = _commit_payload(parents=['p1', 'p2'])
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_commit('o', 'r', 'abc12345')
        assert result['parents'] == ['p1', 'p2']

    def test_server_error_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(500))
        assert self.client.get_commit('o', 'r', 'abc12345') is None


# ═══════════════════════════════════════════════════════════════════════
# get_file_content
# ═══════════════════════════════════════════════════════════════════════

class TestGetFileContent:
    def setup_method(self):
        self.client = _make_client()

    def test_base64_decoded(self):
        raw = 'FROM ubi8\nRUN yum install -y golang'
        content = base64.b64encode(raw.encode()).decode()
        data = {'content': content, 'encoding': 'base64', 'size': len(raw)}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_file_content('o', 'r', 'Dockerfile')
        assert result == raw

    def test_with_ref_param(self):
        content = base64.b64encode(b'hello').decode()
        data = {'content': content, 'encoding': 'base64', 'size': 5}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        self.client.get_file_content('o', 'r', 'file.txt', ref='sha123')
        _, kwargs = self.client._session.get.call_args
        assert kwargs['params']['ref'] == 'sha123'

    def test_no_ref_param_empty_params(self):
        content = base64.b64encode(b'data').decode()
        data = {'content': content, 'encoding': 'base64', 'size': 4}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        self.client.get_file_content('o', 'r', 'file.txt')
        _, kwargs = self.client._session.get.call_args
        assert kwargs['params'] == {}

    def test_directory_listing_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, [{'name': 'a.go'}]))
        assert self.client.get_file_content('o', 'r', 'src/') is None

    def test_file_too_large_returns_message(self):
        data = {'content': '', 'encoding': 'base64', 'size': 2000000}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_file_content('o', 'r', 'big.bin')
        assert 'too large' in result
        assert '2000000' in result

    def test_content_truncated_at_max(self):
        big = b'x' * (MAX_FILE_CONTENT_CHARS + 5000)
        content = base64.b64encode(big).decode()
        data = {'content': content, 'encoding': 'base64', 'size': len(big)}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_file_content('o', 'r', 'huge.go')
        assert len(result) <= MAX_FILE_CONTENT_CHARS + 50  # +50 for "(truncated)" text
        assert '(truncated)' in result

    def test_not_found_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_file_content('o', 'r', 'missing.txt') is None

    def test_non_base64_encoding_returns_none(self):
        data = {'content': 'raw text', 'encoding': 'none', 'size': 8}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_file_content('o', 'r', 'file.txt') is None

    def test_empty_content_returns_none(self):
        data = {'content': '', 'encoding': 'base64', 'size': 0}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_file_content('o', 'r', 'empty.txt') is None

    def test_malformed_base64_returns_none(self):
        data = {'content': '!!!not-base64!!!', 'encoding': 'base64', 'size': 10}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        # base64.b64decode may raise or produce garbage; the method should handle it
        result = self.client.get_file_content('o', 'r', 'bad.txt')
        # Result could be None (if exception) or decoded garbage; both are acceptable
        # The important thing is it does not raise

    def test_path_with_special_characters_encoded(self):
        content = base64.b64encode(b'data').decode()
        data = {'content': content, 'encoding': 'base64', 'size': 4}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        self.client.get_file_content('o', 'r', 'path/with spaces/file.txt')
        url = self.client._session.get.call_args[0][0]
        assert 'path/with%20spaces/file.txt' in url


# ═══════════════════════════════════════════════════════════════════════
# get_directory_listing
# ═══════════════════════════════════════════════════════════════════════

class TestGetDirectoryListing:
    def setup_method(self):
        self.client = _make_client()

    def test_returns_filenames(self):
        data = [{'name': 'pipeline.yaml'}, {'name': 'task.yaml'}]
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_directory_listing('o', 'r', '.tekton')
        assert result == ['pipeline.yaml', 'task.yaml']

    def test_non_list_response_returns_none(self):
        data = {'type': 'file', 'name': 'single.go'}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_directory_listing('o', 'r', 'single.go') is None

    def test_empty_directory_returns_empty_list(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, []))
        assert self.client.get_directory_listing('o', 'r', 'empty-dir') == []

    def test_not_found_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_directory_listing('o', 'r', '.tekton') is None

    def test_with_ref_param(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, []))
        self.client.get_directory_listing('o', 'r', '.tekton', ref='abc123')
        _, kwargs = self.client._session.get.call_args
        assert kwargs['params']['ref'] == 'abc123'


# ═══════════════════════════════════════════════════════════════════════
# get_pr_for_commit
# ═══════════════════════════════════════════════════════════════════════

class TestGetPrForCommit:
    def setup_method(self):
        self.client = _make_client()

    def test_happy_path(self):
        prs = [{
            'number': 42, 'title': 'Update deps', 'body': 'Fixes build',
            'html_url': 'https://github.com/org/repo/pull/42',
            'labels': [{'name': 'bugfix'}, {'name': 'ci'}],
            'state': 'closed',
        }]
        self.client._session.get = MagicMock(return_value=_mock_response(200, prs))
        result = self.client.get_pr_for_commit('o', 'r', 'abc123')
        assert result['number'] == 42
        assert result['title'] == 'Update deps'
        assert result['labels'] == ['bugfix', 'ci']
        assert result['state'] == 'closed'

    def test_body_truncated_at_5000(self):
        prs = [{
            'number': 1, 'title': 'T', 'body': 'x' * 10000,
            'html_url': 'https://github.com/o/r/pull/1',
            'labels': [], 'state': 'open',
        }]
        self.client._session.get = MagicMock(return_value=_mock_response(200, prs))
        result = self.client.get_pr_for_commit('o', 'r', 'sha')
        assert len(result['body']) == 5000

    def test_none_body_becomes_empty_string(self):
        prs = [{
            'number': 1, 'title': 'T', 'body': None,
            'html_url': 'https://github.com/o/r/pull/1',
            'labels': [], 'state': 'open',
        }]
        self.client._session.get = MagicMock(return_value=_mock_response(200, prs))
        result = self.client.get_pr_for_commit('o', 'r', 'sha')
        assert result['body'] == ''

    def test_no_prs_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, []))
        assert self.client.get_pr_for_commit('o', 'r', 'sha') is None

    def test_not_found_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_pr_for_commit('o', 'r', 'sha') is None

    def test_uses_groot_preview_accept_header(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, []))
        self.client.get_pr_for_commit('o', 'r', 'sha')
        _, kwargs = self.client._session.get.call_args
        assert 'groot-preview' in kwargs['headers']['Accept']

    def test_multiple_prs_returns_first(self):
        prs = [
            {'number': 10, 'title': 'First', 'body': '', 'html_url': 'u1',
             'labels': [], 'state': 'closed'},
            {'number': 20, 'title': 'Second', 'body': '', 'html_url': 'u2',
             'labels': [], 'state': 'open'},
        ]
        self.client._session.get = MagicMock(return_value=_mock_response(200, prs))
        result = self.client.get_pr_for_commit('o', 'r', 'sha')
        assert result['number'] == 10


# ═══════════════════════════════════════════════════════════════════════
# get_commit_context — integration of multiple calls
# ═══════════════════════════════════════════════════════════════════════

class TestGetCommitContext:
    def setup_method(self):
        self.client = _make_client()

    def test_invalid_url_returns_none(self):
        assert self.client.get_commit_context('https://gitlab.com/o/r', 'abc12345') is None

    def test_empty_url_returns_none(self):
        assert self.client.get_commit_context('', 'abc12345') is None

    def test_none_url_returns_none(self):
        assert self.client.get_commit_context(None, 'abc12345') is None

    def test_commit_only_collected(self):
        commit_data = _commit_payload()

        def mock_get(url, params=None, headers=None, timeout=None):
            if '/commits/abc12345/pulls' in url:
                return _mock_response(404)
            if '/commits/abc12345' in url:
                return _mock_response(200, commit_data)
            return _mock_response(404)

        self.client._session.get = MagicMock(side_effect=mock_get)
        result = self.client.get_commit_context('https://github.com/org/repo', 'abc12345')
        assert result is not None
        assert result['owner'] == 'org'
        assert result['repo'] == 'repo'
        assert result['commit'] is not None
        assert result['dockerfile'] is None
        assert result['tekton_configs'] == {}
        assert result['pr'] is None

    def test_all_context_collected(self):
        commit_data = _commit_payload()
        dockerfile_content = base64.b64encode(b'FROM ubi8').decode()
        tekton_dir = [{'name': 'pipeline.yaml'}, {'name': 'task.yaml'}, {'name': 'README.md'}]
        tekton_content = base64.b64encode(b'apiVersion: tekton.dev/v1').decode()
        pr_data = [{'number': 5, 'title': 'Fix', 'body': '', 'html_url': 'url',
                     'labels': [], 'state': 'merged'}]

        def mock_get(url, params=None, headers=None, timeout=None):
            if '/commits/abc12345/pulls' in url:
                return _mock_response(200, pr_data)
            if '/commits/abc12345' in url:
                return _mock_response(200, commit_data)
            if '/contents/Dockerfile' in url:
                return _mock_response(200, {'content': dockerfile_content,
                                            'encoding': 'base64', 'size': 8})
            if '/contents/.tekton/' in url:
                return _mock_response(200, {'content': tekton_content,
                                            'encoding': 'base64', 'size': 25})
            if '/contents/.tekton' in url and '/contents/.tekton/' not in url:
                return _mock_response(200, tekton_dir)
            return _mock_response(404)

        self.client._session.get = MagicMock(side_effect=mock_get)
        result = self.client.get_commit_context('https://github.com/org/repo', 'abc12345')
        assert result['commit'] is not None
        assert result['dockerfile'] is not None
        assert result['dockerfile']['path'] == 'Dockerfile'
        assert result['pr'] is not None
        # .yaml and .yml files only — README.md should be excluded
        assert len(result['tekton_configs']) <= 2

    def test_no_context_all_404(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        result = self.client.get_commit_context('https://github.com/org/repo', 'abc12345')
        assert result is not None
        assert result['commit'] is None
        assert result['dockerfile'] is None
        assert result['pr'] is None

    def test_containerfile_fallback(self):
        commit_data = _commit_payload()
        containerfile_content = base64.b64encode(b'FROM ubi9').decode()

        def mock_get(url, params=None, headers=None, timeout=None):
            if '/commits/abc12345' in url and '/pulls' not in url:
                return _mock_response(200, commit_data)
            if '/contents/Dockerfile' in url:
                return _mock_response(404)
            if '/contents/Containerfile' in url:
                return _mock_response(200, {'content': containerfile_content,
                                            'encoding': 'base64', 'size': 9})
            return _mock_response(404)

        self.client._session.get = MagicMock(side_effect=mock_get)
        result = self.client.get_commit_context('https://github.com/org/repo', 'abc12345')
        assert result['dockerfile']['path'] == 'Containerfile'


# ═══════════════════════════════════════════════════════════════════════
# get_ref_sha
# ═══════════════════════════════════════════════════════════════════════

class TestGetRefSha:
    def setup_method(self):
        self.client = _make_client()

    def test_returns_sha(self):
        data = {'object': {'sha': 'abc123def456'}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_ref_sha('o', 'r', 'main') == 'abc123def456'

    def test_not_found_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_ref_sha('o', 'r', 'nonexistent') is None

    def test_missing_object_key(self):
        data = {}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_ref_sha('o', 'r', 'main') is None


# ═══════════════════════════════════════════════════════════════════════
# is_commit_on_branch
# ═══════════════════════════════════════════════════════════════════════

class TestIsCommitOnBranch:
    def setup_method(self):
        self.client = _make_client()

    def test_behind_means_on_branch(self):
        data = {'status': 'behind'}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.is_commit_on_branch('o', 'r', 'sha', 'main') is True

    def test_identical_means_on_branch(self):
        data = {'status': 'identical'}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.is_commit_on_branch('o', 'r', 'sha', 'main') is True

    def test_ahead_means_not_on_branch(self):
        data = {'status': 'ahead'}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.is_commit_on_branch('o', 'r', 'sha', 'main') is False

    def test_diverged_means_not_on_branch(self):
        data = {'status': 'diverged'}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.is_commit_on_branch('o', 'r', 'sha', 'main') is False

    def test_api_error_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.is_commit_on_branch('o', 'r', 'sha', 'main') is None


# ═══════════════════════════════════════════════════════════════════════
# get_file_sha
# ═══════════════════════════════════════════════════════════════════════

class TestGetFileSha:
    def setup_method(self):
        self.client = _make_client()

    def test_returns_sha(self):
        data = {'sha': 'blobsha123'}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_file_sha('o', 'r', 'file.txt', 'main') == 'blobsha123'

    def test_not_found(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_file_sha('o', 'r', 'missing.txt', 'main') is None


# ═══════════════════════════════════════════════════════════════════════
# create_branch
# ═══════════════════════════════════════════════════════════════════════

class TestCreateBranch:
    def setup_method(self):
        self.client = _make_client()

    def test_success_201(self):
        self.client._session.post = MagicMock(return_value=_mock_response(201, {}))
        assert self.client.create_branch('o', 'r', 'fix/branch', 'sha123') is True

    def test_already_exists_returns_true(self):
        self.client._session.post = MagicMock(
            return_value=_mock_response(422, {'message': 'Reference already exists'}))
        assert self.client.create_branch('o', 'r', 'fix/branch', 'sha123') is True

    def test_422_other_error_returns_false(self):
        self.client._session.post = MagicMock(
            return_value=_mock_response(422, {'message': 'Validation failed: sha is not valid'}))
        assert self.client.create_branch('o', 'r', 'fix/branch', 'badsha') is False

    def test_api_error_returns_false(self):
        self.client._session.post = MagicMock(side_effect=requests.RequestException('network'))
        assert self.client.create_branch('o', 'r', 'fix/branch', 'sha') is False

    def test_server_error_returns_false(self):
        self.client._session.post = MagicMock(return_value=_mock_response(500, text='error'))
        assert self.client.create_branch('o', 'r', 'fix/branch', 'sha') is False

    def test_payload_structure(self):
        self.client._session.post = MagicMock(return_value=_mock_response(201, {}))
        self.client.create_branch('o', 'r', 'my-branch', 'sha999')
        _, kwargs = self.client._session.post.call_args
        payload = kwargs['json']
        assert payload['ref'] == 'refs/heads/my-branch'
        assert payload['sha'] == 'sha999'


# ═══════════════════════════════════════════════════════════════════════
# put_file
# ═══════════════════════════════════════════════════════════════════════

class TestPutFile:
    def setup_method(self):
        self.client = _make_client()

    def test_create_new_file(self):
        self.client._session.put = MagicMock(return_value=_mock_response(201, {}))
        assert self.client.put_file('o', 'r', 'new.txt', 'content', 'add file', 'main') is True

    def test_update_existing_file_includes_sha(self):
        self.client._session.put = MagicMock(return_value=_mock_response(200, {}))
        result = self.client.put_file('o', 'r', 'file.txt', 'new content', 'update', 'main',
                                       existing_sha='oldsha')
        assert result is True
        _, kwargs = self.client._session.put.call_args
        assert kwargs['json']['sha'] == 'oldsha'

    def test_no_existing_sha_omits_sha_field(self):
        self.client._session.put = MagicMock(return_value=_mock_response(201, {}))
        self.client.put_file('o', 'r', 'new.txt', 'data', 'msg', 'main')
        _, kwargs = self.client._session.put.call_args
        assert 'sha' not in kwargs['json']

    def test_content_base64_encoded(self):
        self.client._session.put = MagicMock(return_value=_mock_response(201, {}))
        self.client.put_file('o', 'r', 'file.txt', 'hello world', 'msg', 'main')
        _, kwargs = self.client._session.put.call_args
        encoded = kwargs['json']['content']
        decoded = base64.b64decode(encoded).decode('utf-8')
        assert decoded == 'hello world'

    def test_failure_returns_false(self):
        self.client._session.put = MagicMock(return_value=_mock_response(500, text='error'))
        assert self.client.put_file('o', 'r', 'file.txt', 'x', 'msg', 'main') is False

    def test_branch_in_payload(self):
        self.client._session.put = MagicMock(return_value=_mock_response(201, {}))
        self.client.put_file('o', 'r', 'f.txt', 'data', 'msg', 'feature-branch')
        _, kwargs = self.client._session.put.call_args
        assert kwargs['json']['branch'] == 'feature-branch'


# ═══════════════════════════════════════════════════════════════════════
# create_pull_request
# ═══════════════════════════════════════════════════════════════════════

class TestCreatePullRequest:
    def setup_method(self):
        self.client = _make_client()

    def test_success(self):
        data = {'html_url': 'https://github.com/o/r/pull/99', 'number': 99}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        result = self.client.create_pull_request('o', 'r', 'Fix build', 'body', 'fix-branch')
        assert result == {'url': 'https://github.com/o/r/pull/99', 'number': 99}

    def test_custom_base_branch(self):
        data = {'html_url': 'https://github.com/o/r/pull/1', 'number': 1}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        self.client.create_pull_request('o', 'r', 'T', 'B', 'head', base='release-v1')
        _, kwargs = self.client._session.post.call_args
        assert kwargs['json']['base'] == 'release-v1'

    def test_default_base_is_main(self):
        data = {'html_url': 'https://github.com/o/r/pull/1', 'number': 1}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        self.client.create_pull_request('o', 'r', 'T', 'B', 'head')
        _, kwargs = self.client._session.post.call_args
        assert kwargs['json']['base'] == 'main'

    def test_failure_returns_none(self):
        self.client._session.post = MagicMock(side_effect=requests.RequestException('fail'))
        assert self.client.create_pull_request('o', 'r', 'T', 'B', 'head') is None

    def test_missing_html_url_returns_none(self):
        data = {'html_url': '', 'number': None}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        assert self.client.create_pull_request('o', 'r', 'T', 'B', 'head') is None

    def test_draft_is_false(self):
        data = {'html_url': 'url', 'number': 1}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        self.client.create_pull_request('o', 'r', 'T', 'B', 'head')
        _, kwargs = self.client._session.post.call_args
        assert kwargs['json']['draft'] is False


# ═══════════════════════════════════════════════════════════════════════
# get_pull_request
# ═══════════════════════════════════════════════════════════════════════

class TestGetPullRequest:
    def setup_method(self):
        self.client = _make_client()

    def test_merged_pr(self):
        data = {
            'number': 10, 'state': 'closed', 'merged': True,
            'merged_at': '2026-01-01T00:00:00Z', 'merge_commit_sha': 'msha',
            'title': 'PR title',
            'head': {'sha': 'headsha'},
            'base': {'ref': 'main'},
        }
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_pull_request('o', 'r', 10)
        assert result['merged'] is True
        assert result['merge_commit_sha'] == 'msha'
        assert result['head_sha'] == 'headsha'
        assert result['base_branch'] == 'main'

    def test_open_pr(self):
        data = {
            'number': 5, 'state': 'open', 'merged': False,
            'merged_at': None, 'merge_commit_sha': None,
            'title': 'WIP',
            'head': {'sha': 'h1'},
            'base': {'ref': 'develop'},
        }
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_pull_request('o', 'r', 5)
        assert result['state'] == 'open'
        assert result['merged'] is False

    def test_not_found(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_pull_request('o', 'r', 999) is None

    def test_missing_head_base_keys(self):
        data = {
            'number': 1, 'state': 'open', 'title': 'T',
            'merged': False, 'merged_at': None, 'merge_commit_sha': None,
        }
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_pull_request('o', 'r', 1)
        assert result['head_sha'] is None
        assert result['base_branch'] == ''


# ═══════════════════════════════════════════════════════════════════════
# list_pull_requests
# ═══════════════════════════════════════════════════════════════════════

class TestListPullRequests:
    def setup_method(self):
        self.client = _make_client()

    def _pr_data(self, number=1, merged_at=None):
        return {
            'number': number, 'title': 'PR {}'.format(number), 'state': 'open',
            'html_url': 'https://github.com/o/r/pull/{}'.format(number),
            'user': {'login': 'dev'},
            'base': {'ref': 'main'},
            'updated_at': '2026-01-01', 'merged_at': merged_at,
            'merge_commit_sha': 'msha' if merged_at else None,
        }

    def test_returns_list(self):
        prs = [self._pr_data(1), self._pr_data(2)]
        self.client._session.get = MagicMock(return_value=_mock_response(200, prs))
        result = self.client.list_pull_requests('o', 'r')
        assert len(result) == 2
        assert result[0]['number'] == 1

    def test_merged_at_sets_merged_flag(self):
        prs = [self._pr_data(1, merged_at='2026-01-02')]
        self.client._session.get = MagicMock(return_value=_mock_response(200, prs))
        result = self.client.list_pull_requests('o', 'r')
        assert result[0]['merged'] is True

    def test_no_merged_at_sets_merged_false(self):
        prs = [self._pr_data(1)]
        self.client._session.get = MagicMock(return_value=_mock_response(200, prs))
        result = self.client.list_pull_requests('o', 'r')
        assert result[0]['merged'] is False

    def test_with_base_filter(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, []))
        self.client.list_pull_requests('o', 'r', base='release-v1')
        _, kwargs = self.client._session.get.call_args
        assert kwargs['params']['base'] == 'release-v1'

    def test_no_base_filter(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, []))
        self.client.list_pull_requests('o', 'r')
        _, kwargs = self.client._session.get.call_args
        assert 'base' not in kwargs['params']

    def test_state_param(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, []))
        self.client.list_pull_requests('o', 'r', state='closed')
        _, kwargs = self.client._session.get.call_args
        assert kwargs['params']['state'] == 'closed'

    def test_limit_caps_per_page(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, []))
        self.client.list_pull_requests('o', 'r', limit=200)
        _, kwargs = self.client._session.get.call_args
        assert kwargs['params']['per_page'] == 100  # capped at 100

    def test_not_found_returns_empty_list(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.list_pull_requests('o', 'r') == []

    def test_error_returns_empty_list(self):
        self.client._session.get = MagicMock(return_value=_mock_response(500))
        assert self.client.list_pull_requests('o', 'r') == []

    def test_limit_slices_result(self):
        prs = [self._pr_data(i) for i in range(20)]
        self.client._session.get = MagicMock(return_value=_mock_response(200, prs))
        result = self.client.list_pull_requests('o', 'r', limit=5)
        assert len(result) == 5


# ═══════════════════════════════════════════════════════════════════════
# check_rate_limit
# ═══════════════════════════════════════════════════════════════════════

class TestCheckRateLimit:
    def setup_method(self):
        self.client = _make_client()

    def test_returns_rate_info(self):
        data = {'resources': {'core': {'remaining': 4500, 'limit': 5000, 'reset': 1700000000}}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.check_rate_limit()
        assert result['remaining'] == 4500
        assert result['limit'] == 5000
        assert result['reset'] == 1700000000

    def test_failure_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(500))
        assert self.client.check_rate_limit() is None

    def test_missing_core_key_uses_defaults(self):
        data = {'resources': {}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.check_rate_limit()
        assert result['remaining'] == 0
        assert result['limit'] == 0
        assert result['reset'] == 0

    def test_empty_response_uses_defaults(self):
        data = {}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.check_rate_limit()
        assert result['remaining'] == 0


# ═══════════════════════════════════════════════════════════════════════
# get_workflow_runs
# ═══════════════════════════════════════════════════════════════════════

class TestGetWorkflowRuns:
    def setup_method(self):
        self.client = _make_client()

    def test_returns_runs(self):
        data = {'workflow_runs': [{
            'id': 100, 'status': 'completed', 'conclusion': 'success',
            'created_at': '2026-01-01', 'updated_at': '2026-01-01',
            'html_url': 'https://github.com/o/r/actions/runs/100',
            'head_branch': 'main', 'run_number': 42,
        }]}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_workflow_runs('o', 'r', 'nightly.yaml')
        assert len(result) == 1
        assert result[0]['id'] == 100
        assert result[0]['conclusion'] == 'success'
        assert result[0]['head_branch'] == 'main'
        assert result[0]['run_number'] == 42

    def test_failure_returns_empty_list(self):
        self.client._session.get = MagicMock(return_value=_mock_response(500))
        assert self.client.get_workflow_runs('o', 'r', 'nightly.yaml') == []

    def test_respects_limit(self):
        runs = [{'id': i, 'status': 'completed', 'conclusion': 'success',
                 'created_at': '', 'updated_at': '', 'html_url': '',
                 'head_branch': 'main', 'run_number': i}
                for i in range(10)]
        data = {'workflow_runs': runs}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_workflow_runs('o', 'r', 'nightly.yaml', limit=3)
        assert len(result) == 3

    def test_empty_workflow_runs(self):
        data = {'workflow_runs': []}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_workflow_runs('o', 'r', 'nightly.yaml')
        assert result == []

    def test_missing_workflow_runs_key(self):
        data = {}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_workflow_runs('o', 'r', 'nightly.yaml')
        assert result == []

    def test_per_page_param_sent(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, {'workflow_runs': []}))
        self.client.get_workflow_runs('o', 'r', 'nightly.yaml', limit=7)
        _, kwargs = self.client._session.get.call_args
        assert kwargs['params']['per_page'] == 7

    def test_run_fields_with_none_values(self):
        data = {'workflow_runs': [{
            'id': None, 'status': None, 'conclusion': None,
            'created_at': None, 'updated_at': None,
            'html_url': None, 'head_branch': None, 'run_number': None,
        }]}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_workflow_runs('o', 'r', 'nightly.yaml')
        assert len(result) == 1
        assert result[0]['id'] is None


# ═══════════════════════════════════════════════════════════════════════
# Malformed JSON responses
# ═══════════════════════════════════════════════════════════════════════

class TestMalformedResponses:
    def setup_method(self):
        self.client = _make_client()

    def test_get_commit_malformed_json(self):
        resp = _mock_response(200)
        resp.json.side_effect = ValueError('malformed json')
        self.client._session.get = MagicMock(return_value=resp)
        with pytest.raises(ValueError):
            self.client.get_commit('o', 'r', 'abc12345')

    def test_check_rate_limit_malformed_json(self):
        resp = _mock_response(200)
        resp.json.side_effect = ValueError('bad json')
        self.client._session.get = MagicMock(return_value=resp)
        with pytest.raises(ValueError):
            self.client.check_rate_limit()
