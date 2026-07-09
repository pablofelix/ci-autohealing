"""Comprehensive tests for JiraClient — auth, CRUD, edge cases, errors."""

import os
import sys
from unittest.mock import MagicMock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from clients.jira_client import JIRA_API_VERSION, JiraClient

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _mock_response(status_code=200, json_data=None, text=''):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


def _make_client(base_url='https://jira.example.com', email='user@test.com',
                 token='tok123', project='PROJ'):
    return JiraClient(base_url, email, token, project)


# ═══════════════════════════════════════════════════════════════════════
# Constructor and _api helper
# ═══════════════════════════════════════════════════════════════════════

class TestJiraClientInit:
    def test_sets_auth(self):
        client = _make_client()
        assert client._session.auth == ('user@test.com', 'tok123')

    def test_sets_project(self):
        client = _make_client()
        assert client._project == 'PROJ'

    def test_headers_set(self):
        client = _make_client()
        assert client._session.headers['Accept'] == 'application/json'
        assert client._session.headers['Content-Type'] == 'application/json'
        assert 'ci-autohealing' in client._session.headers['User-Agent']

    def test_base_url_trailing_slash_stripped(self):
        client = JiraClient('https://jira.example.com/', 'u@t.com', 'tok', 'P')
        assert client._base_url == 'https://jira.example.com'

    def test_api_path_construction(self):
        client = _make_client()
        url = client._api('myself')
        assert url == 'https://jira.example.com/rest/api/2/myself'

    def test_api_version_constant(self):
        assert JIRA_API_VERSION == '2'


# ═══════════════════════════════════════════════════════════════════════
# _get — internal helper
# ═══════════════════════════════════════════════════════════════════════

class TestJiraGet:
    def setup_method(self):
        self.client = _make_client()

    def test_200_returns_json(self):
        data = {'key': 'PROJ-1', 'fields': {}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client._get('issue/PROJ-1') == data

    def test_404_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client._get('issue/PROJ-999') is None

    def test_500_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(500))
        assert self.client._get('issue/PROJ-1') is None

    def test_401_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(401))
        assert self.client._get('myself') is None

    def test_request_exception_returns_none(self):
        self.client._session.get = MagicMock(side_effect=requests.RequestException('err'))
        assert self.client._get('issue/PROJ-1') is None

    def test_timeout_returns_none(self):
        self.client._session.get = MagicMock(side_effect=requests.Timeout('timeout'))
        assert self.client._get('issue/PROJ-1') is None

    def test_connection_error_returns_none(self):
        self.client._session.get = MagicMock(side_effect=requests.ConnectionError('refused'))
        assert self.client._get('issue/PROJ-1') is None


# ═══════════════════════════════════════════════════════════════════════
# check_token_health
# ═══════════════════════════════════════════════════════════════════════

class TestCheckTokenHealth:
    def setup_method(self):
        self.client = _make_client()

    def test_valid_token(self):
        data = {'displayName': 'John Doe', 'emailAddress': 'john@test.com'}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.check_token_health()
        assert result['status'] == 'valid'
        assert result['user'] == 'John Doe'
        assert 'John Doe' in result['message']

    def test_valid_token_missing_display_name(self):
        data = {}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.check_token_health()
        assert result['status'] == 'valid'
        assert result['user'] == ''

    def test_expired_401(self):
        self.client._session.get = MagicMock(return_value=_mock_response(401))
        result = self.client.check_token_health()
        assert result['status'] == 'expired'
        assert result['user'] is None
        assert 'expired' in result['message'].lower() or 'invalid' in result['message'].lower()

    def test_forbidden_403(self):
        self.client._session.get = MagicMock(return_value=_mock_response(403))
        result = self.client.check_token_health()
        assert result['status'] == 'forbidden'
        assert result['user'] is None

    def test_unreachable_connection_error(self):
        self.client._session.get = MagicMock(side_effect=requests.ConnectionError('refused'))
        result = self.client.check_token_health()
        assert result['status'] == 'unreachable'
        assert result['user'] is None
        assert 'unreachable' in result['message'].lower()

    def test_unreachable_timeout(self):
        self.client._session.get = MagicMock(side_effect=requests.Timeout('timed out'))
        result = self.client.check_token_health()
        assert result['status'] == 'unreachable'

    def test_unreachable_generic_request_exception(self):
        self.client._session.get = MagicMock(side_effect=requests.RequestException('dns fail'))
        result = self.client.check_token_health()
        assert result['status'] == 'unreachable'

    def test_missing_token_empty_string(self):
        self.client._session.auth = ('user@test.com', '')
        result = self.client.check_token_health()
        assert result['status'] == 'missing'
        assert 'not set' in result['message'].lower()

    def test_missing_token_none(self):
        self.client._session.auth = ('user@test.com', None)
        result = self.client.check_token_health()
        assert result['status'] == 'missing'

    def test_missing_auth_tuple(self):
        self.client._session.auth = None
        result = self.client.check_token_health()
        assert result['status'] == 'missing'

    def test_other_status_code_falls_through_as_expired(self):
        self.client._session.get = MagicMock(return_value=_mock_response(502))
        result = self.client.check_token_health()
        assert result['status'] == 'expired'
        assert '502' in result['message']

    def test_500_falls_through(self):
        self.client._session.get = MagicMock(return_value=_mock_response(500))
        result = self.client.check_token_health()
        assert result['status'] == 'expired'

    def test_error_message_truncation(self):
        long_err = 'x' * 300
        self.client._session.get = MagicMock(
            side_effect=requests.RequestException(long_err))
        result = self.client.check_token_health()
        # Message should include at most 100 chars of the error
        assert len(result['message']) < 300


