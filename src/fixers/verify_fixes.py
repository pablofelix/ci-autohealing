#!/usr/bin/env python3
"""Verify resolution attempts: check if PRs merged and builds succeeded.

Run after sync_component_status.py (which marks build_failures resolved when
KubeArchive shows a Succeeded build). This script then correlates:
  1. GitHub PR merge status
  2. DB: did build_failures.is_resolved flip to TRUE after the merge?

If yes to both → was_successful = TRUE.
If PR closed without merge → status = 'abandoned'.

Usage:
  python3 verify_fixes.py             # Check all pending
  python3 verify_fixes.py --dry-run   # Print without writing
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clients.github_client import GitHubClient
from config import CollectorConfig
from logger import setup_logger
from repositories.connection import DatabaseConnection
from repositories.resolution_attempt_repository import ResolutionAttemptRepository

logger = setup_logger(__name__)


def check_build_resolved_after(db_conn, component_name, application, after_ts):
    """Return the pipelinerun_name if this component's failure was resolved after after_ts.

    The StatusSynchronizer marks is_resolved=TRUE when KubeArchive shows a Succeeded
    build. If that happened after the PR merged, we attribute the fix to our PR.
    Returns None if not yet resolved.
    """
    with db_conn.connection() as conn, conn.cursor() as cur:
        cur.execute("""
                SELECT pipelinerun_name
                FROM build_failures
                WHERE component_name = %s
                  AND application = %s
                  AND is_resolved = TRUE
                  AND resolved_at > %s
                ORDER BY resolved_at DESC
                LIMIT 1
            """, (component_name, application, after_ts))
        row = cur.fetchone()
        return row[0] if row else None


def verify_one(attempt, github, db_conn, repo_obj, dry_run=False):
    """Check one pending attempt. Returns a short status string for logging."""
    pr_url = attempt['pr_url']
    pr_number = attempt['pr_number']
    component = attempt['component_name']
    application = attempt['application']
    attempt_id = attempt['id']

    # Parse owner/repo from PR URL (github.com/{owner}/{repo}/pull/{number})
    parts = pr_url.rstrip('/').split('/')
    if len(parts) < 7 or 'github.com' not in pr_url:
        logger.warning("Cannot parse repo from PR URL: %s", pr_url)
        return 'skip'

    owner = parts[-4]
    repo_name = parts[-3]

    pr = github.get_pull_request(owner, repo_name, int(pr_number))
    if pr is None:
        logger.warning("Could not fetch PR #%s from %s/%s", pr_number, owner, repo_name)
        return 'skip'

    state = pr.get('state', 'open')
    merged = pr.get('merged', False)
    merged_at_str = pr.get('merged_at')

    if state == 'open':
        logger.info("PR #%s still open — skipping", pr_number)
        return 'open'

    if not merged:
        # Closed without merging
        note = "PR #{} was closed without merging".format(pr_number)
        logger.info("%s — marking abandoned", note)
        if not dry_run:
            repo_obj.update_verification(
                attempt_id=attempt_id,
                pr_merged=False,
                pr_merged_at=None,
                result_pipelinerun_name=None,
                result_build_status=None,
                was_successful=False,
                verification_notes=note,
            )
        return 'abandoned'

    # PR was merged — parse merged_at timestamp
    try:
        merged_at = datetime.strptime(merged_at_str, '%Y-%m-%dT%H:%M:%SZ')
    except (TypeError, ValueError):
        merged_at = datetime.utcnow()

    # Check if the Konflux build succeeded after the merge
    pipelinerun_name = check_build_resolved_after(db_conn, component, application, merged_at)
    was_successful = pipelinerun_name is not None
    result_status = 'Succeeded' if was_successful else 'Pending'

    if was_successful:
        note = "PR merged at {}; Konflux build {} succeeded".format(
            merged_at_str, pipelinerun_name
        )
    else:
        note = "PR merged at {} but Konflux build not yet resolved — will recheck".format(
            merged_at_str
        )

    logger.info("PR #%s merged=%s build_ok=%s: %s", pr_number, merged, was_successful, note)

    if not dry_run:
        if was_successful:
            repo_obj.update_verification(
                attempt_id=attempt_id,
                pr_merged=True,
                pr_merged_at=merged_at,
                result_pipelinerun_name=pipelinerun_name,
                result_build_status=result_status,
                was_successful=True,
                verification_notes=note,
            )
        else:
            # PR merged but build not yet resolved — leave pr_merged NULL so we recheck next run.
            # Once build_failures.is_resolved flips, a future run will complete the record.
            logger.info("Will recheck on next cron run when build resolves")

    return 'success' if was_successful else 'merged-pending'


def main():
    parser = argparse.ArgumentParser(description='Verify CI fix resolution attempts')
    parser.add_argument('--dry-run', action='store_true', help='Print without writing to DB')
    args = parser.parse_args()

    config = CollectorConfig.from_env()

    if not config.github_token:
        print("Error: GITHUB_TOKEN not set — cannot check PR status", file=sys.stderr)
        return 1

    db_conn = DatabaseConnection(config.db)
    github = GitHubClient(token=config.github_token)
    from repositories.error_pattern_repository import ErrorPatternRepository
    pattern_repo = ErrorPatternRepository(db_conn)
    repo_obj = ResolutionAttemptRepository(db_conn, pattern_repo=pattern_repo)

    pending = repo_obj.get_pending_verification()
    if not pending:
        logger.info("No pending resolution attempts to verify")
        return 0

    logger.info("Verifying %d resolution attempt(s)%s",
                len(pending), ' (dry-run)' if args.dry_run else '')

    counts = {'open': 0, 'success': 0, 'merged-pending': 0, 'abandoned': 0, 'skip': 0}
    for attempt in pending:
        status = verify_one(attempt, github, db_conn, repo_obj, dry_run=args.dry_run)
        counts[status] = counts.get(status, 0) + 1

    logger.info(
        "Verification done: %d success, %d merged-pending, %d abandoned, %d open, %d skip",
        counts['success'], counts['merged-pending'],
        counts['abandoned'], counts['open'], counts['skip']
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
