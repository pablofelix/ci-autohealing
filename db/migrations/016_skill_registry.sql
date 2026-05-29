-- Skill registry tables for cluster-ready skill management.
-- Skills can be loaded from external git repos and queried via CLI, MCP, and API.

CREATE TABLE IF NOT EXISTS skill_sources (
    name            VARCHAR(100) PRIMARY KEY,
    url             TEXT NOT NULL,
    commit_sha      VARCHAR(40),
    local_path      TEXT,
    added_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS skills (
    qualified_name  VARCHAR(200) PRIMARY KEY,  -- 'source/name'
    name            VARCHAR(100) NOT NULL,
    source          VARCHAR(100) NOT NULL REFERENCES skill_sources(name) ON DELETE CASCADE,
    path            TEXT,
    status          VARCHAR(20) DEFAULT 'active',
    tags            TEXT[] DEFAULT '{}',
    metadata        JSONB DEFAULT '{}',
    added_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_skills_source ON skills(source);
CREATE INDEX IF NOT EXISTS idx_skills_tags ON skills USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);
