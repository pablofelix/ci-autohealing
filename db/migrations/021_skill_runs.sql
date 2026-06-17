-- Skill execution history
CREATE TABLE IF NOT EXISTS skill_runs (
    id SERIAL PRIMARY KEY,
    skill_name VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL,
    exit_code INTEGER,
    risk_level VARCHAR(20),
    duration_seconds NUMERIC(8,2),
    stdout TEXT,
    stderr TEXT,
    params JSONB,
    triggered_by VARCHAR(50) DEFAULT 'cli',
    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_skill_runs_name ON skill_runs(skill_name);
CREATE INDEX IF NOT EXISTS idx_skill_runs_status ON skill_runs(status);
CREATE INDEX IF NOT EXISTS idx_skill_runs_started ON skill_runs(started_at DESC);
