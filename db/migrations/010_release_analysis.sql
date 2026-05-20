-- Migration 010: Release failure analysis support
--
-- Extends the AI analysis system to handle release pipeline failures.
-- Unlike build/conforma failures which have their own source tables,
-- release analyses are keyed by release_name (the Release CR name)
-- because release data comes from live cluster + KubeArchive, not a DB table.

ALTER TABLE ai_analysis
    ADD COLUMN IF NOT EXISTS release_name VARCHAR(255);

CREATE INDEX IF NOT EXISTS idx_ai_release_name ON ai_analysis(release_name);
CREATE INDEX IF NOT EXISTS idx_ai_release_analyzed
    ON ai_analysis(release_name, analyzed_at DESC)
    WHERE release_name IS NOT NULL;

-- Update ai_analysis constraint to allow release_name as third source type
ALTER TABLE ai_analysis DROP CONSTRAINT IF EXISTS ai_analysis_result_type_check;
ALTER TABLE ai_analysis ADD CONSTRAINT ai_analysis_result_type_check
CHECK (
    (build_failure_id IS NOT NULL AND conforma_result_id IS NULL AND release_name IS NULL)
    OR
    (build_failure_id IS NULL AND conforma_result_id IS NOT NULL AND release_name IS NULL)
    OR
    (build_failure_id IS NULL AND conforma_result_id IS NULL AND release_name IS NOT NULL)
);

-- Recreate the helper view (DROP required because a.* column set changed)
DROP VIEW IF EXISTS ai_analysis_with_type;
CREATE VIEW ai_analysis_with_type AS
SELECT
    a.*,
    CASE
        WHEN a.build_failure_id IS NOT NULL THEN 'build_failure'
        WHEN a.conforma_result_id IS NOT NULL THEN 'conforma_violation'
        WHEN a.release_name IS NOT NULL THEN 'release_failure'
        ELSE 'unknown'
    END as result_type,
    COALESCE(
        (SELECT component_name FROM build_failures WHERE id = a.build_failure_id),
        (SELECT component_name FROM conforma_results WHERE id = a.conforma_result_id),
        a.release_name
    ) as component_name
FROM ai_analysis a;

-- Update CHECK constraint to allow 'release' as a failure_type in error_patterns
ALTER TABLE error_patterns DROP CONSTRAINT IF EXISTS error_patterns_failure_type_check;
ALTER TABLE error_patterns ADD CONSTRAINT error_patterns_failure_type_check
    CHECK (failure_type IN ('build', 'conforma', 'release'));

-- Seed: known release failure patterns
INSERT INTO error_patterns
    (failure_type, failure_category, pattern_name, description, typical_fix, created_by)
VALUES
(
    'release', 'unmapped_image', 'olm-unmapped-references',
    'Image referenced in operator CSV is not present in the snapshot and not accessible in the target registry. verify-conforma reports olm.unmapped_references violation.',
    '- Check if the image exists in the target registry (registry.redhat.io for prod, registry.stage.redhat.io for stage).\n\n- If the image is from the same product, verify it was included in the snapshot and the RPA mapping is correct.\n\n- If the image is from another product, coordinate with that team to push their images first.',
    'seeded'
),
(
    'release', 'rpa_mapping_typo', 'rpa-component-name-typo',
    'Component name in the ReleasePlanAdmission (RPA) does not match the actual Konflux component name, causing the image to be mapped to a wrong registry path.',
    '- Compare the component name in the RPA YAML against the actual component names in the snapshot.\n\n- Fix the component name in konflux-release-data RPA YAML (both prod and stage).\n\n- Also check if the same typo exists in subsequent version RPAs.',
    'seeded'
),
(
    'release', 'cross_product_dependency', 'cross-product-image-missing',
    'Operator bundle references images from another product (e.g., RHAII vLLM images in RHOAI) that have not been released to the target registry yet.',
    '- Identify the owning product team from the registry namespace (e.g., rhaii/ -> RHAII team).\n\n- Coordinate with the owning team to push their images to the target registry.\n\n- After images are available, update bundle/additional-images-patch.yaml with prod registry digests.',
    'seeded'
),
(
    'release', 'validation_error', 'release-validation-failure',
    'Release failed at the validation stage (before the managed pipeline starts). Common causes: ReleasePlan not found, snapshot missing, or permission issues.',
    '- Check the Validated condition message on the Release CR for the specific error.\n\n- Verify the ReleasePlan exists and the name matches.\n\n- Verify the snapshot exists and has not been garbage-collected.',
    'seeded'
),
(
    'release', 'infrastructure', 'release-infrastructure-failure',
    'Release pipeline failed due to infrastructure issues (registry timeout, Tekton node failure, transient API errors).',
    '- Retry the release — infrastructure failures are often transient.\n\n- If the issue persists, check Konflux status or #konflux-users Slack.',
    'seeded'
)

ON CONFLICT (failure_type, failure_category) DO NOTHING;
