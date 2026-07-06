#!/usr/bin/env python3
"""Diagnose GitHub API access and permissions.

Checks:
- Token validity
- Rate limits
- Access to common repos in the database
- Scopes available
"""

import sys

from clients.github_client import GitHubClient
from config import CollectorConfig
from logger import setup_logger
from repositories import DatabaseConnection

logger = setup_logger(__name__)


def check_token_validity(github):
    """Test if token is valid by fetching rate limit."""
    rate = github.check_rate_limit()
    if rate:
        logger.info("✓ Token is valid")
        logger.info("  Rate limit: %d/%d remaining (resets at %d)",
                   rate['remaining'], rate['limit'], rate['reset'])
        return True
    else:
        logger.error("✗ Token is invalid or GitHub API is unreachable")
        return False


def check_token_scopes(github):
    """Check OAuth scopes of the token."""
    resp = github._get('/user')
    if resp:
        scopes = resp.headers.get('X-OAuth-Scopes', '')
        logger.info("✓ Token scopes: %s", scopes or '(none)')

        if 'repo' in scopes:
            logger.info("  ✓ Has 'repo' scope (full access to private repos)")
        elif 'public_repo' in scopes:
            logger.info("  ⚠ Has 'public_repo' scope only (no private repos)")
        else:
            logger.warning("  ✗ Missing 'repo' or 'public_repo' scope")
        return True
    else:
        logger.error("✗ Cannot check token scopes")
        return False


def check_repo_access(github, config):
    """Test access to repositories in the database."""
    db = DatabaseConnection(config.db)

    # Get unique repos from recent failures
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT repository_url, COUNT(*) as failures
            FROM build_failures
            WHERE application = %s
              AND commit_sha IS NOT NULL
              AND repository_url IS NOT NULL
            GROUP BY repository_url
            ORDER BY failures DESC
            LIMIT 10
        """, (config.k8s.application_name,))

        repos = cursor.fetchall()

    if not repos:
        logger.info("No repositories found in database")
        return True

    logger.info("Testing access to %d repositories:", len(repos))

    accessible = 0
    not_found = 0

    for repo_url, failure_count in repos:
        from clients.github_client import parse_github_repo
        parsed = parse_github_repo(repo_url)
        if not parsed:
            logger.warning("  ⚠ Cannot parse: %s", repo_url)
            continue

        owner, repo_name = parsed

        # Try to fetch repo metadata
        resp = github._get('/repos/{}/{}'.format(owner, repo_name))
        if resp:
            accessible += 1
            logger.info("  ✓ %s/%s (%d failures)", owner, repo_name, failure_count)
        else:
            # Check last error logged
            # This is a bit hacky but works for diagnosis
            logger.warning("  ✗ %s/%s - check logs above for error type", owner, repo_name)
            not_found += 1

    logger.info("")
    logger.info("Summary:")
    logger.info("  Accessible: %d/%d", accessible, len(repos))
    if not_found > 0:
        logger.info("  Not Found/Forbidden: %d", not_found)
        logger.info("  → Check logs above for 403 (permissions) vs 404 (doesn't exist)")

    return accessible > 0


def main():
    config = CollectorConfig.from_env()

    if not config.github_token:
        logger.error("GITHUB_TOKEN not configured in .env")
        logger.error("Set GITHUB_TOKEN with a personal access token from:")
        logger.error("  https://github.com/settings/tokens")
        logger.error("Minimum scopes needed: 'public_repo' (or 'repo' for private repos)")
        sys.exit(1)

    github = GitHubClient(config.github_token)

    logger.info("=" * 70)
    logger.info("GitHub API Access Diagnostics")
    logger.info("=" * 70)
    logger.info("")

    # Check 1: Token validity
    logger.info("[1/3] Checking token validity...")
    if not check_token_validity(github):
        logger.error("Token is invalid. Stopping.")
        sys.exit(1)
    logger.info("")

    # Check 2: Token scopes
    logger.info("[2/3] Checking token scopes...")
    check_token_scopes(github)
    logger.info("")

    # Check 3: Repo access
    logger.info("[3/3] Testing repository access...")
    if not check_repo_access(github, config):
        logger.error("Cannot access any repositories")
        sys.exit(1)
    logger.info("")

    logger.info("=" * 70)
    logger.info("Diagnosis Complete")
    logger.info("=" * 70)
    logger.info("")
    logger.info("If you see 403 errors above:")
    logger.info("  1. Check token scopes: needs 'repo' for private repos")
    logger.info("  2. For SSO orgs: authorize token at https://github.com/settings/tokens")
    logger.info("  3. See docs/TROUBLESHOOTING_GITHUB_API.md for details")
    logger.info("")
    logger.info("If all checks passed, commit context collection should work fine.")


if __name__ == '__main__':
    main()
