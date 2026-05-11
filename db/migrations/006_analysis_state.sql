-- Migration 006: Explicit analysis state tracking
--
-- Adds ai_attempts and ai_skip_reason to both failure tables so the
-- analysis pipeline has a circuit breaker and can surface blocked rows
-- instead of silently skipping them.
--
-- ai_attempts: incremented before each LLM call; surviving retries are visible
-- ai_skip_reason: set when a row is permanently excluded from the analysis queue
--   values: 'no_logs'     -- logs never arrived after timeout (build only)
--           'max_retries' -- LLM call failed 3 times consecutively
--
-- Design note: kept as additive columns rather than a state enum so that adding
-- a parallel-worker locking column (e.g. ai_locked_by, ai_locked_at) in a future
-- migration is straightforward without changing existing logic.

ALTER TABLE build_failures
    ADD COLUMN IF NOT EXISTS ai_attempts    INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ai_skip_reason TEXT;

ALTER TABLE conforma_results
    ADD COLUMN IF NOT EXISTS ai_attempts    INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ai_skip_reason TEXT;

-- Recreate the pending index to also exclude skipped rows
DROP INDEX IF EXISTS idx_bf_ai_pending;
CREATE INDEX IF NOT EXISTS idx_bf_ai_pending
    ON build_failures(ai_analyzed, is_resolved)
    WHERE NOT ai_analyzed AND NOT is_resolved AND ai_skip_reason IS NULL;

CREATE INDEX IF NOT EXISTS idx_conforma_ai_pending
    ON conforma_results(ai_analyzed, is_resolved)
    WHERE NOT ai_analyzed AND NOT is_resolved AND ai_skip_reason IS NULL;
