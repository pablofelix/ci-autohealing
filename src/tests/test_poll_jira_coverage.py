"""Comprehensive tests for poll_jira_comments module.

Covers JiraCommentPoller constructor, all public and private methods,
error handling, edge cases, and the main() entry point.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch

# Mock module-level imports before importing the module under test
sys.modules['logger'] = MagicMock(setup_logger=MagicMock(return_value=MagicMock()))
sys.modules['prompt_loader'] = MagicMock(load_prompt=MagicMock(return_value='test system prompt'))
sys.modules['clients.jira_client'] = MagicMock()
sys.modules['clients.llm_provider'] = MagicMock()
sys.modules['config'] = MagicMock()
sys.modules['repositories'] = MagicMock()
sys.modules['repositories.connection'] = MagicMock()

import pytest  # noqa: E402

from poll_jira_comments import MAX_LOG_CHARS, JiraCommentPoller, main  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_config():
    """Config with jira and llm sub-configs populated."""
    config = MagicMock()
    config.jira.base_url = 'https://issues.example.com'
    config.jira.email = 'bot@example.com'
    config.jira.token = 'tok-123'
    config.jira.project = 'TEST'
    config.llm = MagicMock()
    return config


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_draft_repo():
    return MagicMock()


@pytest.fixture
def mock_jira():
    return MagicMock()


@pytest.fixture
def mock_llm():
    return MagicMock()


@pytest.fixture
def poller(mock_config, mock_db, mock_draft_repo, mock_jira, mock_llm):
    """Fully-injected poller with no real external deps."""
    return JiraCommentPoller(
        config=mock_config,
        db=mock_db,
        draft_repo=mock_draft_repo,
        jira=mock_jira,
        llm=mock_llm,
    )


# ---------------------------------------------------------------------------
# __init__ tests
# ---------------------------------------------------------------------------

class TestInit:
    def test_with_all_deps_provided(self, mock_config, mock_db, mock_draft_repo, mock_jira, mock_llm):
        p = JiraCommentPoller(
            config=mock_config, db=mock_db,
            draft_repo=mock_draft_repo, jira=mock_jira, llm=mock_llm,
        )
        assert p._db is mock_db
        assert p._draft_repo is mock_draft_repo
        assert p._jira is mock_jira
        assert p._llm is mock_llm

    def test_raises_when_no_jira_config(self, mock_config, mock_db, mock_draft_repo, mock_llm):
        mock_config.jira = None
        with pytest.raises(ValueError, match="Jira not configured"):
            JiraCommentPoller(
                config=mock_config, db=mock_db,
                draft_repo=mock_draft_repo, jira=None, llm=mock_llm,
            )

    def test_raises_when_no_llm_config(self, mock_config, mock_db, mock_draft_repo, mock_jira):
        mock_config.llm = None
        with pytest.raises(ValueError, match="LLM not configured"):
            JiraCommentPoller(
                config=mock_config, db=mock_db,
                draft_repo=mock_draft_repo, jira=mock_jira, llm=None,
            )


# ---------------------------------------------------------------------------
# get_active_jira_keys tests
# ---------------------------------------------------------------------------

class TestGetActiveJiraKeys:
    def test_returns_keys_from_db(self, poller, mock_db):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [('TEST-1',), ('TEST-2',)]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.connection.return_value.__exit__ = MagicMock(return_value=False)

        keys = poller.get_active_jira_keys()
        assert keys == ['TEST-1', 'TEST-2']
        mock_cursor.execute.assert_called_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert 'build_failures' in sql
        assert 'conforma_results' in sql
        assert 'UNION' in sql


# ---------------------------------------------------------------------------
# get_failure_context tests
# ---------------------------------------------------------------------------

class TestGetFailureContext:
    _SENTINEL = object()

    def _setup_cursor(self, mock_db, first_result, second_result=_SENTINEL):
        """Helper to set up cursor with fetchone results for two queries."""
        mock_cursor = MagicMock()
        if second_result is not self._SENTINEL:
            mock_cursor.fetchone.side_effect = [first_result, second_result]
        else:
            mock_cursor.fetchone.side_effect = [first_result]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.connection.return_value.__exit__ = MagicMock(return_value=False)
        return mock_cursor

    def test_build_failure_found(self, poller, mock_db):
        row = (
            'my-component', 'CompileError', 'cannot find module',
            'build-task', 'compile-step', 'some logs',
            'abc123', 'dev@co.com',
            'Missing import', 'Add import X', 'dependency',
        )
        self._setup_cursor(mock_db, row)

        ctx = poller.get_failure_context('TEST-1')
        assert ctx['failure_type'] == 'build'
        assert ctx['component_name'] == 'my-component'
        assert ctx['error_type'] == 'CompileError'
        assert ctx['error_message'] == 'cannot find module'
        assert ctx['failed_task'] == 'build-task'
        assert ctx['failed_step'] == 'compile-step'
        assert ctx['build_logs'] == 'some logs'
        assert ctx['commit_sha'] == 'abc123'
        assert ctx['commit_author'] == 'dev@co.com'
        assert ctx['ai_root_cause'] == 'Missing import'
        assert ctx['ai_recommended_fix'] == 'Add import X'
        assert ctx['ai_category'] == 'dependency'

    def test_conforma_found_when_no_build(self, poller, mock_db):
        conforma_row = (
            'my-component', 'release-gate',
            3, 1, 'RPM signature missing', 'def456',
            'Unsigned RPM', 'Sign the RPM', 'signing',
        )
        self._setup_cursor(mock_db, None, conforma_row)

        ctx = poller.get_failure_context('TEST-2')
        assert ctx['failure_type'] == 'conforma'
        assert ctx['component_name'] == 'my-component'
        assert ctx['scenario'] == 'release-gate'
        assert ctx['violations_count'] == 3
        assert ctx['warnings_count'] == 1
        assert ctx['violation_summary'] == 'RPM signature missing'
        assert ctx['commit_sha'] == 'def456'
        assert ctx['ai_root_cause'] == 'Unsigned RPM'
        assert ctx['ai_recommended_fix'] == 'Sign the RPM'
        assert ctx['ai_category'] == 'signing'

    def test_nothing_found(self, poller, mock_db):
        self._setup_cursor(mock_db, None, None)
        ctx = poller.get_failure_context('TEST-3')
        assert ctx is None

    def test_logs_truncation(self, poller, mock_db):
        long_logs = 'x' * (MAX_LOG_CHARS + 5000)
        row = (
            'comp', 'Error', 'msg', 'task', 'step',
            long_logs, 'sha', 'author',
            None, None, None,
        )
        self._setup_cursor(mock_db, row)

        ctx = poller.get_failure_context('TEST-4')
        assert len(ctx['build_logs']) == MAX_LOG_CHARS
        # Should keep the tail (last MAX_LOG_CHARS chars)
        assert ctx['build_logs'] == long_logs[-MAX_LOG_CHARS:]


# ---------------------------------------------------------------------------
# build_user_prompt tests
# ---------------------------------------------------------------------------

class TestBuildUserPrompt:
    def _make_comment(self, author_name='Jane Doe', body='Why is this failing?'):
        return {
            'id': '100',
            'author': {'displayName': author_name, 'emailAddress': 'jane@co.com'},
            'body': body,
        }

    def test_build_failure_context(self, poller):
        ctx = {
            'failure_type': 'build',
            'component_name': 'my-comp',
            'error_type': 'CompileError',
            'error_message': 'missing module',
            'failed_task': 'build-task',
            'failed_step': 'compile',
            'build_logs': 'ERROR: not found',
            'commit_sha': 'abc123def456',
            'commit_author': 'dev@co.com',
            'ai_root_cause': None,
            'ai_recommended_fix': None,
            'ai_category': None,
        }
        prompt = poller.build_user_prompt(self._make_comment(), ctx, 'TEST-1')
        assert 'TEST-1' in prompt
        assert 'Jane Doe' in prompt
        assert 'Build Failure' in prompt
        assert 'my-comp' in prompt
        assert 'CompileError' in prompt
        assert 'ERROR: not found' in prompt

    def test_conforma_context(self, poller):
        ctx = {
            'failure_type': 'conforma',
            'component_name': 'my-comp',
            'scenario': 'release-gate',
            'violations_count': 5,
            'warnings_count': 2,
            'violation_summary': 'Unsigned RPMs',
            'commit_sha': 'abc123',
            'ai_root_cause': None,
            'ai_recommended_fix': None,
            'ai_category': None,
        }
        prompt = poller.build_user_prompt(self._make_comment(), ctx, 'TEST-2')
        assert 'Conforma Violation' in prompt
        assert 'release-gate' in prompt
        assert 'Violations: 5' in prompt
        assert 'Warnings: 2' in prompt
        assert 'Unsigned RPMs' in prompt

    def test_no_failure_context(self, poller):
        prompt = poller.build_user_prompt(self._make_comment(), None, 'TEST-3')
        assert 'TEST-3' in prompt
        assert 'Jane Doe' in prompt
        assert 'Draft a concise' in prompt
        # Should not have failure context sections
        assert 'Failure Context' not in prompt

    def test_with_ai_analysis_sections(self, poller):
        ctx = {
            'failure_type': 'build',
            'component_name': 'comp',
            'error_type': 'Err',
            'error_message': 'msg',
            'failed_task': 'task',
            'failed_step': 'step',
            'build_logs': '',
            'commit_sha': 'sha',
            'commit_author': 'author',
            'ai_root_cause': 'The dependency was removed upstream',
            'ai_recommended_fix': 'Pin to version 2.3',
            'ai_category': 'dependency',
        }
        prompt = poller.build_user_prompt(self._make_comment(), ctx, 'TEST-4')
        assert 'AI Root Cause Analysis' in prompt
        assert 'The dependency was removed upstream' in prompt
        assert 'AI Recommended Fix' in prompt
        assert 'Pin to version 2.3' in prompt


# ---------------------------------------------------------------------------
# _notify tests
# ---------------------------------------------------------------------------

class TestNotify:
    @patch('poll_jira_comments.subprocess.run')
    def test_successful_notification(self, mock_run, poller):
        poller._notify('TEST-1', 'Jane', 'https://jira/TEST-1')
        mock_run.assert_called_once()
        args = mock_run.call_args
        cmd = args[0][0]
        assert 'notify-send' in cmd
        assert any('TEST-1' in str(a) for a in cmd)
        assert args[1]['timeout'] == 5

    @patch('poll_jira_comments.subprocess.run', side_effect=OSError("no notify-send"))
    def test_oserror_handling(self, mock_run, poller):
        # Should not raise
        poller._notify('TEST-1', 'Jane', 'https://jira/TEST-1')

    @patch('poll_jira_comments.subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='notify-send', timeout=5))
    def test_timeout_handling(self, mock_run, poller):
        # Should not raise
        poller._notify('TEST-1', 'Jane', 'https://jira/TEST-1')


# ---------------------------------------------------------------------------
# run tests
# ---------------------------------------------------------------------------

class TestRun:
    def _setup_poller(self, poller, active_keys=None, comments_by_key=None,
                      existing_ids_by_key=None, failure_ctx=None):
        """Wire up mocks for a run() scenario."""
        if active_keys is None:
            active_keys = []

        poller.get_active_jira_keys = MagicMock(return_value=active_keys)
        poller.get_failure_context = MagicMock(return_value=failure_ctx)
        poller._notify = MagicMock()

        if comments_by_key:
            poller._jira.get_comments.side_effect = lambda k: comments_by_key.get(k, [])
        else:
            poller._jira.get_comments.return_value = []

        if existing_ids_by_key:
            poller._draft_repo.get_existing_comment_ids.side_effect = lambda k: existing_ids_by_key.get(k, set())
        else:
            poller._draft_repo.get_existing_comment_ids.return_value = set()

    def test_no_active_keys(self, poller):
        self._setup_poller(poller, active_keys=[])
        stats = poller.run()
        assert stats['tickets'] == 0
        assert stats['new_comments'] == 0
        assert stats['drafted'] == 0
        assert stats['errors'] == 0

    def test_no_new_comments(self, poller):
        self._setup_poller(
            poller,
            active_keys=['TEST-1'],
            comments_by_key={'TEST-1': [
                {'id': '10', 'author': {'displayName': 'Bot', 'emailAddress': 'bot@example.com'}},
            ]},
            existing_ids_by_key={'TEST-1': {10}},
        )
        stats = poller.run()
        assert stats['tickets'] == 1
        assert stats['new_comments'] == 0
        assert stats['drafted'] == 0

    def test_new_comments_drafted_successfully(self, poller, mock_config):
        mock_config.jira.email = 'bot@example.com'

        llm_resp = MagicMock()
        llm_resp.content = '  Draft reply text  '
        llm_resp.model = 'claude-sonnet-4-5-20250929'
        llm_resp.input_tokens = 100
        llm_resp.output_tokens = 50
        poller._llm.create_message.return_value = llm_resp
        poller._draft_repo.insert_draft.return_value = True

        self._setup_poller(
            poller,
            active_keys=['TEST-1'],
            comments_by_key={'TEST-1': [
                {'id': '20', 'author': {'displayName': 'Jane', 'emailAddress': 'jane@co.com'}, 'body': 'Hello'},
            ]},
            existing_ids_by_key={'TEST-1': set()},
            failure_ctx={'failure_type': 'build', 'component_name': 'comp',
                         'error_type': 'Err', 'error_message': 'msg',
                         'failed_task': 'task', 'failed_step': 'step',
                         'build_logs': '', 'commit_sha': 'sha', 'commit_author': 'auth',
                         'ai_root_cause': None, 'ai_recommended_fix': None, 'ai_category': None},
        )

        stats = poller.run()
        assert stats['tickets'] == 1
        assert stats['new_comments'] == 1
        assert stats['drafted'] == 1
        assert stats['errors'] == 0

        poller._llm.create_message.assert_called_once()
        create_call = poller._llm.create_message.call_args
        assert create_call[1]['max_tokens'] == 512

        poller._draft_repo.insert_draft.assert_called_once_with(
            jira_key='TEST-1',
            comment_id=20,
            draft_response='Draft reply text',
            model_used='claude-sonnet-4-5-20250929',
            tokens_used=150,
        )
        poller._notify.assert_called_once()

    def test_draft_insertion_fails_race(self, poller, mock_config):
        mock_config.jira.email = 'bot@example.com'

        llm_resp = MagicMock()
        llm_resp.content = 'Draft text'
        llm_resp.model = 'model-1'
        llm_resp.input_tokens = 50
        llm_resp.output_tokens = 25
        poller._llm.create_message.return_value = llm_resp
        poller._draft_repo.insert_draft.return_value = False  # Race: already inserted

        self._setup_poller(
            poller,
            active_keys=['TEST-1'],
            comments_by_key={'TEST-1': [
                {'id': '30', 'author': {'displayName': 'Jane', 'emailAddress': 'jane@co.com'}, 'body': 'hi'},
            ]},
            existing_ids_by_key={'TEST-1': set()},
        )

        stats = poller.run()
        assert stats['drafted'] == 0
        assert stats['errors'] == 0
        poller._notify.assert_not_called()

    def test_llm_error_for_single_comment(self, poller, mock_config):
        mock_config.jira.email = 'bot@example.com'

        poller._llm.create_message.side_effect = RuntimeError("LLM timeout")

        self._setup_poller(
            poller,
            active_keys=['TEST-1'],
            comments_by_key={'TEST-1': [
                {'id': '40', 'author': {'displayName': 'Jane', 'emailAddress': 'jane@co.com'}, 'body': 'question'},
            ]},
            existing_ids_by_key={'TEST-1': set()},
        )

        stats = poller.run()
        assert stats['new_comments'] == 1
        assert stats['drafted'] == 0
        assert stats['errors'] == 1

    def test_polling_error_for_key(self, poller):
        poller.get_active_jira_keys = MagicMock(return_value=['TEST-1'])
        poller._draft_repo.get_existing_comment_ids.side_effect = RuntimeError("DB down")

        stats = poller.run()
        assert stats['tickets'] == 1
        assert stats['errors'] == 1
        assert stats['drafted'] == 0


# ---------------------------------------------------------------------------
# main() tests
# ---------------------------------------------------------------------------

class TestMain:
    @patch('poll_jira_comments.JiraCommentPoller')
    @patch('poll_jira_comments.CollectorConfig')
    def test_main_success(self, MockConfig, MockPoller):
        config = MagicMock()
        config.jira = MagicMock()
        config.llm = MagicMock()
        MockConfig.from_env.return_value = config

        mock_poller = MagicMock()
        mock_poller.run.return_value = {'tickets': 1, 'new_comments': 1, 'drafted': 1, 'errors': 0}
        MockPoller.return_value = mock_poller

        # Should not raise or sys.exit
        main()
        MockPoller.assert_called_once_with(config)
        mock_poller.run.assert_called_once()

    @patch('poll_jira_comments.CollectorConfig')
    def test_main_exits_when_no_jira(self, MockConfig):
        config = MagicMock()
        config.jira = None
        config.llm = MagicMock()
        MockConfig.from_env.return_value = config

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    @patch('poll_jira_comments.CollectorConfig')
    def test_main_exits_when_no_llm(self, MockConfig):
        config = MagicMock()
        config.jira = MagicMock()
        config.llm = None
        MockConfig.from_env.return_value = config

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
