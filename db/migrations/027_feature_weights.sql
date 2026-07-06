-- Feature weights for evidence-based confidence scoring
CREATE TABLE IF NOT EXISTS feature_weights (
    feature_name VARCHAR(100) PRIMARY KEY,
    weight FLOAT NOT NULL DEFAULT 1.0,
    sample_size INTEGER DEFAULT 0,
    last_updated TIMESTAMP DEFAULT NOW()
);

INSERT INTO feature_weights (feature_name, weight) VALUES
    ('has_build_history', 1.0),
    ('has_pipeline_logs', 1.0),
    ('has_commit_diff', 1.0),
    ('sha_mismatch_confirmed', 1.0),
    ('pattern_matched', 1.0),
    ('nudging_checked', 1.0),
    ('has_open_prs', 1.0),
    ('error_in_recent_builds', 1.0),
    ('multiple_violations', 1.0),
    ('has_triage_context', 1.0)
ON CONFLICT DO NOTHING;
