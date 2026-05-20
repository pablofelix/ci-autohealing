#!/usr/bin/env python3.11
"""Autonomous fix runner for conforma violations.

Called from cron step 7.5 when AUTONOMOUS_MODE=true. Queries for conforma
violations where can_auto_fix=TRUE, confidence >= threshold, and no fix has
been attempted yet, then dispatches to the appropriate deterministic fixer.

Safety gates (all must pass before a PR is created):
  - can_auto_fix = TRUE
  - requires_human_review = FALSE
  - confidence_score >= AUTO_FIX_MIN_CONFIDENCE (default 0.95)
  - ai_fix_attempted = FALSE
  - No open ci-autohealing branch for this violation on GitHub

Usage (from cron):
  python3.11 fixers/auto_fix.py

Env vars:
  AUTONOMOUS_MODE          Set to "true" to enable (required)
  AUTO_FIX_MAX_PER_RUN     Max PRs per cron run (default 3)
  AUTO_FIX_MIN_CONFIDENCE  Min confidence score (default 0.95)
  GITHUB_TOKEN             Required
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clients.github_client import GitHubClient, parse_github_repo
from config import CollectorConfig
from fixers.fix_generator import (
    conforma_branch_name,
    run_conforma_hermetic_mode,
    run_conforma_pr_mode,
    run_conforma_rpm_repo_id_mode,
    run_conforma_sbom_vendor_label_mode,
    run_conforma_unpinned_task_mode,
    run_conforma_untrusted_image_mode,
)
from logger import setup_logger
from repositories.connection import DatabaseConnection

logger = setup_logger(__name__)

_FIXER_BY_CATEGORY = {
    'policy_hermetic_build':   run_conforma_hermetic_mode,
    'policy_sbom_vendor_label': run_conforma_sbom_vendor_label_mode,
    'policy_rpm_repository':   run_conforma_rpm_repo_id_mode,
    'policy_unpinned_task':    run_conforma_unpinned_task_mode,
    'policy_untrusted_image':  run_conforma_untrusted_image_mode,
    'policy_deprecated_task':  run_conforma_pr_mode,
}


def _get_candidates(db_conn, min_confidence, max_count):
    # type: (DatabaseConnection, float, int) -> list
    """Query for conforma violations eligible for autonomous fixing."""
    with db_conn.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.id, c.component_name,
                       COALESCE(c.application, 'acme-v2-0') AS application,
                       c.repository_url,
                       a.failure_category, a.confidence_score
                FROM conforma_results c
                JOIN (
                    SELECT DISTINCT ON (conforma_result_id)
                        conforma_result_id, failure_category, confidence_score,
                        can_auto_fix, requires_human_review
                    FROM ai_analysis
                    ORDER BY conforma_result_id, created_at DESC
                ) a ON a.conforma_result_id = c.id
                WHERE c.is_resolved = FALSE
                  AND c.ai_fix_attempted = FALSE
                  AND a.can_auto_fix = TRUE
                  AND a.requires_human_review = FALSE
                  AND a.confidence_score >= %s
                  AND a.failure_category = ANY(%s)
                ORDER BY a.confidence_score DESC, c.first_detected_at ASC
                LIMIT %s
            """, (
                min_confidence,
                list(_FIXER_BY_CATEGORY.keys()),
                max_count,
            ))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def _branch_exists(github, repo_url, branch):
    # type: (GitHubClient, str, str) -> bool
    """Return True if the named branch already exists in the GitHub repo."""
    parsed = parse_github_repo(repo_url)
    if not parsed:
        return False
    owner, repo = parsed
    try:
        sha = github.get_ref_sha(owner, repo, branch)
        return bool(sha)
    except Exception as exc:
        logger.warning("Could not check branch %s — treating as absent: %s", branch, exc)
        return False


def main():
    # type: () -> int
    if os.environ.get('AUTONOMOUS_MODE', '').lower() != 'true':
        logger.error("AUTONOMOUS_MODE is not set to 'true' — refusing to run")
        return 1

    config = CollectorConfig.from_env()

    if not config.github_token:
        logger.error("GITHUB_TOKEN not set — autonomous mode requires GitHub access")
        return 1

    db_conn = DatabaseConnection(config.db)
    github = GitHubClient(token=config.github_token)

    candidates = _get_candidates(
        db_conn,
        config.auto_fix_min_confidence,
        config.auto_fix_max_per_run,
    )

    if not candidates:
        logger.info("No auto-fixable conforma violations found (min_confidence=%.2f)",
                    config.auto_fix_min_confidence)
        return 0

    logger.info("Found %d auto-fixable candidate(s) (budget=%d)",
                len(candidates), config.auto_fix_max_per_run)

    fixed = 0
    for candidate in candidates:
        conforma_id = candidate['id']
        component = candidate['component_name']
        application = candidate['application']
        repo_url = candidate['repository_url'] or ''
        category = candidate['failure_category']
        confidence = candidate['confidence_score']

        # Idempotency guard: skip if a fix branch already exists on GitHub
        branch = conforma_branch_name(component, conforma_id)
        if _branch_exists(github, repo_url, branch):
            logger.info("Skipping %s — branch %s already exists", component, branch)
            continue

        fixer_fn = _FIXER_BY_CATEGORY.get(category)
        if not fixer_fn:
            logger.warning("No fixer registered for category %s — skipping %s",
                           category, component)
            continue

        logger.info("Autonomous fix: %s (category=%s, confidence=%.2f, id=%d)",
                    component, category, confidence, conforma_id)
        try:
            exit_code = fixer_fn(
                config, component, conforma_id, application,
                execute=True,
                attempted_by='autonomous',
            )
            if exit_code == 0:
                fixed += 1
                logger.info("PR created for %s (conforma_id=%d)", component, conforma_id)
            else:
                logger.warning("Fixer returned non-zero (%d) for %s", exit_code, component)
        except Exception:
            logger.exception("Fixer crashed for %s (conforma_id=%d)", component, conforma_id)

    logger.info("Autonomous mode complete: %d/%d fix(es) applied", fixed, len(candidates))
    return 0


if __name__ == '__main__':
    sys.exit(main())
