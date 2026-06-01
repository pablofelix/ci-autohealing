-- Migration 017: Blob storage references
--
-- Adds blob_refs JSONB column to build_failures and conforma_results.
-- When large data (logs, context, violation details) is stored externally
-- in MinIO/S3 or local filesystem, this column holds the storage keys.
-- The original TEXT/JSONB columns are set to NULL after offloading.

ALTER TABLE build_failures
    ADD COLUMN IF NOT EXISTS blob_refs JSONB DEFAULT '{}';

ALTER TABLE conforma_results
    ADD COLUMN IF NOT EXISTS blob_refs JSONB DEFAULT '{}';

COMMENT ON COLUMN build_failures.blob_refs IS
'References to externally stored blobs. Keys: build_logs, commit_context. Values: storage keys.';

COMMENT ON COLUMN conforma_results.blob_refs IS
'References to externally stored blobs. Keys: violation_details. Values: storage keys.';
