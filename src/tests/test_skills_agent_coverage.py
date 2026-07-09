"""Tests for skills agent modules: agent_sandbox, agent_tools, agent_executor.

Covers:
  - SandboxResult dataclass
  - LocalSandbox: execute (denied patterns, bash/python, timeout, truncation),
    read_file, write_file, cleanup, _truncate, _resolve_path
  - ToolRegistry: get_tool_definitions, dispatch, all handlers
  - _sandbox_result_to_dict, _default_input
  - AgentExecutor: execute (dry_run, happy path, SKILL.md missing, errors),
    _agent_loop (tool calls, task_complete, limits, end_turn, no tool_calls)
  - _build_system_prompt, _build_assistant_content, _summarize_input
"""

import io
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, mock_open, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ===========================================================================
# agent_sandbox tests
# ===========================================================================

class TestSandboxResult:

    def test_defaults(self):
        from skills.agent_sandbox import SandboxResult
        r = SandboxResult(exit_code=0, stdout='hello', stderr='')
        assert r.exit_code == 0
        assert r.stdout == 'hello'
        assert r.stderr == ''
        assert r.timed_out is False

    def test_frozen(self):
        from skills.agent_sandbox import SandboxResult
        r = SandboxResult(exit_code=0, stdout='', stderr='')
        with pytest.raises(FrozenInstanceError):
            r.exit_code = 1

    def test_timed_out_true(self):
        from skills.agent_sandbox import SandboxResult
        r = SandboxResult(exit_code=-1, stdout='', stderr='timeout', timed_out=True)
        assert r.timed_out is True


class TestLocalSandboxInit:

    @patch('skills.agent_sandbox.get_safe_env', return_value={'PATH': '/usr/bin'})
    def test_defaults(self, mock_env):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox()
        assert sb._working_dir == os.getcwd()
        assert sb._max_output == 50000
        mock_env.assert_called_once_with(None)

    @patch('skills.agent_sandbox.get_safe_env', return_value={'PATH': '/usr/bin'})
    def test_custom_params(self, mock_env):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir='/tmp/test', declared_env_vars=['MY_VAR'],
                          max_output_bytes=1000)
        assert sb._working_dir == '/tmp/test'
        assert sb._max_output == 1000
        mock_env.assert_called_once_with(['MY_VAR'])


class TestLocalSandboxDeniedPatterns:

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_rm_rf_root(self, _):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir='/tmp')
        result = sb.execute('rm -rf /')
        assert result.exit_code == -1
        assert 'Command denied' in result.stderr

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_curl_denied(self, _):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir='/tmp')
        result = sb.execute('curl http://evil.com')
        assert result.exit_code == -1
        assert 'denied' in result.stderr.lower()

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_wget_denied(self, _):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir='/tmp')
        result = sb.execute('wget http://evil.com')
        assert result.exit_code == -1

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_pipe_bash_denied(self, _):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir='/tmp')
        result = sb.execute('something | bash')
        assert result.exit_code == -1

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_eval_denied(self, _):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir='/tmp')
        result = sb.execute('eval $(some command)')
        assert result.exit_code == -1

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_chmod_777_denied(self, _):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir='/tmp')
        result = sb.execute('chmod 777 /tmp/file')
        assert result.exit_code == -1

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_case_insensitive(self, _):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir='/tmp')
        result = sb.execute('CURL http://evil.com')
        assert result.exit_code == -1