# ═══════════════════════════════════════════════════════════════════════
# create_issue
# ═══════════════════════════════════════════════════════════════════════

class TestCreateIssue:
    def setup_method(self):
        self.client = _make_client()

    def test_success_201(self):
        data = {'key': 'PROJ-123', 'id': '10001'}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        result = self.client.create_issue('Build failed', 'Description text')
        assert result['key'] == 'PROJ-123'
        assert result['id'] == '10001'
        assert result['url'] == 'https://jira.example.com/browse/PROJ-123'

    def test_default_issue_type_is_bug(self):
        data = {'key': 'PROJ-1', 'id': '1'}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        self.client.create_issue('Summary', 'Desc')
        call_kwargs = self.client._session.post.call_args[1]
        fields = call_kwargs['json']['fields']
        assert fields['issuetype'] == {'name': 'Bug'}

    def test_custom_issue_type(self):
        data = {'key': 'PROJ-1', 'id': '1'}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        self.client.create_issue('Summary', 'Desc', issue_type='Task')
        call_kwargs = self.client._session.post.call_args[1]
        fields = call_kwargs['json']['fields']
        assert fields['issuetype'] == {'name': 'Task'}

    def test_with_priority(self):
        data = {'key': 'PROJ-1', 'id': '1'}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        self.client.create_issue('S', 'D', priority='Blocker')
        call_kwargs = self.client._session.post.call_args[1]
        fields = call_kwargs['json']['fields']
        assert fields['priority'] == {'name': 'Blocker'}

    def test_without_priority_omits_field(self):
        data = {'key': 'PROJ-1', 'id': '1'}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        self.client.create_issue('S', 'D')
        call_kwargs = self.client._session.post.call_args[1]
        fields = call_kwargs['json']['fields']
        assert 'priority' not in fields

    def test_with_labels(self):
        data = {'key': 'PROJ-1', 'id': '1'}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        self.client.create_issue('S', 'D', labels=['ci', 'auto-created'])
        call_kwargs = self.client._session.post.call_args[1]
        fields = call_kwargs['json']['fields']
        assert fields['labels'] == ['ci', 'auto-created']

    def test_without_labels_omits_field(self):
        data = {'key': 'PROJ-1', 'id': '1'}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        self.client.create_issue('S', 'D')
        call_kwargs = self.client._session.post.call_args[1]
        fields = call_kwargs['json']['fields']
        assert 'labels' not in fields

    def test_with_components(self):
        data = {'key': 'PROJ-1', 'id': '1'}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        self.client.create_issue('S', 'D', components=['Backend', 'API'])
        call_kwargs = self.client._session.post.call_args[1]
        fields = call_kwargs['json']['fields']
        assert fields['components'] == [{'name': 'Backend'}, {'name': 'API'}]

    def test_without_components_omits_field(self):
        data = {'key': 'PROJ-1', 'id': '1'}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        self.client.create_issue('S', 'D')
        call_kwargs = self.client._session.post.call_args[1]
        fields = call_kwargs['json']['fields']
        assert 'components' not in fields

    def test_project_key_from_constructor(self):
        data = {'key': 'PROJ-1', 'id': '1'}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        self.client.create_issue('S', 'D')
        call_kwargs = self.client._session.post.call_args[1]
        fields = call_kwargs['json']['fields']
        assert fields['project'] == {'key': 'PROJ'}

    def test_401_unauthorized_returns_none(self):
        self.client._session.post = MagicMock(return_value=_mock_response(401))
        assert self.client.create_issue('S', 'D') is None

    def test_403_forbidden_returns_none(self):
        self.client._session.post = MagicMock(return_value=_mock_response(403))
        assert self.client.create_issue('S', 'D') is None

    def test_400_bad_request_with_errors(self):
        resp = _mock_response(400, {'errors': {'summary': 'Field is required'}})
        self.client._session.post = MagicMock(return_value=resp)
        assert self.client.create_issue('', 'D') is None

    def test_400_bad_request_malformed_json(self):
        resp = _mock_response(400)
        resp.json.side_effect = ValueError('bad json')
        self.client._session.post = MagicMock(return_value=resp)
        # Should not raise, just return None
        assert self.client.create_issue('S', 'D') is None

    def test_500_unexpected_status(self):
        self.client._session.post = MagicMock(return_value=_mock_response(500))
        assert self.client.create_issue('S', 'D') is None

    def test_502_unexpected_status(self):
        self.client._session.post = MagicMock(return_value=_mock_response(502))
        assert self.client.create_issue('S', 'D') is None

    def test_request_exception(self):
        self.client._session.post = MagicMock(side_effect=requests.RequestException('net err'))
        assert self.client.create_issue('S', 'D') is None

    def test_timeout(self):
        self.client._session.post = MagicMock(side_effect=requests.Timeout('timeout'))
        assert self.client.create_issue('S', 'D') is None

    def test_connection_error(self):
        self.client._session.post = MagicMock(side_effect=requests.ConnectionError('refused'))
        assert self.client.create_issue('S', 'D') is None

    def test_all_fields_together(self):
        data = {'key': 'PROJ-99', 'id': '99'}
        self.client._session.post = MagicMock(return_value=_mock_response(201, data))
        result = self.client.create_issue(
            'Build broken',
            'Full description',
            issue_type='Story',
            priority='Critical',
            labels=['ci', 'auto'],
            components=['Frontend', 'Backend'],
        )
        assert result['key'] == 'PROJ-99'
        call_kwargs = self.client._session.post.call_args[1]
        fields = call_kwargs['json']['fields']
        assert fields['issuetype'] == {'name': 'Story'}
        assert fields['priority'] == {'name': 'Critical'}
        assert fields['labels'] == ['ci', 'auto']
        assert len(fields['components']) == 2


