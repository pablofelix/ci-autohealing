-- Migration 013: Extra build metadata from Tekton Results
--
-- Adds fields for pipeline output results and task completion summary.
-- These come from PipelineRun status.results (IMAGE_URL, IMAGE_DIGEST, etc.)
-- and conditions[].message (task completion counts).

ALTER TABLE build_failures
    ADD COLUMN IF NOT EXISTS task_summary TEXT;

ALTER TABLE build_failures
    ADD COLUMN IF NOT EXISTS chains_git_url TEXT;

ALTER TABLE build_failures
    ADD COLUMN IF NOT EXISTS chains_git_commit VARCHAR(64);

COMMENT ON COLUMN build_failures.task_summary IS
'Task completion summary from conditions.message, e.g. "Tasks Completed: 20 (Failed: 1, Cancelled 0), Skipped: 3"';

COMMENT ON COLUMN build_failures.chains_git_url IS
'Verified source repository URL from Tekton Chains (CHAINS-GIT_URL pipeline result)';

COMMENT ON COLUMN build_failures.chains_git_commit IS
'Verified git commit SHA from Tekton Chains (CHAINS-GIT_COMMIT pipeline result)';
