import base64
import os
import sys
from unittest.mock import MagicMock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from clients.github_client import MAX_FILE_CONTENT_CHARS, GitHubClient, parse_github_repo
from clients.jira_client import JiraClient


def _mock_response(status_code=200, json_data=None, text=''):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


class TestParseGithubRepo:
    def test_standard_url(self):
        assert parse_github_repo('https://github.com/org/repo') == ('org', 'repo')

    def test_url_with_git_suffix(self):
        assert parse_github_repo('https://github.com/org/repo.git') == ('org', 'repo')

    def test_empty_url(self):
        assert parse_github_repo('') is None

    def test_none_url(self):
        assert parse_github_repo(None) is None

    def test_non_github_url(self):
        assert parse_github_repo('https://gitlab.com/org/repo') is None

    def test_trailing_slash(self):
        assert parse_github_repo('https://github.com/org/repo/') == ('org', 'repo')


class TestGitHubClientInit:
    def test_with_token(self):
        client = GitHubClient(token='ghp_abc123')
        assert client._session.headers['Authorization'] == 'token ghp_abc123'

    def test_without_token(self):
        client = GitHubClient()
        assert 'Authorization' not in client._session.headers


class TestGitHubClientGet:
    def setup_method(self):
        self.client = GitHubClient(token='test')

    def test_200_returns_response(self):
        mock_resp = _mock_response(200, {'data': 'ok'})
        self.client._session.get = MagicMock(return_value=mock_resp)
        result = self.client._get('/test')
        assert result is mock_resp

    def test_404_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client._get('/test') is None

    def test_403_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(403, {'message': 'Forbidden'}))
        assert self.client._get('/test') is None

    def test_401_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(401))
        assert self.client._get('/test') is None

    def test_429_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(429))
        assert self.client._get('/test') is None

    def test_500_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(500))
        assert self.client._get('/test') is None

    def test_timeout_returns_none(self):
        self.client._session.get = MagicMock(side_effect=requests.Timeout('timed out'))
        assert self.client._get('/test') is None

    def test_request_exception_returns_none(self):
        self.client._session.get = MagicMock(side_effect=requests.RequestException('fail'))
        assert self.client._get('/test') is None

    def test_custom_accept_header(self):
        mock_resp = _mock_response(200)
        self.client._session.get = MagicMock(return_value=mock_resp)
        self.client._get('/test', accept='application/vnd.github.groot-preview+json')
        _, kwargs = self.client._session.get.call_args
        assert kwargs['headers']['Accept'] == 'application/vnd.github.groot-preview+json'