# ═══════════════════════════════════════════════════════════════════════
# add_comment
# ═══════════════════════════════════════════════════════════════════════

class TestAddComment:
    def setup_method(self):
        self.client = _make_client()

    def test_success(self):
        comment = {'id': '100', 'body': 'test comment', 'created': '2026-01-01'}
        self.client._session.post = MagicMock(return_value=_mock_response(201, comment))
        result = self.client.add_comment('PROJ-1', 'test comment')
        assert result['id'] == '100'
        assert result['body'] == 'test comment'

    def test_payload_structure(self):
        self.client._session.post = MagicMock(return_value=_mock_response(201, {'id': '1'}))
        self.client.add_comment('PROJ-1', 'My comment body')
        call_kwargs = self.client._session.post.call_args[1]
        assert call_kwargs['json'] == {'body': 'My comment body'}

    def test_url_includes_issue_key(self):
        self.client._session.post = MagicMock(return_value=_mock_response(201, {'id': '1'}))
        self.client.add_comment('PROJ-42', 'body')
        url = self.client._session.post.call_args[0][0]
        assert 'issue/PROJ-42/comment' in url

    def test_failure_status_code_returns_none(self):
        self.client._session.post = MagicMock(return_value=_mock_response(500))
        assert self.client.add_comment('PROJ-1', 'text') is None

    def test_404_returns_none(self):
        self.client._session.post = MagicMock(return_value=_mock_response(404))
        assert self.client.add_comment('PROJ-999', 'text') is None

    def test_request_exception_returns_none(self):
        self.client._session.post = MagicMock(side_effect=requests.RequestException('fail'))
        assert self.client.add_comment('PROJ-1', 'text') is None

    def test_timeout_returns_none(self):
        self.client._session.post = MagicMock(side_effect=requests.Timeout('timeout'))
        assert self.client.add_comment('PROJ-1', 'text') is None


