"""Open PRs source - fetches open GitHub PRs targeting the component's branch.

When a build fails, knowing what PRs are open against the same branch can help
the analyzer correlate failures with in-flight changes or identify pending fixes.
"""

import os

from enrichment.context_source import ContextSource
from logger import setup_logger

logger = setup_logger(__name__)


class OpenPRsSource(ContextSource):
    """Fetches open GitHub PRs for a failing component's branch."""

    def __init__(self, config, github_client=None):
        super().__init__(config)
        self._github_client = github_client

    def source_name(self):
        return 'open_prs'

    @property
    def requires_external_api(self):
        return True

    @property
    def timeout_seconds(self):
        return 30

    def fetch(self, failure):
        repo_url = failure.get('repository_url', '')
        branch = failure.get('branch', '')

        if not repo_url:
            return None

        if self._github_client is None:
            token = getattr(self.config, 'github_token', None) or os.environ.get('GITHUB_TOKEN')
            if not token:
                return None
            from clients.github_client import GitHubClient
            self._github_client = GitHubClient(token)

        from clients.github_client import parse_github_repo
        parsed = parse_github_repo(repo_url)
        if not parsed:
            return None

        owner, repo = parsed

        try:
            prs = self._github_client.list_pull_requests(
                owner, repo, base=branch or None, state='open', limit=10
            )
        except Exception as e:
            logger.warning("Failed to fetch open PRs for %s/%s: %s", owner, repo, e)
            return None

        if not prs:
            return None

        logger.info("Found %d open PRs for %s/%s (base: %s)", len(prs), owner, repo, branch or 'any')
        return {'open_prs': prs}
