"""Tool definitions and dispatch for agent-mode skill execution.

Defines the Anthropic tool_use schema for each tool the agent can invoke,
and a ToolRegistry that dispatches tool calls to sandbox operations.
"""

import logging

logger = logging.getLogger(__name__)

TOOL_DEFINITIONS = [
    {
        'name': 'bash_execute',
        'description': (
            'Execute a bash command in a sandboxed environment. '
            'Use for running scripts, CLI tools, file operations, and pipelines. '
            'The command runs with a filtered environment (no secrets unless declared). '
            'Returns stdout, stderr, and exit code.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'command': {
                    'type': 'string',
                    'description': 'The bash command to execute.',
                },
                'timeout': {
                    'type': 'integer',
                    'description': 'Timeout in seconds (default: 300, max: 1800).',
                },
            },
            'required': ['command'],
        },
    },
    {
        'name': 'python_execute',
        'description': (
            'Execute a Python 3 code snippet. '
            'Use for data processing, JSON parsing, calculations, or scripting. '
            'The code runs as python3 -c in a sandboxed environment.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'code': {
                    'type': 'string',
                    'description': 'Python 3 code to execute.',
                },
            },
            'required': ['code'],
        },
    },
    {
        'name': 'read_file',
        'description': (
            'Read the contents of a file. '
            'Use to inspect configuration files, logs, scripts, or data.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'path': {
                    'type': 'string',
                    'description': 'Path to the file to read (relative or absolute).',
                },
            },
            'required': ['path'],
        },
    },
    {
        'name': 'write_file',
        'description': (
            'Write content to a file, creating parent directories if needed. '
            'Use to create scripts, save reports, or write configuration.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'path': {
                    'type': 'string',
                    'description': 'Path to the file to write.',
                },
                'content': {
                    'type': 'string',
                    'description': 'Content to write to the file.',
                },
            },
            'required': ['path', 'content'],
        },
    },
    {
        'name': 'ask_user',
        'description': (
            'Ask the user a question and wait for their response. '
            'Use when you need clarification, confirmation, or a decision. '
            'Only available in interactive mode.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'question': {
                    'type': 'string',
                    'description': 'The question to ask the user.',
                },
            },
            'required': ['question'],
        },
    },
    {
        'name': 'task_complete',
        'description': (
            'Signal that the skill workflow is complete. '
            'Call this when all steps are finished successfully. '
            'Include a summary of what was accomplished.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'summary': {
                    'type': 'string',
                    'description': 'Summary of what was accomplished.',
                },
                'status': {
                    'type': 'string',
                    'enum': ['success', 'partial', 'failed'],
                    'description': 'Final status of the workflow.',
                },
            },
            'required': ['summary', 'status'],
        },
    },
]


class ToolRegistry:
    """Dispatches agent tool calls to sandbox operations."""

    def __init__(self, sandbox, interactive=False, user_input_fn=None):
        self._sandbox = sandbox
        self._interactive = interactive
        self._user_input_fn = user_input_fn or _default_input
        self._handlers = {
            'bash_execute': self._handle_bash,
            'python_execute': self._handle_python,
            'read_file': self._handle_read,
            'write_file': self._handle_write,
            'ask_user': self._handle_ask,
            'task_complete': self._handle_complete,
        }

    def get_tool_definitions(self):
        """Return tool schemas for the LLM, filtered by mode."""
        if self._interactive:
            return list(TOOL_DEFINITIONS)
        return [t for t in TOOL_DEFINITIONS if t['name'] != 'ask_user']

    def dispatch(self, tool_name, tool_input):
        """Execute a tool call, return result dict."""
        handler = self._handlers.get(tool_name)
        if not handler:
            return {'error': 'Unknown tool: {}'.format(tool_name)}
        try:
            return handler(tool_input)
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_name, e)
            return {'error': 'Tool execution failed: {}'.format(e)}

    def _handle_bash(self, inputs):
        command = inputs.get('command', '')
        timeout = min(inputs.get('timeout', 600), 1800)
        result = self._sandbox.execute(command, timeout=timeout, language='bash')
        return _sandbox_result_to_dict(result)

    def _handle_python(self, inputs):
        code = inputs.get('code', '')
        timeout = min(inputs.get('timeout', 300), 1800)
        result = self._sandbox.execute(code, timeout=timeout, language='python')
        return _sandbox_result_to_dict(result)

    def _handle_read(self, inputs):
        path = inputs.get('path', '')
        result = self._sandbox.read_file(path)
        return _sandbox_result_to_dict(result)

    def _handle_write(self, inputs):
        path = inputs.get('path', '')
        content = inputs.get('content', '')
        result = self._sandbox.write_file(path, content)
        return _sandbox_result_to_dict(result)

    def _handle_ask(self, inputs):
        question = inputs.get('question', '')
        if not self._interactive:
            return {'error': 'ask_user not available in autonomous mode'}
        answer = self._user_input_fn(question)
        return {'answer': answer}

    def _handle_complete(self, inputs):
        return {
            'summary': inputs.get('summary', ''),
            'status': inputs.get('status', 'success'),
            'completed': True,
        }


def _sandbox_result_to_dict(result):
    d = {
        'exit_code': result.exit_code,
        'stdout': result.stdout,
        'stderr': result.stderr,
    }
    if result.timed_out:
        d['timed_out'] = True
    return d


def _default_input(question):
    return input('\n[Agent asks] {}\n> '.format(question))
