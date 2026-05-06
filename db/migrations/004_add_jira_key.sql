-- Migration 004: Add jira_key to conforma_results and build_failures
-- Allows linking violations/failures to Jira tickets

ALTER TABLE conforma_results ADD COLUMN IF NOT EXISTS jira_key VARCHAR(50);
ALTER TABLE build_failures ADD COLUMN IF NOT EXISTS jira_key VARCHAR(50);

CREATE INDEX IF NOT EXISTS idx_conforma_jira ON conforma_results(jira_key) WHERE jira_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bf_jira ON build_failures(jira_key) WHERE jira_key IS NOT NULL;
