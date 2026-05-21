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
class JiraConfig:
    """Jira API configuration for creating tickets."""

    base_url: str
    email: str
    token: str
    project: str

    @property
    def auth(self):
        # type: () -> tuple
        return (self.email, self.token)


@dataclass(frozen=True)
class BatchAnalysisConfig:
    """Batch analysis automation configuration."""

    enabled: bool = True
    max_per_run: int = 20
    fifo_priority: bool = True
    auto_jira: bool = False  # Auto-create Jira tickets (disabled for P0)
    schedule_cron: str = '0 * * * *'  # Hourly by default


@dataclass(frozen=True)
class CollectorConfig:
    """Main collector configuration."""

    db: DatabaseConfig
    k8s: KubernetesConfig
    llm: Optional[LLMConfig] = None
    github_token: Optional[str] = None
    jira: Optional[JiraConfig] = None
    components_file: Optional[Path] = None
    auto_fix_max_per_run: int = 3
    auto_fix_min_confidence: float = 0.95
    batch_analysis: Optional[BatchAnalysisConfig] = None

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
            namespace=os.getenv('NAMESPACE', ''),
            application_name=os.getenv('APPLICATION_NAME', ''),
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

        # Jira config (optional - for ticket creation via ic fix)
        jira_email = os.getenv('JIRA_EMAIL')
        jira_token = os.getenv('JIRA_TOKEN')
        jira_config = None
        if jira_email and jira_token:
            jira_config = JiraConfig(
                base_url=os.getenv('JIRA_BASE_URL', ''),
                email=jira_email,
                token=jira_token,
                project=os.getenv('JIRA_PROJECT', ''),
            )

        # Batch analysis config
        batch_config = BatchAnalysisConfig(
            enabled=os.getenv('BATCH_ANALYSIS_ENABLED', 'true').lower() == 'true',
            max_per_run=int(os.getenv('BATCH_ANALYSIS_MAX_PER_RUN', '20')),
            fifo_priority=os.getenv('BATCH_ANALYSIS_FIFO', 'true').lower() == 'true',
            auto_jira=os.getenv('BATCH_ANALYSIS_AUTO_JIRA', 'false').lower() == 'true',
            schedule_cron=os.getenv('BATCH_ANALYSIS_SCHEDULE', '0 * * * *'),
        )

        return cls(
            db=db_config,
            k8s=k8s_config,
            llm=llm_config,
            github_token=github_token,
            jira=jira_config,
            components_file=components_file,
            auto_fix_max_per_run=int(os.getenv('AUTO_FIX_MAX_PER_RUN', '3')),
            auto_fix_min_confidence=float(os.getenv('AUTO_FIX_MIN_CONFIDENCE', '0.95')),
            batch_analysis=batch_config,
        )