class TestLocalSandboxExecute:

    @patch('skills.agent_sandbox.sanitize_for_llm', side_effect=lambda x: x)
    @patch('skills.agent_sandbox.get_safe_env', return_value={'PATH': '/usr/bin'})
    def test_bash_command(self, mock_env, mock_sanitize):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir='/tmp')
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = 'hello world'
        mock_proc.stderr = ''
        with patch('subprocess.run', return_value=mock_proc) as mock_run:
            result = sb.execute('echo hello', timeout=30, language='bash')
        assert result.exit_code == 0
        assert result.stdout == 'hello world'
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][0] == ['bash', '-c', 'echo hello']
        assert args[1]['timeout'] == 30
        assert args[1]['cwd'] == '/tmp'

    @patch('skills.agent_sandbox.sanitize_for_llm', side_effect=lambda x: x)
    @patch('skills.agent_sandbox.get_safe_env', return_value={'PATH': '/usr/bin'})
    def test_python_command(self, mock_env, mock_sanitize):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir='/tmp')
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '42'
        mock_proc.stderr = ''
        with patch('subprocess.run', return_value=mock_proc) as mock_run:
            result = sb.execute('print(42)', timeout=10, language='python')
        assert result.exit_code == 0
        args = mock_run.call_args
        assert args[0][0] == ['python3', '-c', 'print(42)']

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_timeout_expired(self, _):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir='/tmp')
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('cmd', 30)):
            result = sb.execute('sleep 100', timeout=30)
        assert result.exit_code == -1
        assert result.timed_out is True
        assert '30 seconds' in result.stderr

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_general_exception(self, _):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir='/tmp')
        with patch('subprocess.run', side_effect=OSError('no such file')):
            result = sb.execute('nonexistent', timeout=5)
        assert result.exit_code == -1
        assert 'Sandbox error' in result.stderr

    @patch('skills.agent_sandbox.sanitize_for_llm', side_effect=lambda x: x)
    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_output_truncation(self, _, mock_sanitize):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir='/tmp', max_output_bytes=20)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = 'a' * 100
        mock_proc.stderr = ''
        with patch('subprocess.run', return_value=mock_proc):
            result = sb.execute('echo lots')
        assert 'truncated' in result.stdout


class TestLocalSandboxReadFile:

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_read_existing(self, _, tmp_path):
        from skills.agent_sandbox import LocalSandbox
        f = tmp_path / 'test.txt'
        f.write_text('content here')
        sb = LocalSandbox(working_dir=str(tmp_path))
        result = sb.read_file('test.txt')
        assert result.exit_code == 0
        assert result.stdout == 'content here'

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_read_nonexistent(self, _, tmp_path):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir=str(tmp_path))
        result = sb.read_file('nonexistent.txt')
        assert result.exit_code == 1
        assert 'Failed to read' in result.stderr

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_read_path_escape(self, _, tmp_path):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir=str(tmp_path))
        with pytest.raises(ValueError, match='escapes sandbox'):
            sb.read_file('/etc/passwd')


class TestLocalSandboxWriteFile:

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_write_new_file(self, _, tmp_path):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir=str(tmp_path))
        result = sb.write_file('output.txt', 'hello')
        assert result.exit_code == 0
        assert 'Wrote 5 bytes' in result.stdout
        assert (tmp_path / 'output.txt').read_text() == 'hello'

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_write_nested_dirs(self, _, tmp_path):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir=str(tmp_path))
        result = sb.write_file('sub/dir/file.txt', 'deep')
        assert result.exit_code == 0
        assert (tmp_path / 'sub' / 'dir' / 'file.txt').read_text() == 'deep'

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_write_path_escape(self, _, tmp_path):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir=str(tmp_path))
        with pytest.raises(ValueError, match='escapes sandbox'):
            sb.write_file('/tmp/../etc/evil.txt', 'evil')


class TestLocalSandboxTruncate:

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_short_text(self, _):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(max_output_bytes=1000)
        assert sb._truncate('short') == 'short'

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_long_text(self, _):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(max_output_bytes=10)
        result = sb._truncate('a' * 100)
        assert len(result) < 100
        assert 'truncated' in result

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_empty_text(self, _):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox()
        assert sb._truncate('') == ''
        assert sb._truncate(None) == ''


class TestLocalSandboxResolvePath:

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_relative_path(self, _, tmp_path):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir=str(tmp_path))
        resolved = sb._resolve_path('file.txt')
        assert resolved == str(tmp_path / 'file.txt')

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_absolute_inside(self, _, tmp_path):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir=str(tmp_path))
        resolved = sb._resolve_path(str(tmp_path / 'inner' / 'file.txt'))
        assert 'inner' in resolved

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_absolute_outside(self, _, tmp_path):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox(working_dir=str(tmp_path))
        with pytest.raises(ValueError, match='escapes sandbox'):
            sb._resolve_path('/etc/passwd')


class TestLocalSandboxCleanup:

    @patch('skills.agent_sandbox.get_safe_env', return_value={})
    def test_cleanup_noop(self, _):
        from skills.agent_sandbox import LocalSandbox
        sb = LocalSandbox()
        sb.cleanup()  # Should not raise


