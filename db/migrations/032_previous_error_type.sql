-- Gap #17: Track error_type changes between syncs.
-- Stores the previous error_type when it changes during an upsert,
-- so triagers can see when a failure was reclassified.
ALTER TABLE build_failures ADD COLUMN IF NOT EXISTS previous_error_type TEXT;
