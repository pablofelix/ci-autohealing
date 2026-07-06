-- Human verdict on AI analysis accuracy
ALTER TABLE ai_analysis
    ADD COLUMN IF NOT EXISTS human_verdict VARCHAR(20),
    ADD COLUMN IF NOT EXISTS human_verdict_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS human_verdict_by VARCHAR(100),
    ADD COLUMN IF NOT EXISTS actual_root_cause TEXT;

CREATE INDEX IF NOT EXISTS idx_ai_analysis_verdict ON ai_analysis(human_verdict)
    WHERE human_verdict IS NOT NULL;
