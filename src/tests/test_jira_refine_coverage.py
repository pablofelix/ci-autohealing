"""Comprehensive tests for jira_refine — wrap, display, refine, analyze, apply, main."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Module-level prompts are loaded at import time — mock before importing.
with patch('prompt_loader.load_prompt', return_value='mock-prompt'):
    from jira_refine import (
        WIDTH,
        analyze_session,
        apply_prompt_change,
        display_draft,
        get_comment_text,
        main,
        refine_draft,
        wrap,
    )


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _mock_llm(content='LLM response'):
    """Return a mock LLM whose create_message returns controllable content."""
    llm = MagicMock()
    resp = MagicMock()
    resp.content = content
    llm.create_message.return_value = resp
    return llm


# ═══════════════════════════════════════════════════════════════════════
# wrap()
# ═══════════════════════════════════════════════════════════════════════

class TestWrap:
    def test_normal_text(self):
        result = wrap('Hello world')
        assert result == '    Hello world'

    def test_blank_lines_preserved(self):
        text = 'First paragraph.\n\nSecond paragraph.'
        result = wrap(text)
        lines = result.split('\n')
        assert len(lines) == 3
        assert lines[0].strip() == 'First paragraph.'
        assert lines[1].strip() == ''
        assert lines[2].strip() == 'Second paragraph.'

    def test_long_line_wraps(self):
        long_text = 'word ' * 30  # ~150 chars
        result = wrap(long_text)
        for line in result.split('\n'):
            assert len(line) <= WIDTH

    def test_empty_string(self):
        result = wrap('')
        # Empty string -> splitlines returns [] -> no iterations -> empty result
        assert result == ''

    def test_custom_indent(self):
        result = wrap('Hello', indent='>> ')
        assert result.startswith('>> ')
        assert 'Hello' in result


# ═══════════════════════════════════════════════════════════════════════
# display_draft()
# ═══════════════════════════════════════════════════════════════════════

class TestDisplayDraft:
    def test_default_label(self, capsys):
        display_draft('Some draft text')
        captured = capsys.readouterr().out
        assert 'Current draft:' in captured
        assert 'Some draft text' in captured

    def test_custom_label(self, capsys):
        display_draft('Some text', label='Revised draft:')
        captured = capsys.readouterr().out
        assert 'Revised draft:' in captured
        assert 'Current draft:' not in captured


# ═══════════════════════════════════════════════════════════════════════
# refine_draft()
# ═══════════════════════════════════════════════════════════════════════

class TestRefineDraft:
    def test_no_history(self):
        llm = _mock_llm('  Improved draft  ')
        result = refine_draft(llm, 'original comment', 'current draft', 'make it shorter', [])
        assert result == 'Improved draft'
        args = llm.create_message.call_args
        assert 'Previous revisions' not in args.kwargs.get('user_content', args[1] if len(args[1]) > 1 else '')

    def test_with_history(self):
        llm = _mock_llm('Better draft')
        history = [
            {'feedback': 'too long', 'draft': 'draft v1'},
            {'feedback': 'more formal', 'draft': 'draft v2'},
        ]
        result = refine_draft(llm, 'comment', 'current', 'fix typos', history)
        assert result == 'Better draft'
        call_kwargs = llm.create_message.call_args
        user_content = call_kwargs.kwargs.get('user_content') or call_kwargs[1][0] if call_kwargs[1] else ''
        # Ensure the user_prompt passed to LLM includes history section
        # Access via the positional/keyword args of create_message
        called_args, called_kwargs = llm.create_message.call_args
        prompt = called_kwargs.get('user_content', '')
        assert 'Previous revisions' in prompt
        assert 'too long' in prompt

    def test_returns_stripped_content(self):
        llm = _mock_llm('\n\n  result with whitespace \n\n')
        result = refine_draft(llm, 'comment', 'draft', 'feedback', [])
        assert result == 'result with whitespace'


# ═══════════════════════════════════════════════════════════════════════
# analyze_session()
# ═══════════════════════════════════════════════════════════════════════

class TestAnalyzeSession:
    def test_empty_history_returns_none(self):
        llm = _mock_llm()
        result = analyze_session(llm, 'original', [], 'final')
        assert result is None
        llm.create_message.assert_not_called()

    @patch.object(Path, 'exists', return_value=True)
    @patch.object(Path, 'read_text', return_value='drafter prompt content')
    def test_valid_json_response(self, mock_read, mock_exists):
        payload = {'has_systematic_pattern': True, 'pattern_summary': 'too verbose'}
        llm = _mock_llm(json.dumps(payload))
        history = [{'feedback': 'shorter', 'draft': 'draft v1'}]
        result = analyze_session(llm, 'original', history, 'final')
        assert result == payload
        assert result['has_systematic_pattern'] is True

    @patch.object(Path, 'exists', return_value=True)
    @patch.object(Path, 'read_text', return_value='drafter prompt')
    def test_invalid_json_returns_none(self, mock_read, mock_exists):
        llm = _mock_llm('this is not json at all')
        history = [{'feedback': 'fix it', 'draft': 'draft'}]
        result = analyze_session(llm, 'original', history, 'final')
        assert result is None

    @patch.object(Path, 'exists', return_value=False)
    def test_drafter_file_missing(self, mock_exists):
        payload = {'has_systematic_pattern': False}
        llm = _mock_llm(json.dumps(payload))
        history = [{'feedback': 'tweak', 'draft': 'draft'}]
        result = analyze_session(llm, 'original', history, 'final')
        assert result == payload
        # read_text should not be called when file does not exist
        # (the code uses DRAFTER_FILE.exists() guard)


# ═══════════════════════════════════════════════════════════════════════
# apply_prompt_change()
# ═══════════════════════════════════════════════════════════════════════

class TestApplyPromptChange:
    @patch.object(Path, 'exists', return_value=True)
    @patch.object(Path, 'read_text', return_value='# Drafter prompt\n- existing rule\n')
    @patch.object(Path, 'write_text')
    def test_appends_correctly(self, mock_write, mock_read, mock_exists):
        result = apply_prompt_change('Always use formal tone')
        assert result is True
        written = mock_write.call_args[0][0]
        assert '- Always use formal tone\n' in written
        assert '- existing rule' in written

    @patch.object(Path, 'exists', return_value=True)
    @patch.object(Path, 'read_text', return_value='# Prompt\n')
    @patch.object(Path, 'write_text')
    def test_strips_leading_dash_from_proposed(self, mock_write, mock_read, mock_exists):
        result = apply_prompt_change('- Already has dash')
        assert result is True
        written = mock_write.call_args[0][0]
        # Should not double the dash prefix
        assert '- Already has dash\n' in written
        assert '- - Already' not in written

    @patch.object(Path, 'exists', return_value=False)
    def test_file_missing_returns_false(self, mock_exists, capsys):
        result = apply_prompt_change('some text')
        assert result is False
        captured = capsys.readouterr().out
        assert 'not found' in captured


# ═══════════════════════════════════════════════════════════════════════
# get_comment_text()
# ═══════════════════════════════════════════════════════════════════════

class TestGetCommentText:
    def test_successful_fetch(self):
        jira = MagicMock()
        jira.get_comment.return_value = {
            'author': {'displayName': 'Jane Doe'},
            'body': '  Please fix this ASAP.  ',
        }
        author, body = get_comment_text(jira, 'PROJ-123', '10001')
        assert author == 'Jane Doe'
        assert body == 'Please fix this ASAP.'

    def test_empty_data_returns_defaults(self):
        jira = MagicMock()
        jira.get_comment.return_value = None
        author, body = get_comment_text(jira, 'PROJ-1', '999')
        assert author == 'Unknown'
        assert body == '[comment not available]'

    def test_missing_author_key(self):
        jira = MagicMock()
        jira.get_comment.return_value = {'body': 'content here'}
        author, body = get_comment_text(jira, 'PROJ-1', '100')
        assert author == 'Unknown'
        assert body == 'content here'


# ═══════════════════════════════════════════════════════════════════════
# main()
# ═══════════════════════════════════════════════════════════════════════

# Common patch target prefix
_MOD = 'jira_refine'


class TestMainArgValidation:
    def test_wrong_arg_count_exits_1(self):
        with patch.object(sys, 'argv', ['jira_refine.py']):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_too_many_args_exits_1(self):
        with patch.object(sys, 'argv', ['jira_refine.py', 'KEY-1', '100', 'extra']):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1


class TestMainConfigErrors:
    @patch(f'{_MOD}.CollectorConfig')
    def test_missing_jira_config_exits(self, mock_config_cls):
        mock_cfg = MagicMock()
        mock_cfg.jira = None
        mock_cfg.llm = MagicMock()
        mock_config_cls.from_env.return_value = mock_cfg

        with patch.object(sys, 'argv', ['jira_refine.py', 'PROJ-1', '100']):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    @patch(f'{_MOD}.CollectorConfig')
    def test_missing_llm_config_exits(self, mock_config_cls):
        mock_cfg = MagicMock()
        mock_cfg.jira = MagicMock()
        mock_cfg.llm = None
        mock_config_cls.from_env.return_value = mock_cfg

        with patch.object(sys, 'argv', ['jira_refine.py', 'PROJ-1', '100']):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1


class TestMainInteractiveLoop:
    """Test the interactive refinement loop in main()."""

    def _setup_main_mocks(self):
        """Create the standard set of mocks needed for main() to reach the loop."""
        patches = {}
        mocks = {}

        # Config
        p = patch(f'{_MOD}.CollectorConfig')
        mock_config_cls = p.start()
        patches['config'] = p
        mock_cfg = MagicMock()
        mock_cfg.jira = MagicMock(base_url='https://jira.example.com',
                                   email='u@t.com', token='tok', project='PROJ')
        mock_cfg.llm = MagicMock()
        mock_cfg.db = MagicMock()
        mock_config_cls.from_env.return_value = mock_cfg
        mocks['config'] = mock_cfg

        # Database + draft repo
        p = patch(f'{_MOD}.DatabaseConnection')
        mock_db_cls = p.start()
        patches['db'] = p
        mocks['db'] = mock_db_cls

        p = patch(f'{_MOD}.JiraCommentDraftRepository')
        mock_repo_cls = p.start()
        patches['repo'] = p
        mock_repo = mock_repo_cls.return_value
        mock_repo.get_unreviewed.return_value = [
            {'jira_key': 'PROJ-1', 'comment_id': 100, 'draft_response': 'Initial draft text'},
        ]
        mocks['draft_repo'] = mock_repo

        # Jira client
        p = patch(f'{_MOD}.JiraClient')
        mock_jira_cls = p.start()
        patches['jira'] = p
        mock_jira = mock_jira_cls.return_value
        mock_jira.get_comment.return_value = {
            'author': {'displayName': 'Tester'},
            'body': 'Original comment body',
        }
        mocks['jira'] = mock_jira

        # LLM provider
        p = patch(f'{_MOD}.create_llm_provider')
        mock_llm_fn = p.start()
        patches['llm'] = p
        mock_llm = _mock_llm('Refined draft from LLM')
        mock_llm_fn.return_value = mock_llm
        mocks['llm'] = mock_llm

        return patches, mocks

    def _stop_patches(self, patches):
        for p in patches.values():
            p.stop()

    def test_ok_accepts_unchanged_draft(self):
        """User types 'ok' immediately — draft accepted unchanged, no DB update."""
        patches, mocks = self._setup_main_mocks()
        try:
            with patch.object(sys, 'argv', ['jira_refine.py', 'PROJ-1', '100']):
                with patch('builtins.input', return_value='ok'):
                    with pytest.raises(SystemExit) as exc_info:
                        main()
                    assert exc_info.value.code == 0
            # LLM refine should NOT have been called
            mocks['llm'].create_message.assert_not_called()
        finally:
            self._stop_patches(patches)

    def test_skip_cancels(self):
        """User types 'skip' — exits without persisting."""
        patches, mocks = self._setup_main_mocks()
        try:
            with patch.object(sys, 'argv', ['jira_refine.py', 'PROJ-1', '100']):
                with patch('builtins.input', return_value='skip'):
                    with pytest.raises(SystemExit) as exc_info:
                        main()
                    assert exc_info.value.code == 0
        finally:
            self._stop_patches(patches)

    def test_feedback_then_ok_persists_draft(self):
        """User gives feedback, gets revision, then accepts with 'ok'."""
        patches, mocks = self._setup_main_mocks()

        # Simulate: first input is feedback, second is 'ok'
        input_values = iter(['make it shorter', 'ok'])

        # Mock the DB connection context manager for the UPDATE
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mocks['db'].return_value.connection.return_value = mock_conn

        try:
            with patch.object(sys, 'argv', ['jira_refine.py', 'PROJ-1', '100']):
                with patch('builtins.input', side_effect=input_values):
                    # analyze_session will be called since history is non-empty;
                    # mock it to return no pattern so main exits cleanly.
                    with patch(f'{_MOD}.analyze_session', return_value=None):
                        with pytest.raises(SystemExit) as exc_info:
                            main()
                        assert exc_info.value.code == 0

            # LLM should have been called once for refining
            assert mocks['llm'].create_message.call_count == 1
            # DB UPDATE should have been executed
            mock_cursor.execute.assert_called_once()
            sql_arg = mock_cursor.execute.call_args[0][0]
            assert 'UPDATE' in sql_arg
        finally:
            self._stop_patches(patches)

    def test_no_draft_found_exits(self):
        """When no matching unreviewed draft exists, main() exits with code 1."""
        patches, mocks = self._setup_main_mocks()
        # Return drafts that don't match our key/comment_id
        mocks['draft_repo'].get_unreviewed.return_value = [
            {'jira_key': 'OTHER-99', 'comment_id': 999, 'draft_response': 'wrong draft'},
        ]
        try:
            with patch.object(sys, 'argv', ['jira_refine.py', 'PROJ-1', '100']):
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 1
        finally:
            self._stop_patches(patches)
