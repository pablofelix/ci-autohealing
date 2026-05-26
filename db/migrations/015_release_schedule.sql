-- Release schedule: milestone dates from Smartsheet per application
CREATE TABLE IF NOT EXISTS release_schedule (
    application TEXT PRIMARY KEY,
    planning_freeze DATE,
    feature_freeze DATE,
    code_freeze DATE,
    initial_rc DATE,
    release_window_start DATE,
    release_date DATE,
    next_release TEXT,
    sheet_id BIGINT,
    updated_at TIMESTAMP DEFAULT NOW()
);
