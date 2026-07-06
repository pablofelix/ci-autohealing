-- Link skill_runs to components and triage items, track outcome
ALTER TABLE skill_runs
    ADD COLUMN IF NOT EXISTS component_name VARCHAR(255),
    ADD COLUMN IF NOT EXISTS application VARCHAR(255),
    ADD COLUMN IF NOT EXISTS triage_item_id INTEGER REFERENCES triage_items(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS outcome VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_skill_runs_component ON skill_runs(component_name)
    WHERE component_name IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_skill_runs_triage ON skill_runs(triage_item_id)
    WHERE triage_item_id IS NOT NULL;
