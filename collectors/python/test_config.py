"""Unit tests for configuration management."""

import unittest
import os
import tempfile
from pathlib import Path
from config import DatabaseConfig, KubernetesConfig, CollectorConfig


class TestDatabaseConfig(unittest.TestCase):
    """Test DatabaseConfig."""

    def test_create_database_config(self):
        """Test DatabaseConfig creation."""
        db_config = DatabaseConfig(
            host="localhost",
            port=5432,
            user="postgres",
            password="secret",
            database="testdb"
        )
        self.assertEqual(db_config.host, "localhost")
        self.assertEqual(db_config.port, 5432)
        self.assertEqual(db_config.user, "postgres")
        self.assertEqual(db_config.password, "secret")
        self.assertEqual(db_config.database, "testdb")

    def test_connection_string(self):
        """Test connection string generation."""
        db_config = DatabaseConfig(
            host="db.example.com",
            port=5433,
            user="myuser",
            password="mypass",
            database="mydb"
        )
        conn_str = db_config.connection_string
        self.assertIn("host=db.example.com", conn_str)
        self.assertIn("port=5433", conn_str)
        self.assertIn("user=myuser", conn_str)
        self.assertIn("password=mypass", conn_str)
        self.assertIn("dbname=mydb", conn_str)

    def test_immutable(self):
        """Test that DatabaseConfig is immutable (frozen)."""
        db_config = DatabaseConfig(
            host="localhost",
            port=5432,
            user="postgres",
            password="secret",
            database="testdb"
        )
        with self.assertRaises(AttributeError):
            db_config.host = "newhost"


class TestKubernetesConfig(unittest.TestCase):
    """Test KubernetesConfig."""

    def test_create_kubernetes_config(self):
        """Test KubernetesConfig creation."""
        k8s_config = KubernetesConfig(
            namespace="test-namespace",
            application_name="test-app",
            kubearchive_api_url="https://api.example.com"
        )
        self.assertEqual(k8s_config.namespace, "test-namespace")
        self.assertEqual(k8s_config.application_name, "test-app")
        self.assertEqual(k8s_config.kubearchive_api_url, "https://api.example.com")

    def test_optional_kubearchive_url(self):
        """Test KubernetesConfig with no KubeArchive URL."""
        k8s_config = KubernetesConfig(
            namespace="test-namespace",
            application_name="test-app"
        )
        self.assertIsNone(k8s_config.kubearchive_api_url)


class TestCollectorConfig(unittest.TestCase):
    """Test CollectorConfig."""

    def test_create_collector_config(self):
        """Test CollectorConfig creation."""
        db_config = DatabaseConfig(
            host="localhost",
            port=5432,
            user="postgres",
            password="secret",
            database="testdb"
        )
        k8s_config = KubernetesConfig(
            namespace="test-ns",
            application_name="test-app"
        )
        config = CollectorConfig(
            db=db_config,
            k8s=k8s_config,
            components_file=Path("/tmp/components.txt")
        )
        self.assertEqual(config.db.host, "localhost")
        self.assertEqual(config.k8s.namespace, "test-ns")
        self.assertEqual(config.components_file, Path("/tmp/components.txt"))

    def test_from_env(self):
        """Test loading configuration from environment."""
        # Create temporary .env file
        env_content = """
DB_HOST=testhost
DB_PORT=5555
DB_USER=testuser
DB_PASSWORD=testpass
DB_NAME=testdb
NAMESPACE=test-namespace
APPLICATION_NAME=test-app
COMPONENTS_FILE=/tmp/test-components.txt
KUBEARCHIVE_API_URL=https://test-api.example.com
"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.env') as f:
            f.write(env_content)
            env_file = f.name

        try:
            config = CollectorConfig.from_env(Path(env_file))

            # Check database config
            self.assertEqual(config.db.host, "testhost")
            self.assertEqual(config.db.port, 5555)
            self.assertEqual(config.db.user, "testuser")
            self.assertEqual(config.db.password, "testpass")
            self.assertEqual(config.db.database, "testdb")

            # Check Kubernetes config
            self.assertEqual(config.k8s.namespace, "test-namespace")
            self.assertEqual(config.k8s.application_name, "test-app")
            self.assertEqual(config.k8s.kubearchive_api_url, "https://test-api.example.com")

            # Check components file
            self.assertEqual(config.components_file, Path("/tmp/test-components.txt"))
        finally:
            os.unlink(env_file)

    def test_from_env_defaults(self):
        """Test loading configuration with defaults."""
        # Clear environment variables from previous tests
        env_vars = [
            'DB_HOST', 'DB_PORT', 'DB_USER', 'DB_PASSWORD', 'DB_NAME',
            'NAMESPACE', 'APPLICATION_NAME', 'COMPONENTS_FILE', 'KUBEARCHIVE_API_URL'
        ]
        for var in env_vars:
            os.environ.pop(var, None)

        # Create minimal .env file
        env_content = "DB_PASSWORD=secret\n"
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.env') as f:
            f.write(env_content)
            env_file = f.name

        try:
            config = CollectorConfig.from_env(Path(env_file))

            # Check defaults
            self.assertEqual(config.db.host, "localhost")
            self.assertEqual(config.db.port, 5433)
            self.assertEqual(config.db.user, "postgres")
            self.assertEqual(config.db.password, "secret")
            self.assertEqual(config.db.database, "konflux_monitoring")

            self.assertEqual(config.k8s.namespace, "NAMESPACE_PLACEHOLDER")
            self.assertEqual(config.k8s.application_name, "acme-v2-0")
            self.assertIsNone(config.k8s.kubearchive_api_url)

            self.assertIsNone(config.components_file)
        finally:
            os.unlink(env_file)


if __name__ == '__main__':
    unittest.main()