# ═══════════════════════════════════════════════════════════════════════
# get_transitions
# ═══════════════════════════════════════════════════════════════════════

class TestGetTransitions:
    def setup_method(self):
        self.client = _make_client()

    def test_returns_transitions(self):
        data = {'transitions': [
            {'id': '31', 'name': 'In Progress'},
            {'id': '41', 'name': 'Done'},
        ]}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_transitions('PROJ-1')
        assert len(result) == 2
        assert result[0]['name'] == 'In Progress'
        assert result[1]['name'] == 'Done'

    def test_empty_transitions(self):
        data = {'transitions': []}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_transitions('PROJ-1')
        assert result == []

    def test_not_found_returns_empty_list(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_transitions('PROJ-999') == []

    def test_error_returns_empty_list(self):
        self.client._session.get = MagicMock(side_effect=requests.RequestException('fail'))
        assert self.client.get_transitions('PROJ-1') == []

    def test_missing_transitions_key(self):
        data = {}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_transitions('PROJ-1')
        assert result == []


# ═══════════════════════════════════════════════════════════════════════
# transition_issue
# ═══════════════════════════════════════════════════════════════════════

class TestTransitionIssue:
    def setup_method(self):
        self.client = _make_client()

    def test_success_204(self):
        self.client._session.post = MagicMock(return_value=_mock_response(204))
        assert self.client.transition_issue('PROJ-1', '31') is True

    def test_sends_transition_id_as_string(self):
        self.client._session.post = MagicMock(return_value=_mock_response(204))
        self.client.transition_issue('PROJ-1', 42)
        call_kwargs = self.client._session.post.call_args[1]
        assert call_kwargs['json'] == {'transition': {'id': '42'}}

    def test_failure_400(self):
        self.client._session.post = MagicMock(return_value=_mock_response(400))
        assert self.client.transition_issue('PROJ-1', '31') is False

    def test_failure_404(self):
        self.client._session.post = MagicMock(return_value=_mock_response(404))
        assert self.client.transition_issue('PROJ-999', '31') is False

    def test_failure_500(self):
        self.client._session.post = MagicMock(return_value=_mock_response(500))
        assert self.client.transition_issue('PROJ-1', '31') is False

    def test_request_exception(self):
        self.client._session.post = MagicMock(side_effect=requests.RequestException('err'))
        assert self.client.transition_issue('PROJ-1', '31') is False

    def test_timeout(self):
        self.client._session.post = MagicMock(side_effect=requests.Timeout('timeout'))
        assert self.client.transition_issue('PROJ-1', '31') is False


# ═══════════════════════════════════════════════════════════════════════
# get_comments
# ═══════════════════════════════════════════════════════════════════════

class TestGetComments:
    def setup_method(self):
        self.client = _make_client()

    def test_returns_comments(self):
        data = {'comments': [
            {'id': '1', 'body': 'First comment', 'created': '2026-01-01'},
            {'id': '2', 'body': 'Second comment', 'created': '2026-01-02'},
        ]}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_comments('PROJ-1')
        assert len(result) == 2
        assert result[0]['body'] == 'First comment'

    def test_empty_comments(self):
        data = {'comments': []}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_comments('PROJ-1') == []

    def test_error_returns_empty_list(self):
        self.client._session.get = MagicMock(side_effect=requests.RequestException('fail'))
        assert self.client.get_comments('PROJ-1') == []

    def test_not_found_returns_empty_list(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_comments('PROJ-999') == []

    def test_missing_comments_key(self):
        data = {}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_comments('PROJ-1')
        assert result == []


# ═══════════════════════════════════════════════════════════════════════
# get_comment (single comment by ID)
# ═══════════════════════════════════════════════════════════════════════

class TestGetComment:
    def setup_method(self):
        self.client = _make_client()

    def test_returns_comment(self):
        data = {'id': '42', 'body': 'A comment', 'created': '2026-01-01'}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_comment('PROJ-1', '42')
        assert result['id'] == '42'
        assert result['body'] == 'A comment'

    def test_not_found_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_comment('PROJ-1', '999') is None

    def test_url_structure(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, {}))
        self.client.get_comment('PROJ-5', '123')
        url = self.client._session.get.call_args[0][0]
        assert 'issue/PROJ-5/comment/123' in url


# ═══════════════════════════════════════════════════════════════════════
# get_issue
# ═══════════════════════════════════════════════════════════════════════

class TestGetIssue:
    def setup_method(self):
        self.client = _make_client()

    def test_returns_full_issue(self):
        data = {
            'key': 'PROJ-5',
            'fields': {
                'summary': 'Build broken',
                'description': 'Details here',
                'status': {
                    'name': 'Open',
                    'statusCategory': {'key': 'new'},
                },
                'comment': {'comments': [{'body': 'c1'}, {'body': 'c2'}]},
            },
        }
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_issue('PROJ-5')
        assert result['key'] == 'PROJ-5'
        assert result['summary'] == 'Build broken'
        assert result['description'] == 'Details here'
        assert result['status'] == 'Open'
        assert result['status_category'] == 'new'
        assert len(result['comments']) == 2

    def test_not_found_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_issue('PROJ-999') is None

    def test_missing_fields_use_defaults(self):
        data = {'key': 'PROJ-1', 'fields': {}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_issue('PROJ-1')
        assert result['summary'] == ''
        assert result['description'] == ''
        assert result['status'] == ''
        assert result['status_category'] == ''
        assert result['comments'] == []

    def test_missing_status_category(self):
        data = {
            'key': 'PROJ-1',
            'fields': {
                'summary': 'S',
                'description': 'D',
                'status': {'name': 'Open'},
                'comment': {'comments': []},
            },
        }
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_issue('PROJ-1')
        assert result['status_category'] == ''

    def test_missing_comment_field(self):
        data = {
            'key': 'PROJ-1',
            'fields': {
                'summary': 'S',
                'description': 'D',
                'status': {'name': 'Open', 'statusCategory': {'key': 'new'}},
            },
        }
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_issue('PROJ-1')
        assert result['comments'] == []

    def test_key_fallback_from_param(self):
        data = {'fields': {'summary': 'S', 'description': 'D',
                           'status': {}, 'comment': {}}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_issue('PROJ-77')
        assert result['key'] == 'PROJ-77'

    def test_error_returns_none(self):
        self.client._session.get = MagicMock(side_effect=requests.RequestException('err'))
        assert self.client.get_issue('PROJ-1') is None


# ═══════════════════════════════════════════════════════════════════════
# get_issue_status
# ═══════════════════════════════════════════════════════════════════════

class TestGetIssueStatus:
    def setup_method(self):
        self.client = _make_client()

    def test_returns_category_key(self):
        data = {'fields': {'status': {'statusCategory': {'key': 'done'}}}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_issue_status('PROJ-1') == 'done'

    def test_indeterminate(self):
        data = {'fields': {'status': {'statusCategory': {'key': 'indeterminate'}}}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_issue_status('PROJ-1') == 'indeterminate'

    def test_new(self):
        data = {'fields': {'status': {'statusCategory': {'key': 'new'}}}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_issue_status('PROJ-1') == 'new'

    def test_not_found_returns_none(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_issue_status('PROJ-999') is None

    def test_missing_status_field_returns_none(self):
        data = {'fields': {}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_issue_status('PROJ-1') is None

    def test_missing_status_category_returns_none(self):
        data = {'fields': {'status': {}}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_issue_status('PROJ-1') is None

    def test_none_status_returns_none(self):
        data = {'fields': {'status': None}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_issue_status('PROJ-1') is None

    def test_missing_fields_key_returns_none(self):
        data = {}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_issue_status('PROJ-1') is None


# ═══════════════════════════════════════════════════════════════════════
# search_blockers
# ═══════════════════════════════════════════════════════════════════════

class TestSearchBlockers:
    def setup_method(self):
        self.client = _make_client()

    def test_returns_issues(self):
        data = {'issues': [{
            'key': 'PROJ-10',
            'fields': {
                'summary': 'Critical bug',
                'status': {'name': 'Open', 'statusCategory': {'key': 'new'}},
                'assignee': {'displayName': 'Dev Person'},
                'created': '2026-01-01T00:00:00Z',
                'updated': '2026-01-02T00:00:00Z',
                'priority': {'name': 'Blocker'},
                'resolution': None,
                'labels': ['ci', 'regression'],
            },
        }]}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.search_blockers('PROJ')
        assert len(result) == 1
        assert result[0]['key'] == 'PROJ-10'
        assert result[0]['summary'] == 'Critical bug'
        assert result[0]['status'] == 'Open'
        assert result[0]['status_category'] == 'new'
        assert result[0]['assignee'] == 'Dev Person'
        assert result[0]['resolution'] is None
        assert result[0]['labels'] == ['ci', 'regression']

    def test_no_assignee(self):
        data = {'issues': [{
            'key': 'PROJ-11',
            'fields': {
                'summary': 'Unassigned',
                'status': {'name': 'Open', 'statusCategory': {'key': 'new'}},
                'assignee': None,
                'created': '', 'updated': '',
                'priority': {'name': 'Blocker'},
                'resolution': None,
                'labels': [],
            },
        }]}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.search_blockers('PROJ')
        assert result[0]['assignee'] is None

    def test_with_resolution(self):
        data = {'issues': [{
            'key': 'PROJ-12',
            'fields': {
                'summary': 'Resolved',
                'status': {'name': 'Done', 'statusCategory': {'key': 'done'}},
                'assignee': {'displayName': 'Dev'},
                'created': '', 'updated': '',
                'priority': {'name': 'Blocker'},
                'resolution': {'name': 'Fixed'},
                'labels': [],
            },
        }]}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.search_blockers('PROJ')
        assert result[0]['resolution'] == 'Fixed'

    def test_with_fix_versions_filter(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, {'issues': []}))
        self.client.search_blockers('PROJ', fix_versions=['v3.5', 'v3.6'])
        url = self.client._session.get.call_args[0][0]
        assert 'fixVersion' in url
        assert 'v3.5' in url
        assert 'v3.6' in url

    def test_without_fix_versions_no_filter(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, {'issues': []}))
        self.client.search_blockers('PROJ')
        url = self.client._session.get.call_args[0][0]
        assert 'fixVersion' not in url

    def test_jql_contains_project_and_priority(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, {'issues': []}))
        self.client.search_blockers('MYPROJ')
        url = self.client._session.get.call_args[0][0]
        assert 'MYPROJ' in url
        assert 'Blocker' in url

    def test_empty_issues(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, {'issues': []}))
        assert self.client.search_blockers('PROJ') == []

    def test_error_returns_empty_list(self):
        self.client._session.get = MagicMock(return_value=_mock_response(500))
        assert self.client.search_blockers('PROJ') == []

    def test_request_exception_returns_empty_list(self):
        self.client._session.get = MagicMock(side_effect=requests.RequestException('fail'))
        assert self.client.search_blockers('PROJ') == []

    def test_multiple_issues(self):
        issues = [
            {
                'key': 'PROJ-{}'.format(i),
                'fields': {
                    'summary': 'Issue {}'.format(i),
                    'status': {'name': 'Open', 'statusCategory': {'key': 'new'}},
                    'assignee': None,
                    'created': '', 'updated': '',
                    'priority': {'name': 'Blocker'},
                    'resolution': None,
                    'labels': [],
                },
            }
            for i in range(5)
        ]
        self.client._session.get = MagicMock(return_value=_mock_response(200, {'issues': issues}))
        result = self.client.search_blockers('PROJ')
        assert len(result) == 5

    def test_missing_fields_use_defaults(self):
        data = {'issues': [{'key': 'PROJ-1', 'fields': {}}]}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.search_blockers('PROJ')
        assert result[0]['summary'] == ''
        assert result[0]['status'] == ''
        assert result[0]['assignee'] is None
        assert result[0]['labels'] == []


# ═══════════════════════════════════════════════════════════════════════
# get_issue_changelog
# ═══════════════════════════════════════════════════════════════════════

class TestGetIssueChangelog:
    def setup_method(self):
        self.client = _make_client()

    def test_returns_entries(self):
        data = {
            'fields': {'summary': 'issue'},
            'changelog': {'histories': [{
                'created': '2026-01-01T00:00:00Z',
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
        assert result[0]['from'] == 'Open'
        assert result[0]['to'] == 'In Progress'
        assert result[0]['author'] == 'Dev'
        assert result[0]['created'] == '2026-01-01T00:00:00Z'

    def test_multiple_items_in_one_history(self):
        data = {
            'fields': {'summary': 'issue'},
            'changelog': {'histories': [{
                'created': '2026-01-01',
                'author': {'displayName': 'Dev'},
                'items': [
                    {'field': 'status', 'fromString': 'Open', 'toString': 'In Progress'},
                    {'field': 'assignee', 'fromString': None, 'toString': 'Dev'},
                ],
            }]},
        }
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_issue_changelog('PROJ-1')
        assert len(result) == 2
        assert result[0]['field'] == 'status'
        assert result[1]['field'] == 'assignee'

    def test_multiple_histories(self):
        histories = [
            {
                'created': '2026-01-0{}'.format(i),
                'author': {'displayName': 'Dev'},
                'items': [{'field': 'status', 'fromString': 'A', 'toString': 'B'}],
            }
            for i in range(1, 4)
        ]
        data = {'fields': {'summary': 'issue'}, 'changelog': {'histories': histories}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_issue_changelog('PROJ-1')
        assert len(result) == 3

    def test_not_found_returns_empty_list(self):
        self.client._session.get = MagicMock(return_value=_mock_response(404))
        assert self.client.get_issue_changelog('PROJ-999') == []

    def test_error_returns_empty_list(self):
        self.client._session.get = MagicMock(side_effect=requests.RequestException('fail'))
        assert self.client.get_issue_changelog('PROJ-1') == []

    def test_empty_changelog(self):
        data = {'fields': {'summary': 'issue'}, 'changelog': {'histories': []}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_issue_changelog('PROJ-1') == []

    def test_missing_changelog_key(self):
        data = {'fields': {'summary': 'issue'}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        assert self.client.get_issue_changelog('PROJ-1') == []

    def test_max_results_slicing(self):
        # Create 60 history entries, default max_results=50
        histories = [
            {
                'created': '2026-01-01',
                'author': {'displayName': 'Dev'},
                'items': [{'field': 'status', 'fromString': 'A', 'toString': 'B'}],
            }
            for _ in range(60)
        ]
        data = {'fields': {'summary': 'issue'}, 'changelog': {'histories': histories}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_issue_changelog('PROJ-1')
        # Takes the last 50 histories, each with 1 item = 50 entries
        assert len(result) == 50

    def test_custom_max_results(self):
        histories = [
            {
                'created': '2026-01-01',
                'author': {'displayName': 'Dev'},
                'items': [{'field': 'status', 'fromString': 'A', 'toString': 'B'}],
            }
            for _ in range(10)
        ]
        data = {'fields': {'summary': 'issue'}, 'changelog': {'histories': histories}}
        self.client._session.get = MagicMock(return_value=_mock_response(200, data))
        result = self.client.get_issue_changelog('PROJ-1', max_results=3)
        assert len(result) == 3


# ═══════════════════════════════════════════════════════════════════════
# Edge cases — None parameters, empty responses
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def setup_method(self):
        self.client = _make_client()

    def test_get_issue_empty_response_body(self):
        # API returns 200 with empty dict — _get returns {}, which is falsy
        self.client._session.get = MagicMock(return_value=_mock_response(200, {}))
        # get_issue checks `if not data` — empty dict is falsy
        result = self.client.get_issue('PROJ-1')
        assert result is None

    def test_search_blockers_empty_response_body(self):
        self.client._session.get = MagicMock(return_value=_mock_response(200, {}))
        # Empty dict is falsy, search_blockers checks `if not data`
        result = self.client.search_blockers('PROJ')
        assert result == []

    def test_get_transitions_none_from_get(self):
        # _get returning None
        self.client._session.get = MagicMock(return_value=_mock_response(500))
        result = self.client.get_transitions('PROJ-1')
        assert result == []

    def test_add_comment_empty_body(self):
        self.client._session.post = MagicMock(return_value=_mock_response(201, {'id': '1', 'body': ''}))
        result = self.client.add_comment('PROJ-1', '')
        assert result is not None
        assert result['body'] == ''

    def test_create_issue_empty_summary(self):
        # Server should reject but client sends it
        self.client._session.post = MagicMock(
            return_value=_mock_response(400, {'errors': {'summary': 'required'}}))
        assert self.client.create_issue('', '') is None

    def test_transition_issue_string_id(self):
        self.client._session.post = MagicMock(return_value=_mock_response(204))
        self.client.transition_issue('PROJ-1', '31')
        call_kwargs = self.client._session.post.call_args[1]
        assert call_kwargs['json']['transition']['id'] == '31'

    def test_transition_issue_int_id_converted(self):
        self.client._session.post = MagicMock(return_value=_mock_response(204))
        self.client.transition_issue('PROJ-1', 31)
        call_kwargs = self.client._session.post.call_args[1]
        assert call_kwargs['json']['transition']['id'] == '31'
