-- Migration 019: Convert slack_thread_url to slack_thread_urls array
-- Supports multiple Slack thread URLs per triage item

ALTER TABLE triage_items ADD COLUMN IF NOT EXISTS slack_thread_urls TEXT[];

UPDATE triage_items
SET slack_thread_urls = ARRAY[slack_thread_url]
WHERE slack_thread_url IS NOT NULL
  AND slack_thread_urls IS NULL;

ALTER TABLE triage_items DROP COLUMN IF EXISTS slack_thread_url;
