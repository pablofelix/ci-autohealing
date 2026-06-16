-- Add trigger_type to build_failures for nightly detection
ALTER TABLE build_failures ADD COLUMN IF NOT EXISTS trigger_type VARCHAR(20) DEFAULT 'push';
CREATE INDEX IF NOT EXISTS idx_bf_trigger_type ON build_failures(trigger_type);

-- Backfill existing nightly builds based on commit author/message
UPDATE build_failures
SET trigger_type = 'nightly'
WHERE commit_author = 'Openshift-AI DevOps'
  AND commit_message LIKE '%Updating the operator repo with latest images and manifests%'
  AND trigger_type = 'push';

-- Tag PR-triggered builds
UPDATE build_failures
SET trigger_type = 'pull_request'
WHERE pr_number IS NOT NULL
  AND trigger_type = 'push';
