"""Direct Anthropic API LLM provider adapter.

Wraps the Anthropic API using an API key for authentication.
Falls back to the ANTHROPIC_API_KEY environment variable if no key is provided.
"""

from anthropic import Anthropic

from clients.llm_provider import LLMProvider, LLMResponse


def _parse_response(response):
    """Parse Anthropic response blocks into LLMResponse format."""
    tool_calls = []
    content_text = ''

    for block in response.content:
        if block.type == 'tool_use':
            tool_calls.append({
                'id': block.id,
                'name': block.name,
                'input': block.input,
            })
        elif block.type == 'text':
            content_text = block.text

    return LLMResponse(
        content=content_text,
        tool_calls=tool_calls,
        model=response.model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        stop_reason=response.stop_reason,
    )


class AnthropicDirectProvider(LLMProvider):
    """Claude via the direct Anthropic API.

    Authentication: API key (constructor arg or ANTHROPIC_API_KEY env var).
    """

    def __init__(self, api_key=None, model='claude-sonnet-4-5-20250929'):
        """Initialize Anthropic direct provider.

        Args:
            api_key: Anthropic API key (falls back to ANTHROPIC_API_KEY env var)
            model: Claude model name
        """
        self._model = model
        self._client = Anthropic(api_key=api_key)

    def create_message(self, system, user_content, tools=None, max_tokens=4096):
        """Call Claude via the Anthropic API and return standardized response."""
        kwargs = {
            'model': self._model,
            'max_tokens': max_tokens,
            'system': system,
            'messages': [{'role': 'user', 'content': user_content}],
        }

        if tools:
            kwargs['tools'] = tools
            kwargs['tool_choice'] = {'type': 'any'}

        response = self._client.messages.create(**kwargs)
        return _parse_response(response)

    def create_conversation(self, system, messages, tools=None, max_tokens=4096):
        """Multi-turn conversation for agent-mode tool_use loops."""
        kwargs = {
            'model': self._model,
            'max_tokens': max_tokens,
            'system': system,
            'messages': messages,
        }

        if tools:
            kwargs['tools'] = tools
            kwargs['tool_choice'] = {'type': 'auto'}

        response = self._client.messages.create(**kwargs)
        return _parse_response(response)

    def model_name(self):
        """Return model identifier for tracking."""
        return self._model
