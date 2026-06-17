-- Runtime configuration (API-managed, overrides env vars)
CREATE TABLE IF NOT EXISTS runtime_config (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW(),
    updated_by VARCHAR(100) DEFAULT 'system'
);

-- Seed with empty watched_applications (falls back to env var)
INSERT INTO runtime_config (key, value, updated_by)
VALUES ('watched_applications', '', 'migration')
ON CONFLICT (key) DO NOTHING;