# ===========================================================================
# agent_tools tests
# ===========================================================================

class TestToolDefinitions:

    def test_has_six_tools(self):
        from skills.agent_tools import TOOL_DEFINITIONS
        assert len(TOOL_DEFINITIONS) == 6
        names = [t['name'] for t in TOOL_DEFINITIONS]
        assert 'bash_execute' in names
        assert 'python_execute' in names
        assert 'read_file' in names
        assert 'write_file' in names
        assert 'ask_user' in names
        assert 'task_complete' in names


class TestToolRegistryGetDefinitions:

    def test_interactive_includes_ask_user(self):
        from skills.agent_tools import ToolRegistry
        mock_sandbox = MagicMock()
        reg = ToolRegistry(sandbox=mock_sandbox, interactive=True)
        defs = reg.get_tool_definitions()
        names = [t['name'] for t in defs]
        assert 'ask_user' in names
        assert len(defs) == 6

    def test_non_interactive_excludes_ask_user(self):
        from skills.agent_tools import ToolRegistry
        mock_sandbox = MagicMock()
        reg = ToolRegistry(sandbox=mock_sandbox, interactive=False)
        defs = reg.get_tool_definitions()
        names = [t['name'] for t in defs]
        assert 'ask_user' not in names
        assert len(defs) == 5


class TestToolRegistryDispatch:

    def test_unknown_tool(self):
        from skills.agent_tools import ToolRegistry
        reg = ToolRegistry(sandbox=MagicMock())
        result = reg.dispatch('unknown_tool', {})
        assert 'error' in result
        assert 'Unknown tool' in result['error']

    def test_handler_exception(self):
        from skills.agent_tools import ToolRegistry
        mock_sandbox = MagicMock()
        mock_sandbox.execute.side_effect = RuntimeError('boom')
        reg = ToolRegistry(sandbox=mock_sandbox)
        result = reg.dispatch('bash_execute', {'command': 'echo hi'})
        assert 'error' in result
        assert 'Tool execution failed' in result['error']


