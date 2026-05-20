-- Migration 011: Add is_future flag to conforma_results
-- Separates future-policy scenarios (informational) from current-policy scenarios (gate-blocking)

-- Add is_future column
ALTER TABLE conforma_results
ADD COLUMN is_future BOOLEAN DEFAULT FALSE NOT NULL;

-- Create index for filtering by future status
CREATE INDEX IF NOT EXISTS idx_conforma_is_future
ON conforma_results(is_future);

-- Create composite index for common queries (unresolved non-future violations)
CREATE INDEX IF NOT EXISTS idx_conforma_blocking_violations
ON conforma_results(application, is_future, is_resolved)
WHERE is_resolved = FALSE AND is_future = FALSE;

COMMENT ON COLUMN conforma_results.is_future IS
'TRUE if scenario uses future EC policy (informational preview), FALSE if current policy (gate-blocking)';
