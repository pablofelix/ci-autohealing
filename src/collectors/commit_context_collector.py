"""Collector for commit context data from GitHub.

Fetches commit diffs, Dockerfile content, .tekton/ configs, and PR info
for build failures that have a commit_sha but no commit_context yet.

Idempotent: skips failures that already have commit_context populated.
No disk usage: all data fetched via GitHub REST API and stored in DB.
"""

import json
import time

from logger import setup_logger
from clients.github_client import GitHubClient
from repositories import DatabaseConnection

logger = setup_logger(__name__)


class CommitContextCollector:
    """Fetches and stores commit context for build failures."""

    def __init__(self, config, db=None, github=None):
        # type: (CollectorConfig, ..., ...) -> None
        if db is None:
            db = DatabaseConnection(config.db)
        self.config = config
        self.db = db
        self.github = github or GitHubClient(
            token=config.github_token
        )

    def get_pending_failures(self, limit=20):
        # type: (int) -> List[Dict[str, Any]]
        """Get failures that need commit context fetched.

        Criteria: has commit_sha, has repository_url, no commit_context yet.
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, component_name, pipelinerun_name,
                       commit_sha, repository_url, branch
                FROM build_failures
                WHERE application = %s
                  AND commit_sha IS NOT NULL
                  AND repository_url IS NOT NULL
                  AND commit_context IS NULL
                ORDER BY first_detected_at DESC
                LIMIT %s
            """, (self.config.k8s.application_name, limit))

            results = []
            for row in cursor.fetchall():
                results.append({
                    'id': row[0],
                    'component_name': row[1],
                    'pipelinerun_name': row[2],
                    'commit_sha': row[3],
                    'repository_url': row[4],
                    'branch': row[5],
                })
            return results

    def store_context(self, failure_id, context):
        # type: (int, Dict[str, Any]) -> None
        """Store commit context in the build_failures table."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE build_failures
                SET commit_context = %s
                WHERE id = %s
            """, (json.dumps(context), failure_id))
            conn.commit()

    def run(self, limit=20):
        # type: (int) -> Dict[str, Any]
        """Fetch commit context for pending failures.

        Returns stats dict: fetched count, skipped, duration.
        """
        start_time = time.time()

        logger.info("=" * 70)
        logger.info("Commit Context Collection")
        logger.info("Application: %s", self.config.k8s.application_name)
        logger.info("=" * 70)

        # Check rate limit
        rate = self.github.check_rate_limit()
        if rate:
            logger.info("GitHub API rate limit: %d/%d remaining",
                       rate['remaining'], rate['limit'])
            if rate['remaining'] < 50:
                logger.warning("Rate limit too low, skipping collection")
                return {'fetched': 0, 'skipped': 0, 'duration': 0,
                        'reason': 'rate_limit'}

        pending = self.get_pending_failures(limit=limit)

        if not pending:
            logger.info("No failures need commit context")
            return {'fetched': 0, 'skipped': 0, 'duration': 0}

        logger.info("Found %d failures needing commit context", len(pending))

        fetched = 0
        skipped = 0
        seen_shas = {}

        for i, failure in enumerate(pending, 1):
            sha = failure['commit_sha']
            component = failure['component_name']
            repo_url = failure['repository_url']

            logger.info("[%d/%d] %s (SHA: %s)",
                       i, len(pending), component, sha[:8])

            # Reuse context if same SHA already fetched in this run
            if sha in seen_shas:
                logger.info("  Reusing context from earlier fetch")
                self.store_context(failure['id'], seen_shas[sha])
                fetched += 1
                continue

            try:
                context = self.github.get_commit_context(
                    repo_url, sha, branch=failure.get('branch')
                )

                if context:
                    self.store_context(failure['id'], context)
                    seen_shas[sha] = context
                    fetched += 1
                else:
                    logger.warning("  No context returned")
                    skipped += 1

            except Exception as e:
                logger.error("  Failed: %s", e)
                skipped += 1

        duration = time.time() - start_time

        logger.info("=" * 70)
        logger.info("Commit Context Collection Complete")
        logger.info("Fetched: %d, Skipped: %d, Duration: %.1fs",
                   fetched, skipped, duration)
        logger.info("=" * 70)

        return {
            'fetched': fetched,
            'skipped': skipped,
            'duration': duration,
        }
