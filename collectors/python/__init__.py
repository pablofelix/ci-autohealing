"""Python collectors for CI Auto-Healing system.

This package provides Python-based collectors for Konflux CI/CD build failures.
It uses the KubeArchive API to fetch archived PipelineRuns and logs.

Main modules:
- collect_failures: Scan components and collect build failures
- fetch_archived_logs: Fetch logs from archived PipelineRuns
- kubearchive_client: KubeArchive API client
- database: PostgreSQL database operations
- config: Configuration management
- models: Data models
"""

__version__ = '1.0.0'
