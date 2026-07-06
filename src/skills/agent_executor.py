"""Agent-mode skill executor — AI-driven multi-step workflow execution.

Reads SKILL.md as a prompt, sends it to an LLM with tool definitions,
executes tool calls in a sandbox, and loops until the agent signals
completion or a safety limit is reached.
"""

import json
import logging
import os
import sys
from datetime import UTC, datetime

from skills.agent_config import AgentConfig
from skills.agent_sandbox import LocalSandbox
from skills.agent_state import AgentState
from skills.agent_tools import ToolRegistry
from skills.models import ExecutionResult, resolve_working_dir

logger = logging.getLogger(__name__)

_SYSTEM_PREFIX = (
    'You are an automated CI/CD agent executing a skill workflow. '
    'Follow the instructions in the skill document step by step. '
    'Use the provided tools to execute commands and inspect results. '
    'When all steps are complete, call task_complete with a summary.\n\n'
    'Rules:\n'
    '- Execute commands exactly as specified in the workflow\n'
    '- Check command exit codes and stderr — stop on unexpected failures\n'
    '- Do not skip steps unless a step explicitly says it is optional\n'
    '- Do not invent data — only use data from command outputs\n'
    '- Call task_complete when done\n\n'
)


def _build_system_prompt(skill_md_content, params, working_dir=None):
    """Build system prompt from SKILL.md content and parameters."""
    prompt = _SYSTEM_PREFIX

    if working_dir:
        prompt += 'Working directory: {}\n'.format(working_dir)
        prompt += 'All commands execute in this directory. Do NOT cd elsewhere.\n\n'

    prompt += '--- SKILL DOCUMENT ---\n\n'
    prompt += skill_md_content
    prompt += '\n\n--- END SKILL DOCUMENT ---\n'

    if params:
        prompt += '\nParameters provided:\n'
        for k, v in params.items():
            prompt += '- {} = {}\n'.format(k, v)

    return prompt


def _build_assistant_content(response):
    """Reconstruct the assistant content blocks for conversation history."""
    blocks = []
    if response.content:
        blocks.append({'type': 'text', 'text': response.content})
    for tc in response.tool_calls:
        blocks.append({
            'type': 'tool_use',
            'id': tc['id'],
            'name': tc['name'],
            'input': tc['input'],
        })
    return blocks



