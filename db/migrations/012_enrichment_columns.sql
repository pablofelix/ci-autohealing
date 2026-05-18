-- Migration 012: Context enrichment infrastructure
--
-- Adds columns and indexes to support multi-source context enrichment
-- for build failures and Conforma violations.
--
-- Design: Separate enriched_context from commit_context to distinguish:
-- - commit_context: Raw data from GitHub (commit diff, Dockerfile, .tekton/)
-- - enriched_context: Derived data (dependency changes, related failures, policy context)

-- ============================================================================
-- 1. Add enriched_context column to build_failures
-- ============================================================================

ALTER TABLE build_failures
    ADD COLUMN IF NOT EXISTS enriched_context JSONB;

ALTER TABLE build_failures
    ADD COLUMN IF NOT EXISTS enrichment_attempts INTEGER DEFAULT 0 NOT NULL;

ALTER TABLE build_failures
    ADD COLUMN IF NOT EXISTS enrichment_error TEXT;

COMMENT ON COLUMN build_failures.enriched_context IS
'Enriched context from multiple sources (JSON structure):
{
  "sources": {
    "dependency_changes": true,
    "related_failures": true,
    "enriched_at": "2026-05-18T10:30:00Z"
  },
  "dependency_changes": {
    "requirements.txt": {"before": "...", "after": "...", "diff": "..."},
    "package.json": {"before": "...", "after": "...", "diff": "..."}
  },
  "related_failures": [
    {
      "id": 123,
      "component_name": "...",
      "error_type": "...",
      "similarity_score": 0.85,
      "root_cause": "..."
    }
  ]
}';

COMMENT ON COLUMN build_failures.enrichment_attempts IS
'Number of times enrichment was attempted (for circuit-breaking)';

COMMENT ON COLUMN build_failures.enrichment_error IS
'Last enrichment error message (NULL if no error or not attempted)';

-- ============================================================================
-- 2. Add enriched_context column to conforma_results
-- ============================================================================

ALTER TABLE conforma_results
    ADD COLUMN IF NOT EXISTS enriched_context JSONB;

ALTER TABLE conforma_results
    ADD COLUMN IF NOT EXISTS enrichment_attempts INTEGER DEFAULT 0 NOT NULL;

ALTER TABLE conforma_results
    ADD COLUMN IF NOT EXISTS enrichment_error TEXT;

COMMENT ON COLUMN conforma_results.enriched_context IS
'Enriched context from multiple sources (similar structure to build_failures)';

-- ============================================================================
-- 3. Indexes for efficient enrichment queries
-- ============================================================================

-- Find pending enrichments (has commit_sha but no enriched_context)
CREATE INDEX IF NOT EXISTS idx_bf_pending_enrichment
    ON build_failures(application, first_detected_at DESC)
    WHERE commit_sha IS NOT NULL
      AND enriched_context IS NULL
      AND enrichment_error IS NULL
      AND ai_analyzed = FALSE
      AND is_resolved = FALSE;

-- Find related failures efficiently (for RelatedFailuresSource)
CREATE INDEX IF NOT EXISTS idx_bf_related_lookup
    ON build_failures(component_name, error_type, first_detected_at DESC)
    WHERE is_resolved = FALSE;

-- ============================================================================
-- 4. Update error_patterns table for pattern reuse tracking
-- ============================================================================

ALTER TABLE error_patterns
    ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMP;

ALTER TABLE error_patterns
    ADD COLUMN IF NOT EXISTS match_count INTEGER DEFAULT 0 NOT NULL;

COMMENT ON COLUMN error_patterns.last_used_at IS
'Last time this pattern was matched to a failure in AI analysis';

COMMENT ON COLUMN error_patterns.match_count IS
'Number of times this pattern was successfully matched and applied';

-- ============================================================================
-- 5. Verify migration
-- ============================================================================

-- Show new columns
SELECT
    table_name,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('build_failures', 'conforma_results', 'error_patterns')
  AND column_name IN ('enriched_context', 'enrichment_attempts', 'enrichment_error', 'last_used_at', 'match_count')
ORDER BY table_name, ordinal_position;

-- Show new indexes
SELECT
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname LIKE '%enrichment%'
ORDER BY tablename, indexname;
