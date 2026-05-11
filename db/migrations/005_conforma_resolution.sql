-- Migration 005: Conforma resolution tracking
-- Extends resolution_attempts to track conforma PR fixes (parallel to build fixes).
-- Also documents the conforma_results AI columns that were applied manually.

-- Step 1: Add AI tracking columns to conforma_results (idempotent — already exist in live DB)
ALTER TABLE conforma_results
    ADD COLUMN IF NOT EXISTS ai_analyzed BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS ai_analysis_id INTEGER REFERENCES ai_analysis(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS ai_fix_attempted BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS ai_fix_successful BOOLEAN DEFAULT FALSE;

-- Step 2: Make build_failure_id nullable on resolution_attempts
-- (conforma resolution rows will have conforma_result_id instead)
ALTER TABLE resolution_attempts
    ALTER COLUMN build_failure_id DROP NOT NULL;

-- Step 3: Add conforma_result_id FK (mirrors build_failure_id pattern)
ALTER TABLE resolution_attempts
    ADD COLUMN IF NOT EXISTS conforma_result_id INTEGER REFERENCES conforma_results(id) ON DELETE CASCADE;

-- Step 4: Exactly one FK must be set per row
ALTER TABLE resolution_attempts
    ADD CONSTRAINT chk_resolution_one_failure_type
    CHECK (
        (build_failure_id IS NOT NULL)::int +
        (conforma_result_id IS NOT NULL)::int = 1
    );

-- Step 5: Index for conforma lookup
CREATE INDEX IF NOT EXISTS idx_ra_conforma ON resolution_attempts(conforma_result_id);