class TestGitHubClientGetCommit:
    def setup_method(self):
        self.client = GitHubClient(token='test')

    def test_happy_path(self):
        data = {
            'sha': 'abc123',
            'commit': {
                'message': 'fix: update deps',
                'author': {'name': 'Dev', 'date': '2026-01-01T00:00:00Z'},
            },
            'parents': [{'sha': 'parent1'}],
            'stats': {'additions': 5, 'deletions': 2},
            'files': [{
                'filename': 'go.mod',
                'status': 'modified',
                'additions': 3,
                'deletions': 1,
                'patch': '@@ -1 +1 @@\n-old\n+new',
            }],
        }
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_commit('org', 'repo', 'abc123def')
        assert result['sha'] == 'abc123'
        assert result['message'] == 'fix: update deps'
        assert result['author'] == 'Dev'
        assert result['parents'] == ['parent1']
        assert len(result['files']) == 1
        assert result['files'][0]['filename'] == 'go.mod'

    def test_patch_truncation_per_file(self):
        big_patch = 'x' * 15000
        data = {
            'sha': 'abc', 'commit': {'message': '', 'author': {}},
            'parents': [], 'stats': {},
            'files': [{'filename': 'big.go', 'status': 'modified', 'patch': big_patch}],
        }
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_commit('o', 'r', 'abc12345')
        assert len(result['files'][0]['patch']) < 15000
        assert '(truncated)' in result['files'][0]['patch']

    def test_total_patch_exceeds_max_diff_chars(self):
        patch_chunk = 'y' * 9000
        files = [{'filename': 'f{}.go'.format(i), 'status': 'modified', 'patch': patch_chunk}
                 for i in range(8)]
        data = {
            'sha': 'abc', 'commit': {'message': '', 'author': {}},
            'parents': [], 'stats': {}, 'files': files,
        }
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_commit('o', 'r', 'abc12345')
        truncated = [f for f in result['files'] if 'diff truncated' in f['patch']]
        assert len(truncated) > 0

    def test_not_found(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_commit('o', 'r', 'abc12345') is None


class TestGitHubClientGetFileContent:
    def setup_method(self):
        self.client = GitHubClient(token='test')

    def test_happy_path_base64(self):
        content = base64.b64encode(b'FROM ubi8\nRUN yum install -y golang').decode()
        data = {'content': content, 'encoding': 'base64', 'size': 100}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_file_content('o', 'r', 'Dockerfile')
        assert 'FROM ubi8' in result

    def test_with_ref_param(self):
        content = base64.b64encode(b'hello').decode()
        data = {'content': content, 'encoding': 'base64', 'size': 5}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        self.client.get_file_content('o', 'r', 'Dockerfile', ref='abc123')
        _, kwargs = self.client._session.get.call_args
        assert kwargs['params']['ref'] == 'abc123'

    def test_directory_listing_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, [{'name': 'file.go'}]))
        assert self.client.get_file_content('o', 'r', 'src') is None

    def test_file_too_large(self):
        data = {'content': '', 'encoding': 'base64', 'size': 2000000}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_file_content('o', 'r', 'big.bin')
        assert 'too large' in result

    def test_content_exceeding_max_chars(self):
        big_content = base64.b64encode(b'x' * (MAX_FILE_CONTENT_CHARS + 5000)).decode()
        data = {'content': big_content, 'encoding': 'base64', 'size': MAX_FILE_CONTENT_CHARS + 5000}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_file_content('o', 'r', 'huge.go')
        assert '(truncated)' in result

    def test_not_found(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_file_content('o', 'r', 'missing.txt') is None


class TestGitHubClientGetDirectoryListing:
    def setup_method(self):
        self.client = GitHubClient(token='test')

    def test_returns_filenames(self):
        data = [{'name': 'pipeline.yaml'}, {'name': 'task.yaml'}]
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_directory_listing('o', 'r', '.tekton')
        assert result == ['pipeline.yaml', 'task.yaml']

    def test_non_list_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, {'type': 'file'}))
        assert self.client.get_directory_listing('o', 'r', 'file.go') is None

    def test_not_found(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_directory_listing('o', 'r', '.tekton') is None


class TestGitHubClientGetPrForCommit:
    def setup_method(self):
        self.client = GitHubClient(token='test')

    def test_happy_path(self):
        prs = [{
            'number': 42,
            'title': 'Update deps',
            'body': 'Fixes build',
            'html_url': 'https://github.com/org/repo/pull/42',
            'labels': [{'name': 'bugfix'}],
            'state': 'closed',
        }]
        self.client._session.get = MagicMock(return_value=_mock_response(200, prs))
        result = self.client.get_pr_for_commit('o', 'r', 'abc123')
        assert result['number'] == 42
        assert result['title'] == 'Update deps'
        assert result['labels'] == ['bugfix']
        assert result['state'] == 'closed'

    def test_no_prs(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, []))
        assert self.client.get_pr_for_commit('o', 'r', 'abc123') is None

    def test_not_found(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_pr_for_commit('o', 'r', 'abc123') is None


class TestGitHubClientGetCommitContext:
    def setup_method(self):
        self.client = GitHubClient(token='test')

    def test_bad_repository_url(self):
        assert self.client.get_commit_context('https://gitlab.com/o/r', 'abc12345') is None

    def test_partial_data(self):
        commit_data = {
            'sha': 'abc', 'commit': {'message': 'hi', 'author': {'name': 'A', 'date': ''}},
            'parents': [], 'stats': {}, 'files': [],
        }

        def mock_get(url, params=None, headers=None, timeout=None):
            if '/commits/abc12345/pulls' in url:
                return _mock_response(404)
            if '/commits/abc12345' in url:
                return _mock_response(200, commit_data)
            return _mock_response(404)

        self.client._session.get = MagicMock(side_effect=mock_get)
        result = self.client.get_commit_context('https://github.com/org/repo', 'abc12345')
        assert result is not None
        assert result['commit'] is not None
        assert result['dockerfile'] is None
        assert result['pr'] is None


class TestGitHubClientPost:
    def setup_method(self):
        self.client = GitHubClient(token='test')

    def test_200_returns_response(self):
        mock_resp = _mock_response(200, {'id': 1})
        self.client._session.post = MagicMock(return_value=mock_resp)
        assert self.client._post('/test', {}) is mock_resp

    def test_201_returns_response(self):
        mock_resp = _mock_response(201, {'id': 1})
        self.client._session.post = MagicMock(return_value=mock_resp)
        assert self.client._post('/test', {}) is mock_resp

    def test_422_returns_response(self):
        mock_resp = _mock_response(422, {'message': 'already exists'})
        self.client._session.post = MagicMock(return_value=mock_resp)
        assert self.client._post('/test', {}) is mock_resp

    def test_500_returns_none(self):
        mock_resp = _mock_response(500, text='Internal Server Error')
        self.client._session.post = MagicMock(return_value=mock_resp)
        assert self.client._post('/test', {}) is None

    def test_request_exception(self):
        self.client._session.post = MagicMock(side_effect=requests.RequestException('fail'))
        assert self.client._post('/test', {}) is None


class TestGitHubClientCreateBranch:
    def setup_method(self):
        self.client = GitHubClient(token='test')

    def test_success(self):
        self.client._session.post = MagicMock(return_value=_mock_response(201, {}))
        assert self.client.create_branch('o', 'r', 'fix/branch', 'sha123') is True

    def test_already_exists(self):
        self.client._session.post = MagicMock(
            return_value=_mock_response(422, {'message': 'Reference already exists'}))
        assert self.client.create_branch('o', 'r', 'fix/branch', 'sha123') is True

    def test_other_422_error(self):
        self.client._session.post = MagicMock(
            return_value=_mock_response(422, {'message': 'Validation failed'}))
        assert self.client.create_branch('o', 'r', 'fix/branch', 'sha123') is False

    def test_api_error(self):
        self.client._session.post = MagicMock(side_effect=requests.RequestException('fail'))
        assert self.client.create_branch('o', 'r', 'fix/branch', 'sha123') is False


class TestGitHubClientPutFile:
    def setup_method(self):
        self.client = GitHubClient(token='test')

    def test_create_new_file(self):
        self.client._session.put = MagicMock(return_value=_mock_response(201, {}))
        assert self.client.put_file('o', 'r', 'file.txt', 'content', 'add file', 'main') is True

    def test_update_existing_file(self):
        self.client._session.put = MagicMock(return_value=_mock_response(200, {}))
        result = self.client.put_file('o', 'r', 'file.txt', 'new', 'update', 'main',
                                      existing_sha='oldsha')
        assert result is True
        call_kwargs = self.client._session.put.call_args[1]
        assert call_kwargs['json']['sha'] == 'oldsha'

    def test_failure(self):
        self.client._session.put = MagicMock(return_value=_mock_response(500, text='error'))
        assert self.client.put_file('o', 'r', 'file.txt', 'x', 'msg', 'main') is False


class TestGitHubClientCreatePullRequest:
    def setup_method(self):
        self.client = GitHubClient(token='test')

    def test_success(self):
        data = {'html_url': 'https://github.com/o/r/pull/99', 'number': 99}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        result = self.client.create_pull_request('o', 'r', 'Fix', 'body', 'fix-branch')
        assert result == {'url': 'https://github.com/o/r/pull/99', 'number': 99}

    def test_failure(self):
        self.client._session.post = MagicMock(side_effect=requests.RequestException('fail'))
        assert self.client.create_pull_request('o', 'r', 'Fix', 'body', 'fix-branch') is None


class TestGitHubClientGetPullRequest:
    def setup_method(self):
        self.client = GitHubClient(token='test')

    def test_happy_path(self):
        data = {
            'number': 10, 'state': 'closed', 'merged': True,
            'merged_at': '2026-01-01', 'merge_commit_sha': 'msha',
            'title': 'PR title',
            'head': {'sha': 'headsha'},
            'base': {'ref': 'main'},
        }
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_pull_request('o', 'r', 10)
        assert result['number'] == 10
        assert result['merged'] is True
        assert result['head_sha'] == 'headsha'
        assert result['base_branch'] == 'main'

    def test_not_found(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_pull_request('o', 'r', 999) is None


class TestGitHubClientListPullRequests:
    def setup_method(self):
        self.client = GitHubClient(token='test')

    def test_returns_list(self):
        prs = [{
            'number': 1, 'title': 'PR 1', 'state': 'open',
            'html_url': 'https://github.com/o/r/pull/1',
            'user': {'login': 'dev'},
            'base': {'ref': 'main'},
            'updated_at': '2026-01-01', 'merged_at': None, 'merge_commit_sha': None,
        }]
        self.client._session.get = MagicMock(return_value=_mock_response(200, prs))
        result = self.client.list_pull_requests('o', 'r')
        assert len(result) == 1
        assert result[0]['number'] == 1
        assert result[0]['merged'] is False

    def test_with_base_filter(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, []))
        self.client.list_pull_requests('o', 'r', base='release-v1')
        _, kwargs = self.client._session.get.call_args
        assert kwargs['params']['base'] == 'release-v1'

    def test_not_found(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.list_pull_requests('o', 'r') == []


class TestGitHubClientCheckRateLimit:
    def setup_method(self):
        self.client = GitHubClient(token='test')

    def test_returns_rate_info(self):
        data = {'resources': {'core': {'remaining': 4500, 'limit': 5000, 'reset': 1700000000}}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.check_rate_limit()
        assert result['remaining'] == 4500
        assert result['limit'] == 5000

    def test_failure(self):
        self.client._session.get = MagicMock(return_value=_mock_response(500))
        assert self.client.check_rate_limit() is None


class TestGitHubClientGetWorkflowRuns:
    def setup_method(self):
        self.client = GitHubClient(token='test')

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
        assert result[0]['conclusion'] == 'success'

    def test_failure(self):
        self.client._session.get = MagicMock(return_value=_mock_response(500))
        assert self.client.get_workflow_runs('o', 'r', 'nightly.yaml') == []


class TestJiraClientInit:
    def test_sets_auth_and_project(self):
        client = JiraClient('https://jira.example.com', 'user@test.com', 'tok123', 'PROJ')
        assert client._session.auth == ('user@test.com', 'tok123')
        assert client._project == 'PROJ'
        assert client._session.headers['Content-Type'] == 'application/json'


class TestJiraClientCheckTokenHealth:
    def setup_method(self):
        self.client = JiraClient('https://jira.example.com', 'u@t.com', 'tok', 'P')

    def test_valid(self):
        self.client._session.get = MagicMock(
            return_value=_mock_response(200, {'displayName': 'John Doe'}))
        result = self.client.check_token_health()
        assert result['status'] == 'valid'
        assert result['user'] == 'John Doe'

    def test_expired_401(self):
        self.client._session.get = MagicMock(return_value=_mock_response(401))
        result = self.client.check_token_health()
        assert result['status'] == 'expired'

    def test_forbidden_403(self):
        self.client._session.get = MagicMock(return_value=_mock_response(403))
        result = self.client.check_token_health()
        assert result['status'] == 'forbidden'

    def test_unreachable(self):
        self.client._session.get = MagicMock(side_effect=requests.ConnectionError('refused'))
        result = self.client.check_token_health()
        assert result['status'] == 'unreachable'

    def test_missing_token(self):
        self.client._session.auth = ('user', '')
        result = self.client.check_token_health()
        assert result['status'] == 'missing'

    def test_other_status(self):
        self.client._session.get = MagicMock(return_value=_mock_response(502))
        result = self.client.check_token_health()
        assert result['status'] == 'expired'


class TestJiraClientCreateIssue:
    def setup_method(self):
        self.client = JiraClient('https://jira.example.com', 'u@t.com', 'tok', 'PROJ')

    def test_success(self):
        data = {'key': 'PROJ-123', 'id': '10001'}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        result = self.client.create_issue('Build failed', 'Description text')
        assert result['key'] == 'PROJ-123'
        assert result['url'] == 'https://jira.example.com/browse/PROJ-123'

    def test_with_priority_labels_components(self):
        data = {'key': 'PROJ-1', 'id': '1'}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        self.client.create_issue('Sum', 'Desc', priority='Blocker',
                                 labels=['ci', 'auto'], components=['Backend'])
        call_kwargs = self.client._session.post.call_args[1]
        fields = call_kwargs['json']['fields']
        assert fields['priority'] == {'name': 'Blocker'}
        assert fields['labels'] == ['ci', 'auto']
        assert fields['components'] == [{'name': 'Backend'}]

    def test_401(self):
        self.client._session.post = MagicMock(return_value=_mock_response(401))
        assert self.client.create_issue('S', 'D') is None

    def test_403(self):
        self.client._session.post = MagicMock(return_value=_mock_response(403))
        assert self.client.create_issue('S', 'D') is None

    def test_400(self):
        self.client._session.post = MagicMock(
            return_value=_mock_response(400, {'errors': {'summary': 'required'}}))
        assert self.client.create_issue('', 'D') is None

    def test_other_status(self):
        self.client._session.post = MagicMock(return_value=_mock_response(500))
        assert self.client.create_issue('S', 'D') is None

    def test_request_exception(self):
        self.client._session.post = MagicMock(side_effect=requests.RequestException('fail'))
        assert self.client.create_issue('S', 'D') is None


class TestJiraClientAddComment:
    def setup_method(self):
        self.client = JiraClient('https://jira.example.com', 'u@t.com', 'tok', 'P')

    def test_success(self):
        comment = {'id': '100', 'body': 'test comment'}
        self.client._session.post = MagicMock(return_value=_mock_response(201, comment))
        result = self.client.add_comment('PROJ-1', 'test comment')
        assert result['body'] == 'test comment'

    def test_failure(self):
        self.client._session.post = MagicMock(return_value=_mock_response(500))
        assert self.client.add_comment('PROJ-1', 'text') is None


class TestJiraClientGetTransitions:
    def setup_method(self):
        self.client = JiraClient('https://jira.example.com', 'u@t.com', 'tok', 'P')

    def test_returns_transitions(self):
        data = {'transitions': [{'id': '31', 'name': 'In Progress'}]}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_transitions('PROJ-1')
        assert len(result) == 1
        assert result[0]['name'] == 'In Progress'

    def test_not_found(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_transitions('PROJ-999') == []


class TestJiraClientTransitionIssue:
    def setup_method(self):
        self.client = JiraClient('https://jira.example.com', 'u@t.com', 'tok', 'P')

    def test_success(self):
        self.client._session.post = MagicMock(return_value=_mock_response(204))
        assert self.client.transition_issue('PROJ-1', '31') is True

    def test_failure(self):
        self.client._session.post = MagicMock(return_value=_mock_response(400))
        assert self.client.transition_issue('PROJ-1', '31') is False


class TestJiraClientGetComments:
    def setup_method(self):
        self.client = JiraClient('https://jira.example.com', 'u@t.com', 'tok', 'P')

    def test_returns_comments(self):
        data = {'comments': [{'id': '1', 'body': 'hi'}]}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_comments('PROJ-1')
        assert len(result) == 1

    def test_error(self):
        self.client._session.get = MagicMock(side_effect=requests.RequestException('fail'))
        assert self.client.get_comments('PROJ-1') == []


class TestJiraClientGetIssue:
    def setup_method(self):
        self.client = JiraClient('https://jira.example.com', 'u@t.com', 'tok', 'P')

    def test_returns_issue(self):
        data = {
            'key': 'PROJ-5',
            'fields': {
                'summary': 'Build broken',
                'description': 'Details here',
                'status': {
                    'name': 'Open',
                    'statusCategory': {'key': 'new'},
                },
                'comment': {'comments': [{'body': 'c1'}]},
            },
        }
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_issue('PROJ-5')
        assert result['key'] == 'PROJ-5'
        assert result['summary'] == 'Build broken'
        assert result['status'] == 'Open'
        assert result['status_category'] == 'new'
        assert len(result['comments']) == 1

    def test_not_found(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_issue('PROJ-999') is None


class TestJiraClientGetIssueStatus:
    def setup_method(self):
        self.client = JiraClient('https://jira.example.com', 'u@t.com', 'tok', 'P')

    def test_returns_category_key(self):
        data = {'fields': {'status': {'statusCategory': {'key': 'done'}}}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_issue_status('PROJ-1') == 'done'

    def test_error(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_issue_status('PROJ-999') is None


class TestJiraClientSearchBlockers:
    def setup_method(self):
        self.client = JiraClient('https://jira.example.com', 'u@t.com', 'tok', 'P')

    def test_returns_issues(self):
        data = {'issues': [{
            'key': 'PROJ-10',
            'fields': {
                'summary': 'Critical bug',
                'status': {'name': 'Open', 'statusCategory': {'key': 'new'}},
                'assignee': {'displayName': 'Dev'},
                'created': '2026-01-01', 'updated': '2026-01-02',
                'priority': {'name': 'Blocker'},
                'resolution': None,
                'labels': ['ci'],
            },
        }]}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.search_blockers('PROJ')
        assert len(result) == 1
        assert result[0]['key'] == 'PROJ-10'
        assert result[0]['assignee'] == 'Dev'

    def test_with_fix_versions(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, {'issues': []}))
        self.client.search_blockers('PROJ', fix_versions=['v3.5', 'v3.6'])
        url = self.client._session.get.call_args[0][0]
        assert 'fixVersion' in url

    def test_error(self):
        self.client._session.get = MagicMock(return_value=_mock_response(500))
        assert self.client.search_blockers('PROJ') == []


class TestJiraClientGetIssueChangelog:
    def setup_method(self):
        self.client = JiraClient('https://jira.example.com', 'u@t.com', 'tok', 'P')

    def test_returns_entries(self):
        data = {
            'fields': {'summary': 'issue'},
            'changelog': {'histories': [{
                'created': '2026-01-01',
                'author': {'displayName': 'Dev'},
                'items': [{
                    'field': 'status',
                    'fromString': 'Open',
                    'toString': 'In Progress',
                }],
            }]},
        }
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_issue_changelog('PROJ-1')
        assert len(result) == 1
        assert result[0]['field'] == 'status'
        assert result[0]['to'] == 'In Progress'

    def test_error(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_issue_changelog('PROJ-999') == []
