-- Migration 009: new conforma categories + enriched Konflux platform bug patterns
-- Run with: docker exec <db> psql -U postgres -d konflux_monitoring -f /workspace/db/migrations/009_new_conforma_patterns.sql

-- ── New conforma categories (Task 101, 104) ───────────────────────────────────

INSERT INTO error_patterns (failure_type, failure_category, pattern_name, description, typical_fix, doc_url, created_by)
VALUES
(
    'conforma', 'policy_sbom_vendor_label',
    'Missing vendor label in Containerfile',
    'Container image is missing LABEL vendor="Red Hat, Inc." required for SBOM metadata compliance.',
    '- Add LABEL vendor="Red Hat, Inc." to the Containerfile after any existing LABEL lines.\n\n- Can be auto-fixed via ic fix — the fixer finds Containerfile/Dockerfile in the repo root and inserts the label.\n\n- Rebuild the component after merging the fix.',
    'https://konflux.pages.redhat.com/docs/users/getting-started/passing-ec.html#common-violations-encountered',
    'seeded'
),
(
    'conforma', 'policy_cpe_label',
    'Missing CPE label in Containerfile',
    'Container image is missing LABEL com.redhat.component.cpe required for Red Hat CVE scanning and supply-chain traceability.',
    '- Add LABEL com.redhat.component.cpe=<cpe-value> to the Containerfile.\n\n- The correct CPE value must be confirmed with Release Engineering — it cannot be inferred automatically.\n\n- File a Jira with the component team to determine the correct CPE and apply the label.',
    'https://konflux.pages.redhat.com/docs/users/getting-started/passing-ec.html#common-violations-encountered',
    'seeded'
),
(
    'conforma', 'policy_source_image',
    'Missing source container image',
    'Build pipeline is not producing a source container image required for license compliance.',
    '- Ensure the Tekton pipeline includes the source-build task from the Konflux task catalog.\n\n- Check that build-source-image task is present in .tekton/*.yaml and enabled.\n\n- If intentionally disabled, file a policy exception via the ProdSec JIRA process.',
    'https://konflux.pages.redhat.com/docs/users/getting-started/passing-ec.html',
    'seeded'
)
ON CONFLICT (failure_type, failure_category) DO NOTHING;


-- ── Enrich existing build patterns with known platform bugs (Task 103) ────────
-- These UPDATE statements extend the typical_fix of existing seeded rows with
-- specific Konflux platform bug knowledge discovered in production.

UPDATE error_patterns
SET
    typical_fix = typical_fix ||
        E'\n\n- KNOWN PLATFORM BUG: If build fails with "Permission denied" on /workspace or /tekton/home, this is a Konflux workspace volume UID mismatch. Retry first; if recurring, report in #konflux-users (platform-side fix only).' ||
        E'\n\n- KNOWN PLATFORM BUG: If Tekton Chains signing step fails after a successful build, this is a transient Sigstore/Rekor reachability issue. Retry the build; persistent failures go to #konflux-users.' ||
        E'\n\n- KNOWN PLATFORM BUG: If PipelineRun status appears stuck or stale after an ArgoCD sync, trigger a manual ArgoCD resync. Konflux state machine may have desynchronised from the actual Tekton execution.',
    created_by = 'seeded'
WHERE failure_type = 'build' AND failure_category = 'infrastructure';

UPDATE error_patterns
SET
    typical_fix = typical_fix ||
        E'\n\n- KNOWN PLATFORM BUG: If Pipeline as Code fails with a git_provider error after a GitHub org rename or repository URL change, reconnect the Konflux GitHub app integration in the application settings and verify the PaC integration points to the current repository URL.',
    created_by = 'seeded'
WHERE failure_type = 'build' AND failure_category = 'config_error';

UPDATE error_patterns
SET
    typical_fix = typical_fix ||
        E'\n\n- KNOWN PLATFORM BUG: If hermetic prefetch fails with a "package not found" error, the required RPM may be missing from rpms.in.yaml. Add the package and rebuild rpms.lock.yaml: https://konflux.pages.redhat.com/docs/users/building/prefetching-dependencies.html#rpm',
    created_by = 'seeded'
WHERE failure_type = 'build' AND failure_category = 'dependency_issue';
