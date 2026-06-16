-- Link error patterns to skills for auto-execution
ALTER TABLE error_patterns ADD COLUMN IF NOT EXISTS skill_name VARCHAR(255);
COMMENT ON COLUMN error_patterns.skill_name IS 'Qualified skill name to auto-execute when this pattern matches (e.g. aiops-infra/fix-hermetic)';
