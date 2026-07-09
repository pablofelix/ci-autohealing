-- Migration 029: ML Training Labels
--
-- Stores inferred failure_category labels for resolved build failures,
-- derived from the GitHub PR that fixed each failure. Used as ground truth
-- for supervised ML training. Kept separate from ai_analysis so label quality
-- is independently trackable and the table can be rebuilt without touching
-- AI analysis records.
--
-- label_source:     human-readable description of which files drove the label
-- label_confidence: 0.0–1.0; follows rules in collectors/verdict_correlator.py
-- pr_files_json:    snapshot of PR file list at label time (for audit/replay)
-- inferred_by:      'auto' (file-pattern rules) or 'human' (manual correction)

CREATE TABLE IF NOT EXISTS ml_training_labels (
    id                    SERIAL PRIMARY KEY,
    build_failure_id      INTEGER NOT NULL REFERENCES build_failures(id) ON DELETE CASCADE,
    resolution_commit_sha VARCHAR(40),
    pr_number             INTEGER,
    pr_url                TEXT,
    failure_category      VARCHAR(100) NOT NULL,
    label_confidence      FLOAT NOT NULL CHECK (label_confidence BETWEEN 0.0 AND 1.0),
    label_source          TEXT NOT NULL,
    pr_files_json         JSONB,
    inferred_at           TIMESTAMP NOT NULL DEFAULT NOW(),
    inferred_by           VARCHAR(50) NOT NULL DEFAULT 'auto',
    CONSTRAINT uq_ml_labels_failure UNIQUE (build_failure_id)
);

CREATE INDEX IF NOT EXISTS idx_ml_labels_category
    ON ml_training_labels(failure_category);

CREATE INDEX IF NOT EXISTS idx_ml_labels_confidence
    ON ml_training_labels(label_confidence);

CREATE INDEX IF NOT EXISTS idx_ml_labels_inferred_at
    ON ml_training_labels(inferred_at DESC);
