-- Pipeline freeze calendar
CREATE TABLE IF NOT EXISTS release_freezes (
    id SERIAL PRIMARY KEY,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_release_freezes_dates ON release_freezes (start_date, end_date);
