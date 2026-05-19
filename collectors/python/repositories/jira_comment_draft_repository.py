"""Repository for jira_comment_drafts table.

Tracks which Jira comments have been processed and stores AI-drafted replies.
One row per (jira_key, comment_id). Comment text is never stored here —
always fetched fresh from the Jira API when displaying.
"""



class JiraCommentDraftRepository:
    """SQL operations on the jira_comment_drafts table."""

    def __init__(self, db):
        self._db = db

    def get_existing_comment_ids(self, jira_key):
        # type: (str,) -> Set[int]
        """Return set of comment_ids already processed for this jira_key."""
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT comment_id FROM jira_comment_drafts WHERE jira_key = %s",
                    (jira_key,),
                )
                return {row[0] for row in cur.fetchall()}

    def insert_draft(self, jira_key, comment_id, draft_response,
                     model_used=None, tokens_used=None):
        # type: (str, int, str, ...) -> bool
        """Insert a new comment draft. Returns True if inserted, False if already exists."""
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO jira_comment_drafts
                        (jira_key, comment_id, draft_response, model_used, tokens_used)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (jira_key, comment_id) DO NOTHING
                    RETURNING id
                """, (jira_key, comment_id, draft_response, model_used, tokens_used))
                return cur.fetchone() is not None

    def mark_notified(self, jira_key, comment_id):
        # type: (str, int) -> None
        """Record when notify-send was called for this comment."""
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE jira_comment_drafts SET notified_at = NOW()
                    WHERE jira_key = %s AND comment_id = %s AND notified_at IS NULL
                """, (jira_key, comment_id))

    def mark_reviewed(self, jira_key, comment_id):
        # type: (str, int) -> None
        """Mark a comment as reviewed by the user in ic jira inbox."""
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE jira_comment_drafts SET reviewed_at = NOW()
                    WHERE jira_key = %s AND comment_id = %s
                """, (jira_key, comment_id))

    def get_unreviewed(self):
        # type: () -> List[Dict[str, Any]]
        """Return all unreviewed drafts ordered oldest-first."""
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT jira_key, comment_id, draft_response,
                           TO_CHAR(created_at, 'DD Mon HH24:MI') AS received_at
                    FROM jira_comment_drafts
                    WHERE reviewed_at IS NULL
                    ORDER BY created_at ASC
                """)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    def count_unreviewed(self):
        # type: () -> int
        """Return total count of unreviewed drafts."""
        with self._db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM jira_comment_drafts WHERE reviewed_at IS NULL"
                )
                return cur.fetchone()[0]
