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
class CollectorConfig:
    """Main collector configuration."""

    db: DatabaseConfig
    k8s: KubernetesConfig
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

        return cls(
            db=db_config,
            k8s=k8s_config,
            components_file=components_file
        )
