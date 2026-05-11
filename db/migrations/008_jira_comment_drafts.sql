-- Migration 008: Jira comment draft tracking
--
-- Tracks which Jira comments have been seen and stores AI-drafted responses.
-- One row per (jira_key, comment_id) — comment_id is Jira's stable internal integer.
-- Comment text is NOT stored here; always fetched fresh from Jira API.
-- A row existing means the comment has been processed (watermark pattern).
--
-- Scope: only tickets linked via ic fix / ic jira create (i.e. jira_key in
-- build_failures or conforma_results). Other Jira tickets are out of scope.

CREATE TABLE IF NOT EXISTS jira_comment_drafts (
    id               SERIAL PRIMARY KEY,
    jira_key         VARCHAR(50)  NOT NULL,
    comment_id       BIGINT       NOT NULL,
    draft_response   TEXT         NOT NULL,
    notified_at      TIMESTAMP,              -- when notify-send was sent
    reviewed_at      TIMESTAMP,              -- when user marked it reviewed in ic jira inbox
    model_used       VARCHAR(100),
    tokens_used      INTEGER,
    created_at       TIMESTAMP    DEFAULT NOW(),
    UNIQUE (jira_key, comment_id)
);

CREATE INDEX IF NOT EXISTS idx_jcd_jira_key   ON jira_comment_drafts (jira_key);
CREATE INDEX IF NOT EXISTS idx_jcd_unreviewed ON jira_comment_drafts (created_at)
    WHERE reviewed_at IS NULL;
