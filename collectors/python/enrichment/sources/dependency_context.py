"""Dependency context source - extracts dependency file changes from commit context.

Analyzes commit diffs to identify changes in dependency manifests:
- Python: requirements.txt, Pipfile, pyproject.toml
- Node.js: package.json, package-lock.json, yarn.lock
- Go: go.mod, go.sum
- Others: Gemfile, pom.xml, Cargo.toml
"""

from typing import Any, Dict, Optional
import json

from config import CollectorConfig
from enrichment.context_source import ContextSource
from logger import setup_logger

logger = setup_logger(__name__)

# Truncation limits for context size management
MAX_PATCH_LENGTH = 3000  # ~75 lines of diff context per dependency file


class DependencyContextSource(ContextSource):
    """Extracts dependency file changes from existing commit_context."""

    DEPENDENCY_PATTERNS = [
        'requirements.txt',
        'requirements/',
        'Pipfile',
        'pyproject.toml',
        'package.json',
        'package-lock.json',
        'go.mod',
        'go.sum',
        'Gemfile',
        'pom.xml',
        'Cargo.toml',
    ]

    def __init__(self, config: CollectorConfig):
        super().__init__(config)

    def fetch(self, failure: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract dependency changes from commit_context."""
        try:
            commit_context = failure.get('commit_context')
            if not commit_context:
                return None

            if isinstance(commit_context, str):
                try:
                    commit_context = json.loads(commit_context)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Failed to parse commit_context for failure %s",
                                 failure.get('id'))
                    return None

            commit = commit_context.get('commit', {})
            files = commit.get('files', [])

            if not files:
                return None

            dependency_changes = {}

            for f in files:
                fname = f.get('filename', '')

                if not self._is_dependency_file(fname):
                    continue

                change_info = {
                    'status': f.get('status', 'modified'),
                    'additions': f.get('additions', 0),
                    'deletions': f.get('deletions', 0),
                }

                patch = f.get('patch', '')
                if patch:
                    change_info['patch'] = patch[:MAX_PATCH_LENGTH]

                dependency_changes[fname] = change_info

            if not dependency_changes:
                return None

            logger.info("Found %d dependency file changes", len(dependency_changes))

            return {
                'dependency_changes': dependency_changes
            }

        except Exception as e:
            logger.error("DependencyContextSource failed: %s", e)
            return None

    def _is_dependency_file(self, filename: str) -> bool:
        """Check if filename matches a dependency pattern."""
        return any(pattern in filename for pattern in self.DEPENDENCY_PATTERNS)

    def source_name(self) -> str:
        return 'dependency_changes'

    @property
    def requires_external_api(self) -> bool:
        return False

    @property
    def timeout_seconds(self) -> int:
        return 5
