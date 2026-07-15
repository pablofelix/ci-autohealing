-- Migration 014: Add issue_type and reference_urls to triage_items
--
-- Allows tracking multiple independent issues per component (e.g. build
-- failure + FIPS scan) by distinguishing them with issue_type.
-- Adds reference_urls for PRs, docs, and other non-Slack links.

ALTER TABLE triage_items
    ADD COLUMN IF NOT EXISTS issue_type VARCHAR(50) DEFAULT 'build'
        CHECK (issue_type IN ('build', 'conforma', 'onboarding', 'release'));

ALTER TABLE triage_items
    ADD COLUMN IF NOT EXISTS reference_urls TEXT[];

COMMENT ON COLUMN triage_items.issue_type IS
'Type of issue: build, conforma, onboarding, or release. Allows multiple triage items per component with different types.';

COMMENT ON COLUMN triage_items.reference_urls IS
'Non-Slack reference links (PRs, Jira URLs, docs). Use slack_thread_urls for Slack links only.';
