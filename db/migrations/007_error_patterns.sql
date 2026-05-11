-- Migration 007: Error pattern library
--
-- Stores known error patterns with their solutions and doc references.
-- Each pattern represents a class of failures (build or conforma) that
-- recurs across components. When the analyzer sees a known pattern, the
-- typical_fix and doc_context are included in the prompt so Claude has
-- institutional memory from previous occurrences.
--
-- Patterns are created automatically after each AI analysis (created_by='auto')
-- and can be enriched manually (created_by='manual') or seeded at migration
-- time (created_by='seeded').
--
-- Design note: keyed on (failure_type, failure_category) — one row per
-- Claude category, not per component. Sub-patterns within a category can be
-- added later via a pattern_subtype column without schema breakage.

CREATE TABLE IF NOT EXISTS error_patterns (
    id                SERIAL PRIMARY KEY,
    failure_type      VARCHAR(20)  NOT NULL CHECK (failure_type IN ('build', 'conforma')),
    failure_category  VARCHAR(100) NOT NULL,
    pattern_name      VARCHAR(200) NOT NULL,
    description       TEXT,          -- human-readable explanation of the pattern
    typical_fix       TEXT,          -- solution that has worked in past occurrences
    doc_url           TEXT,          -- primary documentation page for this pattern
    doc_context       TEXT,          -- cached excerpt from doc_url (plain text, ~3000 chars)
    doc_fetched_at    TIMESTAMP,     -- when doc_context was last refreshed
    occurrence_count  INTEGER        DEFAULT 0,
    avg_confidence    FLOAT,         -- rolling average of confidence_score across analyses
    first_seen_at     TIMESTAMP      DEFAULT NOW(),
    last_seen_at      TIMESTAMP      DEFAULT NOW(),
    created_by        VARCHAR(20)    DEFAULT 'auto',  -- 'auto' | 'manual' | 'seeded'
    UNIQUE(failure_type, failure_category)
);

CREATE INDEX IF NOT EXISTS idx_ep_type_cat ON error_patterns(failure_type, failure_category);

-- FK from ai_analysis to the matched pattern
ALTER TABLE ai_analysis
    ADD COLUMN IF NOT EXISTS error_pattern_id INTEGER
        REFERENCES error_patterns(id) ON DELETE SET NULL;

-- ============================================================================
-- Seed: known build failure patterns (from prompts/build_failure_analyzer.md)
-- ============================================================================
INSERT INTO error_patterns
    (failure_type, failure_category, pattern_name, description, typical_fix, doc_url, created_by)
VALUES
(
    'build', 'config_error', 'context-path-escape',
    'Build fails because the path-context parameter in a Tekton config contains a leading ./ prefix that Buildah interprets as a path traversal attempt.',
    '- Remove the ./ prefix from the path-context value in the .tekton/ YAML file (e.g. change ./jobs/async-upload to jobs/async-upload).\n\n- Check if the value was introduced by a konflux-central sync and fix it upstream if so.\n\n- Rebuild after the fix — confidence should be 0.9+ when this pattern is matched.',
    'https://konflux.pages.redhat.com/docs/users/building/',
    'seeded'
),
(
    'build', 'build_error', 'generic-build-error',
    'Build step fails with an error that does not match a specific known pattern. Common causes: missing dependencies, compiler errors, base image issues.',
    '- Check the build logs for the specific error message and trace it to the commit diff.\n\n- Look for changes to Dockerfile, requirements files, or build scripts in the latest commit.\n\n- If the error mentions a missing package or module, check if it was recently removed or renamed.',
    'https://konflux.pages.redhat.com/docs/users/building/',
    'seeded'
),
(
    'build', 'dependency_issue', 'dependency-resolution-failure',
    'Build fails because a dependency cannot be fetched or resolved. Common in hermetic builds where network access is restricted.',
    '- Check if the dependency is available in an approved source (Red Hat RPM repos, vendored PyPI).\n\n- For hermetic builds, ensure all dependencies are pre-fetched using cachi2 or equivalent prefetching.\n\n- Pin exact versions to avoid resolution failures from version ranges.',
    'https://konflux.pages.redhat.com/docs/users/building/',
    'seeded'
),
(
    'build', 'test_failure', 'test-failure',
    'A test step within the pipeline fails. May be a real regression or a flaky test.',
    '- Check the test logs for the specific assertion or error that failed.\n\n- Look at the commit diff for code changes that could affect test behavior.\n\n- If the test was passing before this commit, trace the failure to the specific change.',
    'https://konflux.pages.redhat.com/docs/users/testing/',
    'seeded'
),
(
    'build', 'resource_limit', 'resource-limit-exceeded',
    'Build fails because it exceeded CPU, memory, or storage limits defined in the pipeline.',
    '- Check the resource requests/limits in the Tekton config (.tekton/*.yaml).\n\n- Increase limits if the build genuinely requires more resources.\n\n- Look for memory leaks or inefficiencies introduced in the latest commit.',
    'https://konflux.pages.redhat.com/docs/users/building/',
    'seeded'
),
(
    'build', 'git_sync_issue', 'git-sync-failure',
    'Build fails at the git clone or sync step, typically due to authentication or network issues.',
    '- Verify that the component repository is accessible from Konflux.\n\n- Check if any secrets or credentials needed for git access are configured correctly.\n\n- Look for recent changes to the repository URL or branch configuration.',
    'https://konflux.pages.redhat.com/docs/users/building/',
    'seeded'
),
(
    'build', 'infrastructure', 'infrastructure-failure',
    'Build fails due to an infrastructure or platform issue (node failure, registry timeout, storage issue) rather than a code problem.',
    '- Retry the build — infrastructure failures are often transient.\n\n- If the issue persists, check the Konflux status page or #konflux-users Slack.\n\n- Look for patterns: does this happen on specific architectures or node types?',
    'https://konflux.pages.redhat.com/docs/users/building/',
    'seeded'
),

