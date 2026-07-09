-- Add trigger_type to conforma_results to distinguish scheduled (nightly) from push builds.
--
-- Scheduled builds run the full pipeline including FIPS check (fbc-fips-check-oci-ta).
-- Push builds skip FIPS for speed. For release readiness, the scheduled build's
-- conforma result is authoritative — it reflects what will actually ship.
--
-- trigger_type values:
--   'push'      — triggered by a git push (no FIPS for FBC)
--   'scheduled' — triggered by the nightly scheduled pipeline (includes FIPS for FBC)
--   'other'     — anything else (manual, incoming, override)
--
-- Detected at collection time from the Snapshot's
-- pac.test.appstudio.openshift.io/original-prname label:
--   ends with '-on-schedule' → 'scheduled'
--   ends with '-on-push'     → 'push'
--   anything else            → 'other'
--
-- See: docs/adr/007-its-scoping-false-positives.md for related context.

ALTER TABLE conforma_results
    ADD COLUMN IF NOT EXISTS trigger_type VARCHAR(20) DEFAULT 'push';

CREATE INDEX IF NOT EXISTS idx_cr_trigger_type
    ON conforma_results(trigger_type);

CREATE INDEX IF NOT EXISTS idx_cr_component_trigger
    ON conforma_results(component_name, application, trigger_type, last_updated_at DESC);
