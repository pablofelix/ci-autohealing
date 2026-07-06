-- Migration 028: Conforma Rule Catalog
--
-- Reference catalog of all 175+ Conforma (Enterprise Contract) policy rules.
-- Seeded from conforma.dev documentation via data/conforma_rule_catalog.json.
--
-- This is distinct from error_patterns (which tracks observed failure patterns
-- with occurrence stats and learning). The rule catalog is reference knowledge
-- about all possible rules — what they check and how to fix them — regardless
-- of whether they've been observed in this project.

CREATE TABLE IF NOT EXISTS conforma_rule_catalog (
    id                SERIAL PRIMARY KEY,
    rule_id           VARCHAR(100) UNIQUE NOT NULL,   -- e.g. 'hermetic_task__hermetic'
    rule_package      VARCHAR(50) NOT NULL,            -- e.g. 'hermetic_task'
    rule_name         VARCHAR(200) NOT NULL,            -- human-readable name
    description       TEXT,                             -- what the rule checks (from conforma.dev)
    policy_type       VARCHAR(20),                      -- release/pipeline/build_task/task/stepaction
    collections       TEXT[],                            -- {redhat,minimal,slsa3,...}
    typical_fix       TEXT,                              -- generic solution from conforma.dev
    reporter_solution TEXT,                              -- RHOAI-specific solution from conforma-reporter
    doc_url           TEXT,                              -- conforma.dev documentation page
    created_at        TIMESTAMP DEFAULT NOW(),
    updated_at        TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crc_package ON conforma_rule_catalog(rule_package);
CREATE INDEX IF NOT EXISTS idx_crc_policy_type ON conforma_rule_catalog(policy_type);
