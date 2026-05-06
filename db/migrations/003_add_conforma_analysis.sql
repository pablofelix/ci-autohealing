-- Migration 003: Add Conforma support to AI analysis
-- Allows ai_analysis to reference either build_failures OR conforma_results

-- Step 1: Add conforma_result_id column
ALTER TABLE ai_analysis
ADD COLUMN conforma_result_id INTEGER REFERENCES conforma_results(id) ON DELETE CASCADE;

-- Step 2: Make build_failure_id nullable (was NOT NULL implicitly)
ALTER TABLE ai_analysis
ALTER COLUMN build_failure_id DROP NOT NULL;

-- Step 3: Add constraint that exactly one ID must be set
ALTER TABLE ai_analysis
ADD CONSTRAINT ai_analysis_result_type_check
CHECK (
    (build_failure_id IS NOT NULL AND conforma_result_id IS NULL)
    OR
    (build_failure_id IS NULL AND conforma_result_id IS NOT NULL)
);

-- Step 4: Create index on conforma_result_id
CREATE INDEX IF NOT EXISTS idx_ai_conforma_result ON ai_analysis(conforma_result_id);

-- Step 5: Add helper view to easily query analysis type
CREATE OR REPLACE VIEW ai_analysis_with_type AS
SELECT
    a.*,
    CASE
        WHEN a.build_failure_id IS NOT NULL THEN 'build_failure'
        WHEN a.conforma_result_id IS NOT NULL THEN 'conforma_violation'
        ELSE 'unknown'
    END as result_type,
    COALESCE(
        (SELECT component_name FROM build_failures WHERE id = a.build_failure_id),
        (SELECT component_name FROM conforma_results WHERE id = a.conforma_result_id)
    ) as component_name
FROM ai_analysis a;

COMMENT ON VIEW ai_analysis_with_type IS 'AI analysis with computed result_type and component_name for easier querying';