class TestToolRegistryHandlers:

    def _make_sandbox_result(self, exit_code=0, stdout='ok', stderr='',
                              timed_out=False):
        from skills.agent_sandbox import SandboxResult
        return SandboxResult(exit_code=exit_code, stdout=stdout,
                             stderr=stderr, timed_out=timed_out)

    def test_handle_bash(self):
        from skills.agent_tools import ToolRegistry
        mock_sandbox = MagicMock()
        mock_sandbox.execute.return_value = self._make_sandbox_result()
        reg = ToolRegistry(sandbox=mock_sandbox)
        result = reg.dispatch('bash_execute', {'command': 'echo hi', 'timeout': 30})
        mock_sandbox.execute.assert_called_once_with('echo hi', timeout=30, language='bash')
        assert result['exit_code'] == 0

    def test_handle_bash_timeout_capped(self):
        from skills.agent_tools import ToolRegistry
        mock_sandbox = MagicMock()
        mock_sandbox.execute.return_value = self._make_sandbox_result()
        reg = ToolRegistry(sandbox=mock_sandbox)
        reg.dispatch('bash_execute', {'command': 'long', 'timeout': 5000})
        mock_sandbox.execute.assert_called_once_with('long', timeout=1800, language='bash')

    def test_handle_bash_default_timeout(self):
        from skills.agent_tools import ToolRegistry
        mock_sandbox = MagicMock()
        mock_sandbox.execute.return_value = self._make_sandbox_result()
        reg = ToolRegistry(sandbox=mock_sandbox)
        reg.dispatch('bash_execute', {'command': 'echo'})
        mock_sandbox.execute.assert_called_once_with('echo', timeout=600, language='bash')

    def test_handle_python(self):
        from skills.agent_tools import ToolRegistry
        mock_sandbox = MagicMock()
        mock_sandbox.execute.return_value = self._make_sandbox_result(stdout='42')
        reg = ToolRegistry(sandbox=mock_sandbox)
        result = reg.dispatch('python_execute', {'code': 'print(42)'})
        mock_sandbox.execute.assert_called_once_with('print(42)', timeout=300, language='python')
        assert result['stdout'] == '42'

    def test_handle_read(self):
        from skills.agent_tools import ToolRegistry
        mock_sandbox = MagicMock()
        mock_sandbox.read_file.return_value = self._make_sandbox_result(stdout='file content')
        reg = ToolRegistry(sandbox=mock_sandbox)
        result = reg.dispatch('read_file', {'path': '/tmp/test.txt'})
        mock_sandbox.read_file.assert_called_once_with('/tmp/test.txt')
        assert result['stdout'] == 'file content'

    def test_handle_write(self):
        from skills.agent_tools import ToolRegistry
        mock_sandbox = MagicMock()
        mock_sandbox.write_file.return_value = self._make_sandbox_result(stdout='Wrote 5 bytes')
        reg = ToolRegistry(sandbox=mock_sandbox)
        result = reg.dispatch('write_file', {'path': 'out.txt', 'content': 'hello'})
        mock_sandbox.write_file.assert_called_once_with('out.txt', 'hello')
        assert 'Wrote' in result['stdout']

    def test_handle_ask_interactive(self):
        from skills.agent_tools import ToolRegistry
        mock_sandbox = MagicMock()
        mock_input = MagicMock(return_value='yes')
        reg = ToolRegistry(sandbox=mock_sandbox, interactive=True, user_input_fn=mock_input)
        result = reg.dispatch('ask_user', {'question': 'Continue?'})
        assert result['answer'] == 'yes'
        mock_input.assert_called_once_with('Continue?')

    def test_handle_ask_non_interactive(self):
        from skills.agent_tools import ToolRegistry
        mock_sandbox = MagicMock()
        reg = ToolRegistry(sandbox=mock_sandbox, interactive=False)
        result = reg.dispatch('ask_user', {'question': 'Continue?'})
        assert 'error' in result
        assert 'not available' in result['error']

    def test_handle_complete(self):
        from skills.agent_tools import ToolRegistry
        mock_sandbox = MagicMock()
        reg = ToolRegistry(sandbox=mock_sandbox)
        result = reg.dispatch('task_complete', {'summary': 'All done', 'status': 'success'})
        assert result['summary'] == 'All done'
        assert result['status'] == 'success'
        assert result['completed'] is True

    def test_handle_complete_defaults(self):
        from skills.agent_tools import ToolRegistry
        mock_sandbox = MagicMock()
        reg = ToolRegistry(sandbox=mock_sandbox)
        result = reg.dispatch('task_complete', {})
        assert result['summary'] == ''
        assert result['status'] == 'success'


class TestSandboxResultToDict:

    def test_normal_result(self):
        from skills.agent_sandbox import SandboxResult
        from skills.agent_tools import _sandbox_result_to_dict
        r = SandboxResult(exit_code=0, stdout='ok', stderr='')
        d = _sandbox_result_to_dict(r)
        assert d == {'exit_code': 0, 'stdout': 'ok', 'stderr': ''}
        assert 'timed_out' not in d

    def test_timed_out_result(self):
        from skills.agent_sandbox import SandboxResult
        from skills.agent_tools import _sandbox_result_to_dict
        r = SandboxResult(exit_code=-1, stdout='', stderr='timeout', timed_out=True)
        d = _sandbox_result_to_dict(r)
        assert d['timed_out'] is True


class TestDefaultInput:

    def test_calls_input(self):
        from skills.agent_tools import _default_input
        with patch('builtins.input', return_value='user answer') as mock_input:
            result = _default_input('What?')
        assert result == 'user answer'
        mock_input.assert_called_once()


# ===========================================================================
# agent_executor tests
# ===========================================================================

class TestBuildSystemPrompt:

    def test_basic(self):
        from skills.agent_executor import _build_system_prompt
        result = _build_system_prompt('Do step 1\nDo step 2', {})
        assert 'SKILL DOCUMENT' in result
        assert 'Do step 1' in result
        assert 'Parameters' not in result

    def test_with_params(self):
        from skills.agent_executor import _build_system_prompt
        result = _build_system_prompt('skill', {'comp': 'odh', 'app': 'v35'})
        assert 'Parameters provided:' in result
        assert 'comp = odh' in result
        assert 'app = v35' in result

    def test_with_working_dir(self):
        from skills.agent_executor import _build_system_prompt
        result = _build_system_prompt('skill', {}, working_dir='/tmp/work')
        assert '/tmp/work' in result
        assert 'Do NOT cd elsewhere' in result

    def test_without_working_dir(self):
        from skills.agent_executor import _build_system_prompt
        result = _build_system_prompt('skill', {}, working_dir=None)
        assert 'Working directory' not in result


