-- Triage tracking: persistent record of build failure investigations,
-- grouping, Slack/Jira links, and resolution status.

CREATE TABLE IF NOT EXISTS triage_items (
    id              SERIAL PRIMARY KEY,
    application     VARCHAR(255) NOT NULL,

    -- Grouping
    group_label     VARCHAR(255),
    components      TEXT[] NOT NULL,

    -- Failure context
    root_cause      TEXT,
    failed_step     VARCHAR(255),

    -- Status tracking
    status          VARCHAR(50) DEFAULT 'active'
                    CHECK (status IN ('active', 'resolved', 'monitoring')),
    slack_thread_url TEXT,
    jira_key        VARCHAR(50),
    notes           TEXT,

    -- Resolution
    resolution      TEXT,
    resolution_pr_url TEXT,
    resolved_at     TIMESTAMPTZ,

    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_triage_app ON triage_items(application);
CREATE INDEX IF NOT EXISTS idx_triage_status ON triage_items(status);
CREATE INDEX IF NOT EXISTS idx_triage_app_active ON triage_items(application)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_triage_created ON triage_items(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_triage_components ON triage_items USING GIN(components);
