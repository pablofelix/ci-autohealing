-- Add triage_snapshots table for daily delta tracking
CREATE TABLE IF NOT EXISTS triage_snapshots (
    id SERIAL PRIMARY KEY,
    application VARCHAR(255) NOT NULL,
    failing INTEGER NOT NULL DEFAULT 0,
    working INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    snapshot_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (application, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_triage_snapshots_app_date
    ON triage_snapshots (application, snapshot_date DESC);