class TestBuildAssistantContent:

    def test_text_only(self):
        from skills.agent_executor import _build_assistant_content
        response = MagicMock()
        response.content = 'I will do this'
        response.tool_calls = []
        blocks = _build_assistant_content(response)
        assert len(blocks) == 1
        assert blocks[0] == {'type': 'text', 'text': 'I will do this'}

    def test_tool_calls(self):
        from skills.agent_executor import _build_assistant_content
        response = MagicMock()
        response.content = None
        response.tool_calls = [
            {'id': 'tc1', 'name': 'bash_execute', 'input': {'command': 'echo hi'}},
        ]
        blocks = _build_assistant_content(response)
        assert len(blocks) == 1
        assert blocks[0]['type'] == 'tool_use'
        assert blocks[0]['name'] == 'bash_execute'

    def test_text_and_tool_calls(self):
        from skills.agent_executor import _build_assistant_content
        response = MagicMock()
        response.content = 'Let me run this'
        response.tool_calls = [
            {'id': 'tc1', 'name': 'bash_execute', 'input': {'command': 'ls'}},
        ]
        blocks = _build_assistant_content(response)
        assert len(blocks) == 2
        assert blocks[0]['type'] == 'text'
        assert blocks[1]['type'] == 'tool_use'


class TestSummarizeInput:

    def test_empty(self):
        from skills.agent_executor import _summarize_input
        assert _summarize_input(None) == ''
        assert _summarize_input({}) == ''

    def test_command(self):
        from skills.agent_executor import _summarize_input
        result = _summarize_input({'command': 'echo hello'})
        assert result == 'echo hello'

    def test_command_long(self):
        from skills.agent_executor import _summarize_input
        long_cmd = 'x' * 100
        result = _summarize_input({'command': long_cmd})
        assert result.endswith('...')
        assert len(result) <= 84

    def test_code(self):
        from skills.agent_executor import _summarize_input
        result = _summarize_input({'code': 'print(42)'})
        assert result == 'print(42)'

    def test_code_long(self):
        from skills.agent_executor import _summarize_input
        long_code = 'x' * 100
        result = _summarize_input({'code': long_code})
        assert result.endswith('...')

    def test_path(self):
        from skills.agent_executor import _summarize_input
        result = _summarize_input({'path': '/tmp/test.txt'})
        assert result == '/tmp/test.txt'

    def test_question(self):
        from skills.agent_executor import _summarize_input
        result = _summarize_input({'question': 'What should I do?'})
        assert result == 'What should I do?'

    def test_summary(self):
        from skills.agent_executor import _summarize_input
        result = _summarize_input({'summary': 'All done'})
        assert result == 'All done'

    def test_fallback(self):
        from skills.agent_executor import _summarize_input
        result = _summarize_input({'unknown_key': 'value'})
        assert 'unknown_key' in result


class TestAgentExecutorInit:

    def _make_skill(self):
        skill = MagicMock()
        skill.name = 'test-skill'
        skill.qualified_name = 'source/test-skill'
        skill.path = '/tmp/skills/test-skill'
        skill.metadata.working_dir = 'skill'
        skill.metadata.ic_metadata = MagicMock()
        skill.metadata.ic_metadata.requires_env = ['MY_VAR']
        return skill

    @patch('skills.agent_executor.resolve_working_dir', return_value='/tmp/work')
    @patch('skills.agent_executor.LocalSandbox')
    def test_basic_init(self, mock_sandbox_cls, mock_resolve):
        from skills.agent_executor import AgentExecutor
        skill = self._make_skill()
        llm = MagicMock()
        config = MagicMock()
        config.max_output_bytes = 50000
        executor = AgentExecutor(skill, llm, config=config)
        assert executor.skill == skill
        mock_sandbox_cls.assert_called_once()


