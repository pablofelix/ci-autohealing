"""Vertex AI LLM provider adapter.

Wraps Claude on Vertex AI (accessed via anthropic SDK's AnthropicVertex client).
Uses Google Cloud Application Default Credentials for authentication.
"""

from anthropic import AnthropicVertex

from clients.llm_provider import LLMProvider, LLMResponse


class VertexAIProvider(LLMProvider):
    """Claude on Vertex AI via the anthropic SDK.

    Authentication: Google Cloud Application Default Credentials (ADC).
    User must run 'gcloud auth application-default login' before using.

    Available regions: us-east5, europe-west1, asia-southeast1
    (check GCP docs for current Claude model availability)
    """

    def __init__(self, project_id, region='us-east5', model='claude-sonnet-4-5-20250929'):
        """Initialize Vertex AI provider.

        Args:
            project_id: GCP project ID with Vertex AI enabled
            region: GCP region where Claude is available
            model: Claude model name (same as Anthropic API names)
        """
        self.project_id = project_id
        self.region = region
        self._model = model
        self._client = AnthropicVertex(
            project_id=self.project_id,
            region=self.region,
        )

    def create_message(self, system, user_content, tools=None, max_tokens=4096):
        """Call Claude via Vertex AI and return standardized response.

        Uses the same messages.create() API as direct Anthropic - only
        authentication differs (GCP ADC instead of API key).
        """
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

        # Parse response blocks into LLMResponse format
        tool_calls = []
        content_text = ''

        for block in response.content:
            if block.type == 'tool_use':
                tool_calls.append({
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

    def model_name(self):
        """Return model identifier for tracking."""
        return self._model
