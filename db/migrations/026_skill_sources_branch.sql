-- Add branch support to skill sources.
-- Allows cloning skills from non-default git branches.
ALTER TABLE skill_sources ADD COLUMN IF NOT EXISTS branch VARCHAR(100);
