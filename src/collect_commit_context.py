#!/usr/bin/env python3
"""Entry point for collecting commit context from GitHub.

Fetches commit diffs, Dockerfiles, .tekton/ configs, and PR info
for build failures that don't have this context yet.

Requires GITHUB_TOKEN environment variable.
"""

import sys
from config import CollectorConfig
from collectors.commit_context_collector import CommitContextCollector
from logger import setup_logger

logger = setup_logger(__name__)


def main():
    try:
        config = CollectorConfig.from_env()

        if not config.github_token:
            logger.error("GITHUB_TOKEN not configured")
            logger.error("Set GITHUB_TOKEN env var with read access to the repos")
            sys.exit(1)

        collector = CommitContextCollector(config)
        result = collector.run(limit=20)

        logger.info("Collection complete: %d contexts fetched in %.1fs",
                   result['fetched'], result['duration'])

        sys.exit(0)

    except Exception as e:
        logger.error("Commit context collection failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
