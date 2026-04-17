-- CI/CD Auto-Healing Database Schema
-- Database: konflux_monitoring (same Postgres as Langfuse)

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- 1. BUILD FAILURES - Core table for all build failures
-- ============================================================================
CREATE TABLE IF NOT EXISTS build_failures (
    id SERIAL PRIMARY KEY,
    uuid UUID DEFAULT uuid_generate_v4() UNIQUE,

    -- Identification
    component_name VARCHAR(255) NOT NULL,
    pipelinerun_name VARCHAR(255) UNIQUE NOT NULL,
    pipelinerun_uid VARCHAR(100),
    application VARCHAR(100),
    namespace VARCHAR(100) DEFAULT 'NAMESPACE_PLACEHOLDER',

    -- Source Control
    repository VARCHAR(255),
    repository_url TEXT,
    branch VARCHAR(100),
    commit_sha VARCHAR(40),
    commit_short_sha VARCHAR(10),
    commit_message TEXT,
    commit_url TEXT,
    commit_author VARCHAR(255),
    commit_timestamp TIMESTAMP,
    pr_number INTEGER,
    pr_url TEXT,

    -- Build Info
    status VARCHAR(50) NOT NULL,  -- Failed, Running, Cancelled, Timeout
    failed_task_name VARCHAR(255),
    failed_step_name VARCHAR(255),
    error_message TEXT,
    error_type VARCHAR(100),  -- build_error, test_failure, timeout, oom, etc
    failure_reason VARCHAR(255),

    -- Timing
    build_start_time TIMESTAMP,
    build_completion_time TIMESTAMP,
    build_duration_seconds INTEGER,
    first_detected_at TIMESTAMP DEFAULT NOW(),
    last_updated_at TIMESTAMP DEFAULT NOW(),

    -- Resolution Tracking
    is_resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP,
    resolution_type VARCHAR(50),  -- manual, auto_ai, retry_success, reverted
    resolution_commit_sha VARCHAR(40),
    resolution_pr_url TEXT,
    resolution_notes TEXT,

    -- Output
    output_image TEXT,
    image_digest VARCHAR(255),

    -- Logs & Debug
    build_logs TEXT,  -- Full build logs from pods/KubeArchive
    logs_snippet TEXT,  -- Last 100 lines of error
    logs_full_url TEXT,  -- URL to full logs (S3/storage)
    konflux_url TEXT,
    konflux_logs_url TEXT,  -- Direct link to logs in Konflux UI
    raw_pipelinerun_yaml JSONB,  -- Full PR YAML for reference

    -- AI Processing
    ai_analyzed BOOLEAN DEFAULT FALSE,
    ai_analysis_id INTEGER,  -- FK to ai_analysis
    ai_fix_attempted BOOLEAN DEFAULT FALSE,
    ai_fix_successful BOOLEAN,

    -- Metadata
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for build_failures
CREATE INDEX IF NOT EXISTS idx_bf_component ON build_failures(component_name);
CREATE INDEX IF NOT EXISTS idx_bf_pipelinerun_uid ON build_failures(pipelinerun_uid);
CREATE INDEX IF NOT EXISTS idx_bf_status ON build_failures(status);
CREATE INDEX IF NOT EXISTS idx_bf_resolved ON build_failures(is_resolved);
CREATE INDEX IF NOT EXISTS idx_bf_completion_time ON build_failures(build_completion_time DESC);
CREATE INDEX IF NOT EXISTS idx_bf_error_type ON build_failures(error_type);
CREATE INDEX IF NOT EXISTS idx_bf_ai_pending ON build_failures(ai_analyzed, is_resolved)
    WHERE NOT ai_analyzed AND NOT is_resolved;
CREATE INDEX IF NOT EXISTS idx_bf_created_at ON build_failures(created_at DESC);

-- ============================================================================
-- 2. AI ANALYSIS - AI diagnosis of each failure
-- ============================================================================
CREATE TABLE IF NOT EXISTS ai_analysis (
    id SERIAL PRIMARY KEY,
    uuid UUID DEFAULT uuid_generate_v4() UNIQUE,
    build_failure_id INTEGER REFERENCES build_failures(id) ON DELETE CASCADE,

    -- Analysis
    analyzed_at TIMESTAMP DEFAULT NOW(),
    model_used VARCHAR(100),  -- claude-sonnet-4-5, etc

    -- Diagnosis
    root_cause TEXT,  -- AI's diagnosis
    failure_category VARCHAR(100),  -- dependency_issue, syntax_error, resource_limit, etc
    confidence_score DECIMAL(3,2),  -- 0.0 - 1.0

    -- Recommendations
    recommended_fix TEXT,
    recommended_files TEXT[],  -- Files that need to be changed
    can_auto_fix BOOLEAN DEFAULT FALSE,
    requires_human_review BOOLEAN DEFAULT FALSE,

    -- Langfuse Tracking
    langfuse_trace_id VARCHAR(255),
    langfuse_trace_url TEXT,
    langfuse_observation_id VARCHAR(255),

    -- Cost & Performance
    tokens_used INTEGER,
    cost_usd DECIMAL(10,4),
    analysis_duration_seconds INTEGER,

    -- Full AI Response
    analysis_json JSONB,  -- Full structured response from AI

    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_build_failure ON ai_analysis(build_failure_id);
CREATE INDEX IF NOT EXISTS idx_ai_category ON ai_analysis(failure_category);
CREATE INDEX IF NOT EXISTS idx_ai_can_auto_fix ON ai_analysis(can_auto_fix);
CREATE INDEX IF NOT EXISTS idx_ai_langfuse_trace ON ai_analysis(langfuse_trace_id);

-- ============================================================================
-- 3. RESOLUTION ATTEMPTS - Track all fix attempts (AI or manual)
-- ============================================================================
CREATE TABLE IF NOT EXISTS resolution_attempts (
    id SERIAL PRIMARY KEY,
    uuid UUID DEFAULT uuid_generate_v4() UNIQUE,
    build_failure_id INTEGER REFERENCES build_failures(id) ON DELETE CASCADE,
    ai_analysis_id INTEGER REFERENCES ai_analysis(id) ON DELETE SET NULL,

    -- Attempt Info
    attempt_number INTEGER NOT NULL,  -- 1st, 2nd, 3rd attempt
    attempted_at TIMESTAMP DEFAULT NOW(),
    attempted_by VARCHAR(100),  -- 'ai-agent', 'user:operator', 'skill:ci-fix'

    -- Strategy
    resolution_strategy VARCHAR(100),  -- code_fix, dependency_update, config_change, retry, revert
    changes_description TEXT,

    -- PR Info (if created)
    pr_created BOOLEAN DEFAULT FALSE,
    pr_number INTEGER,
    pr_url TEXT,
    pr_branch VARCHAR(255),
    pr_commits TEXT[],  -- Array of commit SHAs
    pr_merged BOOLEAN DEFAULT FALSE,
    pr_merged_at TIMESTAMP,

    -- Files Changed
    files_modified TEXT[],  -- Array of file paths
    diff_url TEXT,
    diff_content TEXT,  -- Actual diff

    -- Result
    status VARCHAR(50),  -- pending, pr_created, build_triggered, success, failed, abandoned
    result_pipelinerun_name VARCHAR(255),
    result_build_status VARCHAR(50),

    -- Success Tracking
    was_successful BOOLEAN,
    verified_at TIMESTAMP,
    verification_notes TEXT,

    -- Langfuse Tracking
    langfuse_trace_id VARCHAR(255),
    langfuse_observation_id VARCHAR(255),

    -- Cost
    tokens_used INTEGER,
    cost_usd DECIMAL(10,4),

    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ra_build_failure ON resolution_attempts(build_failure_id);
CREATE INDEX IF NOT EXISTS idx_ra_status ON resolution_attempts(status);
CREATE INDEX IF NOT EXISTS idx_ra_successful ON resolution_attempts(was_successful);
CREATE INDEX IF NOT EXISTS idx_ra_attempted_by ON resolution_attempts(attempted_by);

-- ============================================================================
-- 4. COMPONENT HEALTH - Aggregated health metrics per component
-- ============================================================================
CREATE TABLE IF NOT EXISTS component_health (
    component_name VARCHAR(255) PRIMARY KEY,
    application VARCHAR(100),
    repository VARCHAR(255),
    repository_url TEXT,

    -- Build Status
    last_successful_build TIMESTAMP,
    last_failed_build TIMESTAMP,
    last_build_pipelinerun VARCHAR(255),
    current_status VARCHAR(50),  -- healthy, failing, degraded, unknown

    -- Failure Stats
    consecutive_failures INTEGER DEFAULT 0,
    total_failures_last_7d INTEGER DEFAULT 0,
    total_failures_last_30d INTEGER DEFAULT 0,
    total_builds_last_7d INTEGER DEFAULT 0,
    total_builds_last_30d INTEGER DEFAULT 0,

    -- Performance
    avg_build_duration_seconds INTEGER,
    success_rate_last_7d DECIMAL(5,2),  -- Percentage
    success_rate_last_30d DECIMAL(5,2),  -- Percentage

    -- AI Stats
    ai_analyses_count INTEGER DEFAULT 0,
    ai_fixes_attempted INTEGER DEFAULT 0,
    ai_fixes_successful INTEGER DEFAULT 0,
    ai_success_rate DECIMAL(5,2),

    -- Health Score (0-100)
    health_score INTEGER,
    health_status VARCHAR(20),  -- healthy (80-100), warning (50-79), critical (0-49)

    -- Alerts
    alert_sent BOOLEAN DEFAULT FALSE,
    last_alert_sent_at TIMESTAMP,
    alert_threshold_failures INTEGER DEFAULT 3,

    -- Metadata
    last_scanned_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ch_health_status ON component_health(health_status);
CREATE INDEX IF NOT EXISTS idx_ch_consecutive_failures ON component_health(consecutive_failures DESC);
CREATE INDEX IF NOT EXISTS idx_ch_current_status ON component_health(current_status);

-- ============================================================================
-- 5. EVENT LOG - Audit log of all system events
-- ============================================================================
CREATE TABLE IF NOT EXISTS event_log (
    id SERIAL PRIMARY KEY,
    uuid UUID DEFAULT uuid_generate_v4() UNIQUE,

    event_type VARCHAR(100) NOT NULL,  -- build_failed, ai_analysis_started, pr_created, etc
    event_source VARCHAR(100),  -- webhook, manual, cron, daemon, ai_agent, skill
    event_level VARCHAR(20) DEFAULT 'info',  -- debug, info, warning, error, critical

    component_name VARCHAR(255),
    pipelinerun_name VARCHAR(255),

    message TEXT,
    payload JSONB,  -- Full event data

    -- Context
    user_id VARCHAR(255),  -- Who triggered (if manual)
    session_id VARCHAR(255),  -- For correlating related events

    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_el_event_type ON event_log(event_type);
CREATE INDEX IF NOT EXISTS idx_el_created_at ON event_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_el_component ON event_log(component_name);
CREATE INDEX IF NOT EXISTS idx_el_event_level ON event_log(event_level);
CREATE INDEX IF NOT EXISTS idx_el_session ON event_log(session_id);

-- ============================================================================
-- 6. SCAN HISTORY - Track scanner runs
-- ============================================================================
CREATE TABLE IF NOT EXISTS scan_history (
    id SERIAL PRIMARY KEY,
    scan_id UUID DEFAULT uuid_generate_v4() UNIQUE,

    scan_type VARCHAR(50),  -- daemon, manual, trigger, cron
    scan_mode VARCHAR(50),  -- full, incremental, specific_component

    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    duration_seconds INTEGER,

    -- Results
    components_scanned INTEGER DEFAULT 0,
    failures_found INTEGER DEFAULT 0,
    new_failures INTEGER DEFAULT 0,
    resolved_failures INTEGER DEFAULT 0,

    -- Status
    status VARCHAR(50),  -- running, completed, failed, cancelled
    error_message TEXT,

    -- Config
    config JSONB,  -- Scanner configuration used

    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sh_started_at ON scan_history(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sh_status ON scan_history(status);

-- ============================================================================
-- VIEWS - Convenient queries
-- ============================================================================

-- Active failures view
CREATE OR REPLACE VIEW active_failures AS
SELECT
    bf.id,
    bf.component_name,
    bf.pipelinerun_name,
    bf.failed_task_name,
    bf.error_message,
    bf.build_completion_time,
    bf.ai_analyzed,
    bf.ai_fix_attempted,
    aa.root_cause,
    aa.can_auto_fix,
    COUNT(ra.id) as fix_attempts
FROM build_failures bf
LEFT JOIN ai_analysis aa ON bf.ai_analysis_id = aa.id
LEFT JOIN resolution_attempts ra ON bf.id = ra.build_failure_id
WHERE bf.is_resolved = FALSE
GROUP BY bf.id, aa.id
ORDER BY bf.build_completion_time DESC;

-- Component metrics view
CREATE OR REPLACE VIEW component_metrics AS
SELECT
    ch.component_name,
    ch.current_status,
    ch.consecutive_failures,
    ch.success_rate_last_7d,
    ch.health_score,
    COUNT(bf.id) FILTER (WHERE bf.created_at > NOW() - INTERVAL '7 days') as failures_7d,
    COUNT(ra.id) FILTER (WHERE ra.was_successful = TRUE) as successful_fixes
FROM component_health ch
LEFT JOIN build_failures bf ON ch.component_name = bf.component_name
LEFT JOIN resolution_attempts ra ON bf.id = ra.build_failure_id
GROUP BY ch.component_name, ch.current_status, ch.consecutive_failures,
         ch.success_rate_last_7d, ch.health_score
ORDER BY ch.health_score ASC;

-- AI performance view
CREATE OR REPLACE VIEW ai_performance AS
SELECT
    DATE(analyzed_at) as analysis_date,
    COUNT(*) as total_analyses,
    COUNT(*) FILTER (WHERE can_auto_fix = TRUE) as auto_fixable,
    AVG(confidence_score) as avg_confidence,
    SUM(tokens_used) as total_tokens,
    SUM(cost_usd) as total_cost,
    AVG(analysis_duration_seconds) as avg_duration_seconds
FROM ai_analysis
GROUP BY DATE(analyzed_at)
ORDER BY analysis_date DESC;

-- ============================================================================
-- FUNCTIONS - Utility functions
-- ============================================================================

-- Function to update component health
CREATE OR REPLACE FUNCTION update_component_health(comp_name VARCHAR)
RETURNS VOID AS $$
DECLARE
    last_success TIMESTAMP;
    last_failure TIMESTAMP;
    consec_fails INTEGER;
    fails_7d INTEGER;
    fails_30d INTEGER;
    builds_7d INTEGER;
    builds_30d INTEGER;
    success_rate_7d DECIMAL;
    success_rate_30d DECIMAL;
    health_value INTEGER;
    status_value VARCHAR;
BEGIN
    -- Get statistics
    SELECT MAX(build_completion_time) INTO last_success
    FROM build_failures
    WHERE component_name = comp_name AND status = 'Succeeded';

    SELECT MAX(build_completion_time) INTO last_failure
    FROM build_failures
    WHERE component_name = comp_name AND status = 'Failed';

    -- Count consecutive failures
    WITH ranked_builds AS (
        SELECT status, ROW_NUMBER() OVER (ORDER BY build_completion_time DESC) as rn
        FROM build_failures
        WHERE component_name = comp_name
        ORDER BY build_completion_time DESC
        LIMIT 20
    )
    SELECT COUNT(*) INTO consec_fails
    FROM ranked_builds
    WHERE rn <= (SELECT MIN(rn) FROM ranked_builds WHERE status != 'Failed');

    -- Count failures
    SELECT COUNT(*) INTO fails_7d
    FROM build_failures
    WHERE component_name = comp_name
      AND status = 'Failed'
      AND build_completion_time > NOW() - INTERVAL '7 days';

    SELECT COUNT(*) INTO fails_30d
    FROM build_failures
    WHERE component_name = comp_name
      AND status = 'Failed'
      AND build_completion_time > NOW() - INTERVAL '30 days';

    -- Count total builds
    SELECT COUNT(*) INTO builds_7d
    FROM build_failures
    WHERE component_name = comp_name
      AND build_completion_time > NOW() - INTERVAL '7 days';

    SELECT COUNT(*) INTO builds_30d
    FROM build_failures
    WHERE component_name = comp_name
      AND build_completion_time > NOW() - INTERVAL '30 days';

    -- Calculate success rates
    IF builds_7d > 0 THEN
        success_rate_7d := ((builds_7d - fails_7d)::DECIMAL / builds_7d) * 100;
    ELSE
        success_rate_7d := 100;
    END IF;

    IF builds_30d > 0 THEN
        success_rate_30d := ((builds_30d - fails_30d)::DECIMAL / builds_30d) * 100;
    ELSE
        success_rate_30d := 100;
    END IF;

    -- Calculate health score (0-100)
    health_value := LEAST(100, GREATEST(0,
        CAST(success_rate_30d AS INTEGER) - (consec_fails * 10)
    ));

    -- Determine status
    IF health_value >= 80 THEN
        status_value := 'healthy';
    ELSIF health_value >= 50 THEN
        status_value := 'warning';
    ELSE
        status_value := 'critical';
    END IF;

    -- Determine current status
    DECLARE current_stat VARCHAR;
    IF last_failure IS NULL THEN
        current_stat := 'healthy';
    ELSIF last_success IS NULL OR last_failure > last_success THEN
        current_stat := 'failing';
    ELSE
        current_stat := 'healthy';
    END IF;

    -- Update or insert
    INSERT INTO component_health (
        component_name,
        last_successful_build,
        last_failed_build,
        current_status,
        consecutive_failures,
        total_failures_last_7d,
        total_failures_last_30d,
        total_builds_last_7d,
        total_builds_last_30d,
        success_rate_last_7d,
        success_rate_last_30d,
        health_score,
        health_status,
        updated_at
    ) VALUES (
        comp_name,
        last_success,
        last_failure,
        current_stat,
        COALESCE(consec_fails, 0),
        COALESCE(fails_7d, 0),
        COALESCE(fails_30d, 0),
        COALESCE(builds_7d, 0),
        COALESCE(builds_30d, 0),
        success_rate_7d,
        success_rate_30d,
        health_value,
        status_value,
        NOW()
    )
    ON CONFLICT (component_name) DO UPDATE SET
        last_successful_build = EXCLUDED.last_successful_build,
        last_failed_build = EXCLUDED.last_failed_build,
        current_status = EXCLUDED.current_status,
        consecutive_failures = EXCLUDED.consecutive_failures,
        total_failures_last_7d = EXCLUDED.total_failures_last_7d,
        total_failures_last_30d = EXCLUDED.total_failures_last_30d,
        total_builds_last_7d = EXCLUDED.total_builds_last_7d,
        total_builds_last_30d = EXCLUDED.total_builds_last_30d,
        success_rate_last_7d = EXCLUDED.success_rate_last_7d,
        success_rate_last_30d = EXCLUDED.success_rate_last_30d,
        health_score = EXCLUDED.health_score,
        health_status = EXCLUDED.health_status,
        updated_at = NOW();
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- TRIGGERS - Automatic updates
-- ============================================================================

-- Update component health when build failure is inserted/updated
CREATE OR REPLACE FUNCTION trigger_update_component_health()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM update_component_health(NEW.component_name);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_health_after_build_failure
AFTER INSERT OR UPDATE ON build_failures
FOR EACH ROW
EXECUTE FUNCTION trigger_update_component_health();

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION trigger_set_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_timestamp_build_failures
BEFORE UPDATE ON build_failures
FOR EACH ROW
EXECUTE FUNCTION trigger_set_timestamp();

CREATE TRIGGER set_timestamp_resolution_attempts
BEFORE UPDATE ON resolution_attempts
FOR EACH ROW
EXECUTE FUNCTION trigger_set_timestamp();

-- ============================================================================
-- GRANTS - Permissions
-- ============================================================================

-- Grant permissions to langfuse user (assuming same user)
-- Adjust username as needed
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO langfuse_user;
-- GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO langfuse_user;
-- GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO langfuse_user;
