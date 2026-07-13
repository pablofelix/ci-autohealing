-- 031_failure_nature.sql
-- Adds failure_nature column for smarter auto-resolution guards.
-- Values: 'structural', 'unknown', NULL (not yet classified).

ALTER TABLE build_failures
    ADD COLUMN IF NOT EXISTS failure_nature VARCHAR(20) DEFAULT NULL;
