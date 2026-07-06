"""Configuration for agent-mode skill execution."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentConfig:
    """Limits and defaults for agent execution.

    All values can be overridden via environment variables or CLI flags.
    """

    max_turns: int = 30
    total_timeout_seconds: int = 900
    cost_limit_usd: float = 2.0
    sandbox_timeout_seconds: int = 600
    max_output_bytes: int = 50_000
    model: str = ''

    @classmethod
    def from_env(cls) -> 'AgentConfig':
        return cls(
            max_turns=int(os.getenv('IC_AGENT_MAX_TURNS', '30')),
            total_timeout_seconds=int(os.getenv('IC_AGENT_TIMEOUT', '900')),
            cost_limit_usd=float(os.getenv('IC_AGENT_COST_LIMIT', '2.0')),
            sandbox_timeout_seconds=int(os.getenv('IC_AGENT_SANDBOX_TIMEOUT', '60')),
            max_output_bytes=int(os.getenv('IC_AGENT_MAX_OUTPUT', '50000')),
            model=os.getenv('IC_AGENT_MODEL', ''),
        )