class TestAgentExecutorExecute:

    def _make_skill(self, skill_content='# Step 1\nDo something'):
        skill = MagicMock()
        skill.name = 'test-skill'
        skill.qualified_name = 'source/test-skill'
        skill.path = '/tmp/skills/test-skill'
        skill.metadata.working_dir = 'skill'
        skill.metadata.ic_metadata = MagicMock()
        skill.metadata.ic_metadata.requires_env = []
        return skill

    @patch('skills.agent_executor.resolve_working_dir', return_value='/tmp')
    @patch('skills.agent_executor.LocalSandbox')
    def test_dry_run(self, mock_sandbox_cls, mock_resolve):
        from skills.agent_executor import AgentExecutor
        skill = self._make_skill()
        llm = MagicMock()
        config = MagicMock()
        config.max_output_bytes = 50000
        config.max_turns = 10
        config.cost_limit_usd = 1.0
        executor = AgentExecutor(skill, llm, config=config, dry_run=True)
        with patch('builtins.open', mock_open(read_data='# Do step 1')):
            result = executor.execute()
        assert result.status == 'dry_run'
        assert 'tools' in result.stdout.lower() or 'agent' in result.stdout.lower()
        assert len(result.dry_run_steps) > 0

    @patch('skills.agent_executor.resolve_working_dir', return_value='/tmp')
    @patch('skills.agent_executor.LocalSandbox')
    def test_skill_md_not_found(self, mock_sandbox_cls, mock_resolve):
        from skills.agent_executor import AgentExecutor
        skill = self._make_skill()
        llm = MagicMock()
        config = MagicMock()
        config.max_output_bytes = 50000
        executor = AgentExecutor(skill, llm, config=config)
        with patch('builtins.open', side_effect=FileNotFoundError('not found')):
            result = executor.execute()
        assert result.status == 'failed'
        assert 'SKILL.md' in result.stderr

    @patch('skills.agent_executor.resolve_working_dir', return_value='/tmp')
    @patch('skills.agent_executor.LocalSandbox')
    def test_end_turn_no_tools(self, mock_sandbox_cls, mock_resolve):
        """Test agent that responds with end_turn, no tool calls."""
        from skills.agent_executor import AgentExecutor
        skill = self._make_skill()
        llm = MagicMock()
        config = MagicMock()
        config.max_output_bytes = 50000
        config.max_turns = 10
        config.cost_limit_usd = 1.0
        config.total_timeout_seconds = 300

        response = MagicMock()
        response.content = 'All done successfully'
        response.tool_calls = []
        response.stop_reason = 'end_turn'
        response.input_tokens = 100
        response.output_tokens = 50
        llm.create_conversation.return_value = response

        output = io.StringIO()
        executor = AgentExecutor(skill, llm, config=config, output_stream=output)
        with patch('builtins.open', mock_open(read_data='# Do step 1')):
            result = executor.execute()
        assert result.status == 'success'
        assert 'All done successfully' in result.stdout

    @patch('skills.agent_executor.resolve_working_dir', return_value='/tmp')
    @patch('skills.agent_executor.LocalSandbox')
    def test_task_complete_tool(self, mock_sandbox_cls, mock_resolve):
        """Test agent that calls task_complete tool."""
        from skills.agent_executor import AgentExecutor
        skill = self._make_skill()
        llm = MagicMock()
        config = MagicMock()
        config.max_output_bytes = 50000
        config.max_turns = 10
        config.cost_limit_usd = 1.0
        config.total_timeout_seconds = 300

        response = MagicMock()
        response.content = 'Let me complete'
        response.tool_calls = [
            {'id': 'tc1', 'name': 'task_complete',
             'input': {'summary': 'Workflow finished', 'status': 'success'}},
        ]
        response.stop_reason = 'tool_use'
        response.input_tokens = 100
        response.output_tokens = 50
        llm.create_conversation.return_value = response

        output = io.StringIO()
        executor = AgentExecutor(skill, llm, config=config, output_stream=output)
        with patch('builtins.open', mock_open(read_data='# Do step 1')):
            result = executor.execute()
        assert result.status == 'success'
        assert 'Workflow finished' in result.stdout

    @patch('skills.agent_executor.resolve_working_dir', return_value='/tmp')
    @patch('skills.agent_executor.LocalSandbox')
    def test_turn_limit(self, mock_sandbox_cls, mock_resolve):
        """Test agent loop stopped by turn limit."""
        from skills.agent_executor import AgentExecutor
        skill = self._make_skill()
        llm = MagicMock()
        config = MagicMock()
        config.max_output_bytes = 50000
        config.max_turns = 1
        config.cost_limit_usd = 10.0
        config.total_timeout_seconds = 300

        response = MagicMock()
        response.content = 'Working...'
        response.tool_calls = [
            {'id': 'tc1', 'name': 'bash_execute', 'input': {'command': 'echo hi'}},
        ]
        response.stop_reason = 'tool_use'
        response.input_tokens = 100
        response.output_tokens = 50

        llm.create_conversation.return_value = response

        # Mock the sandbox execute to return a result
        mock_sandbox_inst = mock_sandbox_cls.return_value
        from skills.agent_sandbox import SandboxResult
        mock_sandbox_inst.execute.return_value = SandboxResult(
            exit_code=0, stdout='hello', stderr='')

        output = io.StringIO()
        executor = AgentExecutor(skill, llm, config=config, output_stream=output)
        with patch('builtins.open', mock_open(read_data='# Do step 1')):
            result = executor.execute()
        # After 1 turn, should hit limit on second iteration
        assert result.status == 'failed'

    @patch('skills.agent_executor.resolve_working_dir', return_value='/tmp')
    @patch('skills.agent_executor.LocalSandbox')
    def test_llm_exception(self, mock_sandbox_cls, mock_resolve):
        """Test agent handles LLM exception gracefully."""
        from skills.agent_executor import AgentExecutor
        skill = self._make_skill()
        llm = MagicMock()
        config = MagicMock()
        config.max_output_bytes = 50000
        config.max_turns = 10
        config.cost_limit_usd = 1.0
        config.total_timeout_seconds = 300

        llm.create_conversation.side_effect = RuntimeError('API error')

        output = io.StringIO()
        executor = AgentExecutor(skill, llm, config=config, output_stream=output)
        with patch('builtins.open', mock_open(read_data='# Do step 1')):
            result = executor.execute()
        assert result.status == 'failed'
        assert 'Agent error' in result.stderr

    @patch('skills.agent_executor.resolve_working_dir', return_value='/tmp')
    @patch('skills.agent_executor.LocalSandbox')
    def test_no_tool_calls_not_end_turn(self, mock_sandbox_cls, mock_resolve):
        """Test agent loop adds 'Continue' message when no tools and not end_turn."""
        from skills.agent_executor import AgentExecutor
        skill = self._make_skill()
        llm = MagicMock()
        config = MagicMock()
        config.max_output_bytes = 50000
        config.max_turns = 3
        config.cost_limit_usd = 10.0
        config.total_timeout_seconds = 300

        # First response: no tools, not end_turn (model wants to continue thinking)
        response1 = MagicMock()
        response1.content = 'Let me think...'
        response1.tool_calls = []
        response1.stop_reason = 'max_tokens'
        response1.input_tokens = 100
        response1.output_tokens = 50

        # Second response: end_turn
        response2 = MagicMock()
        response2.content = 'Done'
        response2.tool_calls = []
        response2.stop_reason = 'end_turn'
        response2.input_tokens = 150
        response2.output_tokens = 30

        llm.create_conversation.side_effect = [response1, response2]

        output = io.StringIO()
        executor = AgentExecutor(skill, llm, config=config, output_stream=output)
        with patch('builtins.open', mock_open(read_data='# Do step 1')):
            result = executor.execute()
        assert result.status == 'success'
        assert llm.create_conversation.call_count == 2


class TestAgentExecutorEmit:

    @patch('skills.agent_executor.resolve_working_dir', return_value='/tmp')
    @patch('skills.agent_executor.LocalSandbox')
    def test_emit_writes_to_stream(self, mock_sandbox_cls, mock_resolve):
        from skills.agent_executor import AgentExecutor
        skill = MagicMock()
        skill.name = 'test'
        skill.qualified_name = 'src/test'
        skill.path = '/tmp/test'
        skill.metadata.working_dir = 'skill'
        skill.metadata.ic_metadata = MagicMock()
        skill.metadata.ic_metadata.requires_env = []
        output = io.StringIO()
        config = MagicMock()
        config.max_output_bytes = 50000
        executor = AgentExecutor(skill, MagicMock(), config=config, output_stream=output)
        executor._emit('hello world')
        assert output.getvalue() == 'hello world'