-- ============================================================================
-- Seed: known conforma patterns (from prompts/conforma_analyzer.md)
-- ============================================================================
(
    'conforma', 'policy_hermetic_build', 'hermetic-build-missing',
    'Container image was not built in a hermetic environment (no internet access during build). The pipeline has hermetic: false or the parameter is missing.',
    '- Set hermetic: true in the pipeline spec params section of the Tekton config.\n\n- If the build truly cannot be hermetic, request a policy exception via the ProdSec process (rare, requires strong justification).',
    'https://konflux.pages.redhat.com/docs/users/building/',
    'seeded'
),
(
    'conforma', 'policy_unpinned_task', 'unpinned-task-reference',
    'A Tekton task is referenced by branch name (e.g. revision: main) instead of a specific commit sha. This is a hard requirement with no exception path.',
    '- Pin the task reference to a specific commit sha (revision: sha256:...).\n\n- Example PR: https://github.com/acme-org/konflux-central/pull/1358\n\n- Check Renovate PRs in konflux-central for automated updates.',
    'https://conforma.dev/docs/user-guide/',
    'seeded'
),
(
    'conforma', 'policy_untrusted_image', 'outdated-task-bundle',
    'A Konflux container image used during the build is more than 1 month old. Task bundle digests go stale as new versions are released.',
    '- Check the current digest on quay.io for the affected task bundle image.\n\n- Update the digest reference in the .tekton/ config or in konflux-central.\n\n- Rebuild the component after updating.',
    'https://conforma.dev/docs/user-guide/',
    'seeded'
),
(
    'conforma', 'policy_package_source', 'disallowed-package-source',
    'Packages fetched during a hermetic build came from an unapproved source (e.g. huggingface.co, non-RHOAI PyPI packages).',
    '- Install the package from a Red Hat RPM repository if an RPM is available.\n\n- If using PyPI, verify the package is covered by RHOAI legal agreements.\n\n- Alternatively, vendor the source code into the source container image.\n\n- If no approved alternative exists, file a policy exception: https://JIRA_CREATE_ISSUE_URL',
    'https://conforma.dev/docs/user-guide/',
    'seeded'
),
(
    'conforma', 'policy_signing_key', 'non-rh-signing-key',
    'Software in the image is signed by a non-Red Hat key (e.g. Intel, NVIDIA, a third-party vendor).',
    '- Use a Red Hat signed version of the software if available.\n\n- For external software, include the source code in the source container image.\n\n- If Red Hat has a legal agreement with the vendor (e.g. NVIDIA CUDA), that may cover it.\n\n- Otherwise, request a policy exception via the ProdSec process.',
    'https://conforma.dev/docs/user-guide/',
    'seeded'
),
(
    'conforma', 'policy_rpm_repository', 'rpm-repository-issue',
    'RPM repository issue: either mismatched versions across architectures, or repository IDs that use a generic format instead of the arch-specific format.',
    '- For mismatched versions: rebuild the component — multi-arch builds can pick up different RPM versions if not built simultaneously.\n\n- For unknown repository IDs: update Dockerfile repo IDs to use arch-specific format (e.g. ubi-9-for-x86_64-baseos-rpms instead of ubi-9-baseos-rpms), then rebuild rpms.lock.yaml.',
    'https://conforma.dev/docs/user-guide/',
    'seeded'
),
(
    'conforma', 'policy_version_label', 'version-label-mismatch',
    'The container image version label does not match the expected version from conforma-reporter config (e.g. built before the version label was updated for a new release).',
    '- Rebuild the component in Konflux. A fresh build will pick up the current version label automatically.',
    'https://conforma.dev/docs/user-guide/',
    'seeded'
),
(
    'conforma', 'policy_fips_check', 'missing-fips-check',
    'FIPS compliance check is missing or failing. For FBC fragments, the FIPS check task is disabled on push builds (takes 2-4 hours) and only runs on nightly builds.',
    '- If this appears only on push/CI builds: ignore it — the FIPS check is intentionally disabled there.\n\n- Check the nightly build logs for actual FIPS failures before investigating further.',
    'https://conforma.dev/docs/user-guide/',
    'seeded'
),
(
    'conforma', 'policy_deprecated_task', 'deprecated-task-bundle',
    'A Tekton task bundle digest is scheduled for deprecation. The solution field in the violation contains the old and new bundle refs.',
    '- Update the task bundle digest in the .tekton/ configs or in konflux-central to the newer ref shown in the violation solution field.\n\n- The fix-generator can handle this automatically (policy_deprecated_task is the only auto-fixable conforma pattern).',
    'https://conforma.dev/docs/user-guide/',
    'seeded'
),
(
    'conforma', 'config_error', 'conforma-config-error',
    'The component has a configuration error that causes Conforma to report a policy failure unrelated to a specific known violation type.',
    '- Review the violation summary carefully for the specific rule that is failing.\n\n- Check #konflux-users Slack for known issues with this rule.\n\n- Contact @owatkins (ProdSec) if the violation is unclear.',
    'https://conforma.dev/docs/user-guide/',
    'seeded'
),
(
    'conforma', 'infrastructure', 'conforma-infrastructure',
    'Conforma itself failed to run or returned an unexpected result due to an infrastructure or platform issue.',
    '- Retry the Conforma check — infrastructure failures are often transient.\n\n- If it persists, check #konflux-users or file a bug against the Conforma service.',
    'https://conforma.dev/docs/user-guide/',
    'seeded'
)

ON CONFLICT (failure_type, failure_category) DO NOTHING;
