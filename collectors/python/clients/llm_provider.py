"""Abstract base class for LLM providers.

Defines the contract that all LLM adapters must implement.
Concrete implementations adapt different providers (Vertex AI, Anthropic API,
Gemini) behind this common interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class LLMResponse:
    """Provider-independent structured response from an LLM call.

    Isolates analyzers from SDK-specific response objects (anthropic.Message,
    Gemini GenerateContentResponse, etc.). All providers return this type.
    """

    content: str
    tool_calls: List[Dict[str, Any]]
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str


class LLMProvider(ABC):
    """Abstract interface for LLM providers.

    Subclasses adapt different providers (Vertex AI via AnthropicVertex,
    direct Anthropic API, Gemini) behind this common interface. Analyzers
    depend on LLMProvider, not on any concrete SDK.

    Pattern: Adapter (same as PipelineRunSource for data sources).
    """

    @abstractmethod
    def create_message(self, system, user_content, tools=None, max_tokens=4096):
        # type: (str, str, Optional[List[Dict]], int) -> LLMResponse
        """Send a message to the LLM and return a structured response.

        Args:
            system: System prompt (instructions, knowledge base, examples)
            user_content: User message (the actual request/input)
            tools: Optional list of tool definitions for structured output
            max_tokens: Maximum tokens in response

        Returns:
            LLMResponse with content, tool_calls, token counts
        """

    @abstractmethod
    def model_name(self):
        # type: () -> str
        """Return the model identifier for logging/tracking.

        Used in ai_analysis.model_used column and Langfuse traces.
        """


def create_llm_provider(config):
    # type: (Any) -> LLMProvider
    """Create LLM provider from configuration.

    Factory function with lazy imports - only loads the SDK that's actually
    configured. Allows running the collectors without AI dependencies installed.

    Args:
        config: LLMConfig instance (must have .provider attribute)

    Returns:
        Concrete LLMProvider instance

    Raises:
        ValueError: Unknown provider name
        ImportError: Provider SDK not installed
    """
    if config.provider == 'vertex_ai':
        from clients.vertex_ai_provider import VertexAIProvider
        return VertexAIProvider(
            project_id=config.project_id,
            region=config.region,
            model=config.model,
        )
    elif config.provider == 'anthropic':
        from clients.anthropic_provider import AnthropicDirectProvider
        return AnthropicDirectProvider(
            api_key=config.api_key,
            model=config.model,
        )
    else:
        raise ValueError("Unknown LLM provider: {}. Supported: vertex_ai, anthropic".format(config.provider))
