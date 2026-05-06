"""Configuration management for CI Auto-Healing collectors."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class DatabaseConfig:
    """PostgreSQL database configuration."""

    host: str
    port: int
    user: str
    password: str
    database: str

    @property
    def connection_string(self) -> str:
        """Build PostgreSQL connection string."""
        return f"host={self.host} port={self.port} user={self.user} password={self.password} dbname={self.database}"


@dataclass(frozen=True)
class KubernetesConfig:
    """Kubernetes/OpenShift configuration."""

    namespace: str
    application_name: str
    kubearchive_api_url: Optional[str] = None


@dataclass(frozen=True)
class LLMConfig:
    """LLM provider configuration for AI analysis."""

    provider: str  # 'vertex_ai', 'anthropic', etc.
    model: str
    project_id: Optional[str] = None  # Vertex AI
    region: str = 'us-east5'          # Vertex AI
    api_key: Optional[str] = None     # Direct Anthropic
    max_analysis_per_run: int = 5
    min_confidence: float = 0.8


@dataclass(frozen=True)
class CollectorConfig:
    """Main collector configuration."""

    db: DatabaseConfig
    k8s: KubernetesConfig
    llm: Optional[LLMConfig] = None
    github_token: Optional[str] = None
    components_file: Optional[Path] = None

    @classmethod
    def from_env(cls, env_path: Optional[Path] = None) -> 'CollectorConfig':
        """Load configuration from environment variables.

        Args:
            env_path: Path to .env file. If None, searches parent directories.

        Returns:
            CollectorConfig instance with all settings loaded.
        """
        # Load .env file
        if env_path is None:
            # Search for .env in current and parent directories
            current = Path.cwd()
            for parent in [current] + list(current.parents):
                env_file = parent / '.env'
                if env_file.exists():
                    load_dotenv(env_file)
                    break
        else:
            load_dotenv(env_path)

        # Build database config
        db_config = DatabaseConfig(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', '5433')),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', 'admin'),
            database=os.getenv('DB_NAME', 'konflux_monitoring')
        )

        # Build Kubernetes config
        k8s_config = KubernetesConfig(
            namespace=os.getenv('NAMESPACE', 'NAMESPACE_PLACEHOLDER'),
            application_name=os.getenv('APPLICATION_NAME', 'acme-v2-0'),
            kubearchive_api_url=os.getenv('KUBEARCHIVE_API_URL')
        )

        # Components file
        components_file_str = os.getenv('COMPONENTS_FILE')
        components_file = Path(components_file_str) if components_file_str else None

        # LLM config (optional - only for AI analysis)
        # Auto-detect Vertex AI if Anthropic SDK env vars are set
        llm_provider = os.getenv('LLM_PROVIDER')
        if not llm_provider and os.getenv('ANTHROPIC_VERTEX_PROJECT_ID'):
            llm_provider = 'vertex_ai'

        llm_config = None
        if llm_provider:
            # Default model depends on provider
            if llm_provider == 'vertex_ai':
                default_model = 'claude-sonnet-4-6'  # Vertex AI
            else:
                default_model = 'claude-sonnet-4-6'  # Direct API

            llm_config = LLMConfig(
                provider=llm_provider,
                model=os.getenv('LLM_MODEL', default_model),
                # Support both custom vars and Anthropic SDK native vars
                project_id=os.getenv('VERTEX_PROJECT_ID') or os.getenv('ANTHROPIC_VERTEX_PROJECT_ID'),
                region=os.getenv('VERTEX_REGION') or os.getenv('CLOUD_ML_REGION', 'us-east5'),
                api_key=os.getenv('ANTHROPIC_API_KEY'),
                max_analysis_per_run=int(os.getenv('AI_MAX_PER_RUN', '5')),
                min_confidence=float(os.getenv('AI_MIN_CONFIDENCE', '0.8')),
            )

        # GitHub token (for commit context collection)
        github_token = os.getenv('GITHUB_TOKEN')

        return cls(
            db=db_config,
            k8s=k8s_config,
            llm=llm_config,
            github_token=github_token,
            components_file=components_file
        )
