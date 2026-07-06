"""Conversation state tracking for agent-mode execution."""

import json
import time
from dataclasses import dataclass, field

# Rough per-token cost estimates (Claude Sonnet 4)
_INPUT_COST_PER_TOKEN = 3.0 / 1_000_000
_OUTPUT_COST_PER_TOKEN = 15.0 / 1_000_000


@dataclass
class AgentState:
    """Tracks the full state of an agent execution session."""

    messages: list = field(default_factory=list)
    turn_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    start_time: float = field(default_factory=time.time)
    completed: bool = False
    final_summary: str = ''
    final_status: str = ''

    @property
    def estimated_cost_usd(self):
        return (self.total_input_tokens * _INPUT_COST_PER_TOKEN +
                self.total_output_tokens * _OUTPUT_COST_PER_TOKEN)

    @property
    def elapsed_seconds(self):
        return time.time() - self.start_time

    def add_message(self, role, content):
        self.messages.append({'role': role, 'content': content})

    def add_tool_result(self, tool_use_id, result):
        content_block = {
            'type': 'tool_result',
            'tool_use_id': tool_use_id,
            'content': json.dumps(result) if isinstance(result, dict) else str(result),
        }
        if self.messages and self.messages[-1]['role'] == 'user':
            existing = self.messages[-1]['content']
            if isinstance(existing, list):
                existing.append(content_block)
            else:
                self.messages[-1]['content'] = [content_block]
        else:
            self.messages.append({'role': 'user', 'content': [content_block]})

    def record_usage(self, input_tokens, output_tokens):
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.turn_count += 1

    def mark_complete(self, summary, status='success'):
        self.completed = True
        self.final_summary = summary
        self.final_status = status

    def within_limits(self, max_turns, cost_limit, total_timeout):
        if self.turn_count >= max_turns:
            return False, 'Turn limit reached ({}/{})'.format(
                self.turn_count, max_turns)
        if self.estimated_cost_usd >= cost_limit:
            return False, 'Cost limit reached (${:.4f}/${:.2f})'.format(
                self.estimated_cost_usd, cost_limit)
        if self.elapsed_seconds >= total_timeout:
            return False, 'Timeout reached ({:.0f}s/{:.0f}s)'.format(
                self.elapsed_seconds, total_timeout)
        return True, ''