class AgentExecutor:
    """Execute workflow-type skills via an AI agent loop."""

    def __init__(self, skill, llm_provider, config=None, params=None,
                 interactive=False, user_input_fn=None, dry_run=False,
                 triggered_by='cli', component_name=None, application=None,
                 triage_item_id=None, output_stream=None):
        self.skill = skill
        self._llm = llm_provider
        self._config = config or AgentConfig.from_env()
        self._params = params or {}
        self._interactive = interactive
        self._dry_run = dry_run
        self._triggered_by = triggered_by
        self._component_name = component_name
        self._application = application
        self._triage_item_id = triage_item_id
        self._output = output_stream or sys.stdout

        declared_env = []
        if skill.metadata.ic_metadata:
            declared_env = skill.metadata.ic_metadata.requires_env

        self._sandbox = LocalSandbox(
            working_dir=resolve_working_dir(skill.path, skill.metadata.working_dir),
            declared_env_vars=declared_env,
            max_output_bytes=self._config.max_output_bytes,
        )
        self._tools = ToolRegistry(
            sandbox=self._sandbox,
            interactive=interactive,
            user_input_fn=user_input_fn,
        )

    def execute(self):
        """Run the agent loop and return ExecutionResult."""
        started = datetime.now(UTC).isoformat()

        skill_md_path = os.path.join(self.skill.path, 'SKILL.md')
        try:
            with open(skill_md_path) as f:
                skill_content = f.read()
        except Exception as e:
            return self._result(
                status='failed',
                stderr='Cannot read SKILL.md: {}'.format(e),
                started_at=started,
            )

        system = _build_system_prompt(
            skill_content, self._params,
            working_dir=self._sandbox._working_dir,
        )
        tool_defs = self._tools.get_tool_definitions()
        state = AgentState()

        if self._dry_run:
            return self._result(
                status='dry_run',
                stdout='Agent would execute SKILL.md with {} tools'.format(
                    len(tool_defs)),
                started_at=started,
                dry_run_steps=[
                    'System prompt: {} chars'.format(len(system)),
                    'Tools: {}'.format(', '.join(t['name'] for t in tool_defs)),
                    'Max turns: {}'.format(self._config.max_turns),
                    'Cost limit: ${}'.format(self._config.cost_limit_usd),
                ],
            )

        state.add_message('user', 'Execute the skill workflow now.')

        self._emit('\n[Agent] Starting skill: {}\n'.format(self.skill.name))

        try:
            return self._agent_loop(system, tool_defs, state, started)
        except KeyboardInterrupt:
            state.mark_complete('Interrupted by user', 'failed')
            return self._state_to_result(state, started)
        except Exception as e:
            logger.exception("Agent loop error")
            return self._result(
                status='failed',
                stderr='Agent error: {}'.format(e),
                started_at=started,
                duration_seconds=state.elapsed_seconds,
            )
        finally:
            self._sandbox.cleanup()

    def _agent_loop(self, system, tool_defs, state, started):
        """Core agent loop: LLM → tool_use → execute → repeat."""
        while not state.completed:
            ok, reason = state.within_limits(
                self._config.max_turns,
                self._config.cost_limit_usd,
                self._config.total_timeout_seconds,
            )
            if not ok:
                state.mark_complete(reason, 'failed')
                self._emit('\n[Agent] Stopped: {}\n'.format(reason))
                break

            response = self._llm.create_conversation(
                system=system,
                messages=state.messages,
                tools=tool_defs,
                max_tokens=4096,
            )
            state.record_usage(response.input_tokens, response.output_tokens)

            if response.content:
                self._emit('\n[Agent] {}\n'.format(response.content))

            if not response.tool_calls:
                if response.stop_reason == 'end_turn':
                    state.mark_complete(
                        response.content or 'Agent finished without task_complete',
                        'success',
                    )
                    break
                state.add_message('assistant', response.content or '')
                state.add_message('user', 'Continue with the next step.')
                continue

            assistant_content = _build_assistant_content(response)
            state.add_message('assistant', assistant_content)

            tool_results = []
            for tc in response.tool_calls:
                self._emit('[Tool] {}({})\n'.format(
                    tc['name'],
                    _summarize_input(tc['input']),
                ))

                result = self._tools.dispatch(tc['name'], tc['input'])

                if tc['name'] == 'task_complete':
                    state.mark_complete(
                        result.get('summary', ''),
                        result.get('status', 'success'),
                    )

                if result.get('stdout'):
                    preview = result['stdout'][:500]
                    self._emit('  stdout: {}\n'.format(preview))
                if result.get('stderr'):
                    self._emit('  stderr: {}\n'.format(result['stderr'][:300]))
                if result.get('exit_code', 0) != 0:
                    self._emit('  exit_code: {}\n'.format(result.get('exit_code')))

                tool_results.append({
                    'type': 'tool_result',
                    'tool_use_id': tc['id'],
                    'content': json.dumps(result),
                })

            state.messages.append({'role': 'user', 'content': tool_results})

        return self._state_to_result(state, started)

    def _state_to_result(self, state, started):
        return self._result(
            status=state.final_status or 'success',
            stdout=state.final_summary,
            stderr='',
            started_at=started,
            duration_seconds=state.elapsed_seconds,
            steps_executed=state.turn_count,
            steps_total=state.turn_count,
        )

    def _result(self, **kwargs):
        kwargs.setdefault('skill_name', self.skill.qualified_name)
        kwargs.setdefault('risk_level', 'medium')
        kwargs.setdefault('triggered_by', self._triggered_by)
        kwargs.setdefault('component_name', self._component_name)
        kwargs.setdefault('application', self._application)
        kwargs.setdefault('triage_item_id', self._triage_item_id)
        return ExecutionResult(**kwargs)

    def _emit(self, text):
        self._output.write(text)
        self._output.flush()


def _summarize_input(tool_input):
    """Short summary of tool input for display."""
    if not tool_input:
        return ''
    if 'command' in tool_input:
        cmd = tool_input['command']
        return cmd[:80] + '...' if len(cmd) > 80 else cmd
    if 'code' in tool_input:
        code = tool_input['code']
        return code[:60] + '...' if len(code) > 60 else code
    if 'path' in tool_input:
        return tool_input['path']
    if 'question' in tool_input:
        return tool_input['question'][:60]
    if 'summary' in tool_input:
        return tool_input['summary'][:60]
    return str(tool_input)[:80]
