#!/usr/bin/env python3
"""Poll active Jira tickets for new comments and generate AI draft replies.

Run by cron every 20 minutes. For each active (is_resolved=FALSE) Jira ticket
linked in build_failures or conforma_results:
  1. Fetch comments from Jira API
  2. Skip already-processed comment_ids (watermark pattern)
  3. Skip comments posted by the bot account (prevent reply loops)
  4. Generate an AI draft reply with full failure context
  5. Store draft in jira_comment_drafts
  6. Send desktop notification via notify-send

Requires: JIRA_EMAIL + JIRA_TOKEN (Jira access) + LLM_PROVIDER (AI drafts).
All three must be set; the cron script guards on these before calling this script.
"""

import subprocess
import sys
import time

from config import CollectorConfig
from clients.jira_client import JiraClient
from clients.llm_provider import create_llm_provider
from logger import setup_logger
from prompt_loader import load_prompt
from repositories import DatabaseConnection, JiraCommentDraftRepository

logger = setup_logger(__name__)

SYSTEM_PROMPT = load_prompt('jira_reply_drafter')
MAX_LOG_CHARS = 20000


class JiraCommentPoller:
    """Poll Jira tickets for new comments and generate AI draft replies."""

    def __init__(self, config, db=None, draft_repo=None, jira=None, llm=None):
        # type: (CollectorConfig, ...) -> None
        if db is None:
            db = DatabaseConnection(config.db)
        self._db = db
        self._config = config
        self._draft_repo = draft_repo or JiraCommentDraftRepository(db)

        if jira is None:
            if not config.jira:
                raise ValueError("Jira not configured. Set JIRA_EMAIL and JIRA_TOKEN.")
            jira = JiraClient(
                base_url=config.jira.base_url,
                email=config.jira.email,
                token=config.jira.token,
                project=config.jira.project,
            )
        self._jira = jira

        if llm is None:
            if not config.llm:
                raise ValueError("LLM not configured. Set LLM_PROVIDER.")
            llm = create_llm_provider(config.llm)
        self._llm = llm

    def get_active_jira_keys(self):
        # type: () -> List[str]
        """Return distinct jira_keys from unresolved build and conforma failures."""
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT jira_key FROM build_failures
                    WHERE is_resolved = FALSE AND jira_key IS NOT NULL
                    UNION
                    SELECT DISTINCT jira_key FROM conforma_results
                    WHERE is_resolved = FALSE AND jira_key IS NOT NULL
                    ORDER BY jira_key
                """)
                return [row[0] for row in cur.fetchall()]

    def get_failure_context(self, jira_key):
        # type: (str,) -> Optional[Dict[str, Any]]
        """Fetch failure details and AI analysis for the given jira_key.

        Tries build_failures first, then conforma_results.
        Returns a dict with keys: failure_type, component_name, error_summary,
        ai_root_cause, ai_recommended_fix, ai_category.
        """
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                # Try build failure first
                cur.execute("""
                    SELECT bf.component_name, bf.error_type, bf.error_message,
                           bf.failed_task_name, bf.failed_step_name,
                           bf.build_logs, bf.commit_sha, bf.commit_author,
                           a.root_cause, a.recommended_fix, a.failure_category
                    FROM build_failures bf
                    LEFT JOIN ai_analysis a ON a.build_failure_id = bf.id
                    WHERE bf.jira_key = %s AND bf.is_resolved = FALSE
                    ORDER BY a.analyzed_at DESC NULLS LAST
                    LIMIT 1
                """, (jira_key,))
                row = cur.fetchone()
                if row:
                    logs = row[5] or ''
                    if len(logs) > MAX_LOG_CHARS:
                        logs = logs[-MAX_LOG_CHARS:]
                    return {
                        'failure_type': 'build',
                        'component_name': row[0],
                        'error_type': row[1],
                        'error_message': row[2],
                        'failed_task': row[3],
                        'failed_step': row[4],
                        'build_logs': logs,
                        'commit_sha': row[6],
                        'commit_author': row[7],
                        'ai_root_cause': row[8],
                        'ai_recommended_fix': row[9],
                        'ai_category': row[10],
                    }

                # Try conforma
                cur.execute("""
                    SELECT cr.component_name, cr.scenario,
                           cr.violations_count, cr.warnings_count,
                           cr.violation_summary, cr.commit_sha,
                           a.root_cause, a.recommended_fix, a.failure_category
                    FROM conforma_results cr
                    LEFT JOIN ai_analysis a ON a.conforma_result_id = cr.id
                    WHERE cr.jira_key = %s AND cr.is_resolved = FALSE
                    ORDER BY a.analyzed_at DESC NULLS LAST
                    LIMIT 1
                """, (jira_key,))
                row = cur.fetchone()
                if row:
                    return {
                        'failure_type': 'conforma',
                        'component_name': row[0],
                        'scenario': row[1],
                        'violations_count': row[2],
                        'warnings_count': row[3],
                        'violation_summary': row[4],
                        'commit_sha': row[5],
                        'ai_root_cause': row[6],
                        'ai_recommended_fix': row[7],
                        'ai_category': row[8],
                    }

        return None

    def build_user_prompt(self, comment, failure_context, jira_key):
        # type: (Dict[str, Any], Optional[Dict[str, Any]], str) -> str
        """Build the user prompt for the reply drafter LLM."""
        author = (comment.get('author') or {}).get('displayName', 'Unknown')
        body = comment.get('body', '').strip()

        sections = [
            "## Jira Ticket\nKey: {}".format(jira_key),
            "## Comment (from {})\n{}".format(author, body),
        ]

        if failure_context:
            ft = failure_context['failure_type']
            component = failure_context.get('component_name', 'unknown')

            if ft == 'build':
                ctx = (
                    "## Failure Context (Build Failure)\n"
                    "Component: {component}\n"
                    "Error type: {error_type}\n"
                    "Error message: {error_message}\n"
                    "Failed task: {failed_task}\n"
                    "Failed step: {failed_step}\n"
                    "Commit: {commit_sha} by {commit_author}"
                ).format(
                    component=component,
                    error_type=failure_context.get('error_type', 'unknown'),
                    error_message=(failure_context.get('error_message') or '')[:500],
                    failed_task=failure_context.get('failed_task', 'unknown'),
                    failed_step=failure_context.get('failed_step', 'unknown'),
                    commit_sha=(failure_context.get('commit_sha') or 'unknown')[:12],
                    commit_author=failure_context.get('commit_author', 'unknown'),
                )
                sections.append(ctx)

                logs = failure_context.get('build_logs', '')
                if logs:
                    sections.append("## Build Logs (tail)\n```\n{}\n```".format(logs[-5000:]))

            else:  # conforma
                ctx = (
                    "## Failure Context (Conforma Violation)\n"
                    "Component: {component}\n"
                    "Scenario: {scenario}\n"
                    "Violations: {violations_count} | Warnings: {warnings_count}\n"
                    "Summary: {summary}\n"
                    "Commit: {commit_sha}"
                ).format(
                    component=component,
                    scenario=failure_context.get('scenario', 'unknown'),
                    violations_count=failure_context.get('violations_count', 0),
                    warnings_count=failure_context.get('warnings_count', 0),
                    summary=(failure_context.get('violation_summary') or '')[:1000],
                    commit_sha=(failure_context.get('commit_sha') or 'unknown')[:12],
                )
                sections.append(ctx)

            if failure_context.get('ai_root_cause'):
                sections.append(
                    "## AI Root Cause Analysis\n{}".format(failure_context['ai_root_cause'][:2000])
                )
            if failure_context.get('ai_recommended_fix'):
                sections.append(
                    "## AI Recommended Fix\n{}".format(failure_context['ai_recommended_fix'][:1000])
                )

        sections.append(
            "Draft a concise, professional reply to this comment. "
            "Keep it under 200 words. Plain text only."
        )

        return "\n\n".join(sections)

    def _notify(self, jira_key, author, jira_url):
        # type: (str, str, str) -> None
        """Send a desktop notification via notify-send."""
        summary = "Jira reply drafted: {}".format(jira_key)
        body = "New comment from {}. Draft ready in `ic jira inbox`.".format(author)
        try:
            subprocess.run(
                ['notify-send', '--urgency=normal', '--expire-time=10000', summary, body],
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.debug("notify-send not available or timed out")

    def run(self):
        # type: () -> Dict[str, int]
        """Poll all active Jira tickets and draft replies for new comments."""
        start = time.time()
        stats = {'tickets': 0, 'new_comments': 0, 'drafted': 0, 'errors': 0}

        bot_email = self._config.jira.email if self._config.jira else ''
        jira_keys = self.get_active_jira_keys()
        stats['tickets'] = len(jira_keys)
        logger.info("Polling %d active Jira tickets", len(jira_keys))

        for jira_key in jira_keys:
            try:
                existing_ids = self._draft_repo.get_existing_comment_ids(jira_key)
                comments = self._jira.get_comments(jira_key)

                new_comments = [
                    c for c in comments
                    if int(c['id']) not in existing_ids
                    and (c.get('author') or {}).get('emailAddress', '') != bot_email
                ]

                if not new_comments:
                    continue

                stats['new_comments'] += len(new_comments)
                failure_context = self.get_failure_context(jira_key)
                jira_url = '{}/browse/{}'.format(
                    self._config.jira.base_url if self._config.jira else '',
                    jira_key
                )

                for comment in new_comments:
                    comment_id = int(comment['id'])
                    author = (comment.get('author') or {}).get('displayName', 'Unknown')
                    logger.info("%s: new comment %d from %s", jira_key, comment_id, author)

                    try:
                        user_prompt = self.build_user_prompt(comment, failure_context, jira_key)
                        resp = self._llm.create_message(
                            system=SYSTEM_PROMPT,
                            user_content=user_prompt,
                            tools=None,
                            max_tokens=512,
                        )
                        draft = resp.content.strip()
                        inserted = self._draft_repo.insert_draft(
                            jira_key=jira_key,
                            comment_id=comment_id,
                            draft_response=draft,
                            model_used=resp.model,
                            tokens_used=resp.input_tokens + resp.output_tokens,
                        )
                        if inserted:
                            stats['drafted'] += 1
                            self._notify(jira_key, author, jira_url)
                            logger.info("%s: draft stored for comment %d", jira_key, comment_id)
                        else:
                            logger.debug("%s: comment %d already in DB (race)", jira_key, comment_id)

                    except Exception as e:
                        logger.error("%s: failed to draft reply for comment %d: %s",
                                     jira_key, comment_id, e)
                        stats['errors'] += 1

            except Exception as e:
                logger.error("%s: polling error: %s", jira_key, e)
                stats['errors'] += 1

        duration = time.time() - start
        logger.info(
            "Poll complete: %d tickets, %d new comments, %d drafted, %d errors in %.1fs",
            stats['tickets'], stats['new_comments'], stats['drafted'], stats['errors'], duration,
        )
        return stats


def main():
    """Entry point: poll Jira tickets for new comments."""
    try:
        config = CollectorConfig.from_env()

        if not config.jira:
            logger.error("Jira not configured. Set JIRA_EMAIL and JIRA_TOKEN.")
            sys.exit(1)

        if not config.llm:
            logger.error("LLM not configured. Set LLM_PROVIDER.")
            sys.exit(1)

        poller = JiraCommentPoller(config)
        stats = poller.run()

        if stats['errors'] > 0 and stats['drafted'] == 0:
            sys.exit(1)

    except Exception as e:
        logger.error("Jira comment polling failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
