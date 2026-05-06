"""Conforma violation analyzer using LLM provider.

Analyzes Enterprise Contract compliance violations. Similar to BuildFailureAnalyzer
but focused on policy compliance rather than code bugs.
"""

import os
import time
from typing import Any, Dict, List, Optional, Tuple

from config import CollectorConfig
from logger import setup_logger
from repositories import DatabaseConnection, AIAnalysisRepository
from clients.llm_provider import create_llm_provider
from clients.langfuse_tracker import LangfuseTracker

logger = setup_logger(__name__)


# Tool schema for structured output from Claude
CONFORMA_ANALYSIS_TOOL = {
    'name': 'record_conforma_analysis',
    'description': 'Record the analysis of a Conforma (Enterprise Contract) compliance violation',
    'input_schema': {
        'type': 'object',
        'properties': {
            'root_cause': {
                'type': 'string',
                'description': 'Clear explanation of why the violation occurred'
            },
            'failure_category': {
                'type': 'string',
                'enum': [
                    'policy_hermetic_build',
                    'policy_unpinned_task',
                    'policy_untrusted_image',
                    'policy_signing_key',
                    'policy_package_source',
                    'policy_rpm_repository',
                    'policy_version_label',
                    'policy_fips_check',
                    'policy_deprecated_task',
                    'config_error',
                    'infrastructure'
                ],
                'description': 'Category of compliance violation'
            },
            'confidence_score': {
                'type': 'number',
                'minimum': 0,
                'maximum': 1,
                'description': 'Confidence in this analysis (0.0-1.0)'
            },
            'recommended_fix': {
                'type': 'string',
                'description': 'Specific fix recommendation or exception request guidance'
            },
            'recommended_files': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': 'Files that need to be modified (if fixable via code)'
            },
            'can_auto_fix': {
                'type': 'boolean',
                'description': 'Whether this can be automatically fixed (usually false for policy violations)'
            },
            'requires_human_review': {
                'type': 'boolean',
                'description': 'Whether human review is needed (usually true - policy exceptions need approval)'
            },
        },
        'required': [
            'root_cause',
            'failure_category',
            'confidence_score',
            'recommended_fix',
            'can_auto_fix'
        ]
    }
}


CONFORMA_SYSTEM_PROMPT = """You are a Conforma (Enterprise Contract) compliance specialist analyzing policy violations for the RHOAI (Red Hat OpenShift AI) project. Your role is to help the team understand what policy is being violated and how to resolve it.

## What is Conforma?

Conforma is Red Hat's Enterprise Contract compliance testing tool. It ensures that container images and build processes meet security, legal, and operational requirements before being released to production. Violations must either be fixed or granted a policy exception through ProdSec approval.

## Tone and Style

Write as a compliance advisor helping the team navigate policy requirements — not as a gatekeeper blocking progress. Use helpful, collaborative language:

- "This appears to violate the hermetic build policy because..." rather than "This violates policy"
- "The quickest path forward might be..." rather than "You must..."
- "If fixing this immediately isn't feasible, you can request a policy exception via..." rather than just "Request an exception"

## Known Conforma Violations and Fixes

### 1. Build not hermetic (hermetic_task.hermetic)

**What it is**: Container images must be built in hermetic environment (no internet access during build).

**Root Cause**: Pipeline spec has `hermetic: false` or missing `hermetic` parameter.

**Fix**:
```yaml
spec:
  params:
    - name: hermetic
      value: true  # Must be true for RHOAI
```

**Exception**: File exception if build truly cannot be hermetic (rare). Requires strong justification.

**Confidence**: 0.95+ if you see `hermetic: false` in Tekton config

---

### 2. Unpinned task reference (tasks.unpinned_task_reference)

**What it is**: Tekton tasks must be pinned to specific commit sha, not branch names like 'main'.

**Root Cause**: Pipeline uses task reference like `revision: main` instead of `revision: sha256:...`.

**Fix**: Pin task to specific commit. Example: https://github.com/acme-org/konflux-central/pull/1358

**Exception**: None - this is a hard requirement.

**Confidence**: 0.95+ if violation message mentions unpinned reference

---

### 3. Untrusted build images (attestation_task_bundle.trusted_task)

**What it is**: Konflux container images used during build must be recent (< 1 month old).

**Root Cause**: Outdated Konflux task bundle container image reference.

**Fix**:
1. Check task bundle sha on quay.io: https://quay.io/konflux-ci/tekton-catalog/task-rpms-signature-scan
2. If old (> 1 month), update to recent digest
3. Rebuild component in Konflux after updating

**Exception**: None - update the reference.

**Confidence**: 0.90+ if violation mentions build-image-index, buildah-oci-ta, etc.

---

### 4. Disallowed package sources (sbom_spdx.allowed_package_sources)

**What it is**: Packages fetched during hermetic build must come from approved sources.

**Root Cause**: Hermetic build prefetched packages from unapproved sources (e.g., huggingface.co, PyPI packages not in RHOAI agreement).

**Approved sources**:
- Red Hat RPM repositories (ubi-*, rhel-*)
- PyPI packages covered by RHOAI agreements (see spreadsheet: https://docs.google.com/spreadsheets/d/1o2j87H-k33eBsDcxR4oeqpNJJZe_TqHarnEBw-PqepM/edit?gid=1354667519)
- Vendored source code in source container image

**Fix options**:
1. **Install from Red Hat repository** - if RPM available
2. **Install from approved PyPI** - if covered by legal agreement
3. **Build from source** - vendor the source code
4. **Request exception** - if no alternative (requires legal/ProdSec approval)

**Exception process**:
- Create JIRA: https://JIRA_CREATE_ISSUE_URL
- Explain why package is needed and why no approved alternative exists
- Attach to exception merge request: https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_DOMAIN/product/EnterpriseContractPolicy/fbc-acme-prod.yaml
- Wait for ProdSec (@owatkins) approval

**Confidence**: 0.90+ if violation shows package URL from unapproved source

---

### 5. Signing key not allowed (sbom_cyclonedx.allowed_sigstore_keys)

**What it is**: Software must be signed by Red Hat key (199e2f9fd431d51) or covered by exception.

**Root Cause**: Package signed by non-RH key (e.g., Intel, NVIDIA).

**Fix options**:
1. **Use RH-signed software** - built/signed by Red Hat
2. **Include source code** - for external software, include source in source container image
3. **Legal agreement** - RH has agreement with vendor (e.g., NVIDIA CUDA)
4. **Request exception** - via same process as #4

**Confidence**: 0.95+ if violation message shows non-RH signing key

---

### 6. Mismatched RPM versions (sbom_spdx.mismatched_rpm_versions)

**What it is**: Multi-arch builds (x86_64, ppc64le, s390x, arm64) must use same RPM versions.

**Root Cause**: Some architectures built faster and picked newer RPM version released mid-build.

**Fix**: Rebuild component in Konflux. The rebuild will use current RPM versions across all arches.

**Exception**: None - just rebuild.

**Confidence**: 0.95+ if violation explicitly states mismatched versions across arches

---

### 7. Unknown RPM repository ID (sbom_spdx.unknown_repository_id)

**What it is**: RPM repository IDs must use arch-specific format.

**Root Cause**: Using generic repo ID like `ubi-9-baseos-rpms` instead of `ubi-9-for-x86_64-baseos-rpms`.

**Fix**: Update repository IDs in component's Dockerfile to use arch-specific format:
```
[ubi-9-for-x86_64-baseos-rpms]  # not [ubi-9-baseos-rpms]
```

Then rebuild rpms.lock.yaml: https://konflux.pages.redhat.com/docs/users/building/prefetching-dependencies.html#rpm

**Confidence**: 0.95+ if violation shows non-arch-specific repo ID

---

### 8. Deprecated/unsupported task (tasks.task_version_outdated)

**What it is**: Tekton task version will be unsupported as of specified date.

**Root Cause**: Using old task bundle digest that's scheduled for deprecation.

**Fix**: Update task bundle digest in konflux-central to latest version. Check renovate PRs: https://github.com/acme-org/konflux-central/pulls

**Confidence**: 0.95+ if violation includes deprecation date

---

### 9. Missing FIPS check (fips.fbc_fips_check, fips.fbc_fips_check_oci_ta)

**What it is**: FBC (File Based Catalog) fragment must pass FIPS compliance check.

**Root Cause**: FIPS check task disabled on CI (push) builds because it takes 2-4 hours. It only runs on nightly builds.

**Fix**: This is expected for push builds. Check nightly build logs for actual FIPS failures. Ignore if only appearing on CI builds.

**Exception**: Often a false alarm. Check if it appears on nightly Conforma run before investigating.

**Confidence**: 0.70 - often false positive on push builds

---

### 10. Version label mismatch (labels.version_label_mismatch)

**What it is**: Container image version label doesn't match expected version (e.g., v3.4.0-ea1).

**Root Cause**: Image was built before version label was updated for new release.

**Fix**: Rebuild component in Konflux. Fresh build will pick up current version label from conforma-reporter config.

**Confidence**: 0.95+ if violation shows expected vs actual version

---

### 11. FBC target index pruning check (test.fbc_target_index_pruning_check)

**What it is**: FBC fragment prunes correct operator versions from beta channel.

**Root Cause**: Complex - check acme-fbc-fragment build logs for details.

**Fix**:
1. Go to acme-fbc-fragment successful build in Konflux
2. Find fbc-target-index-pruning-check task logs
3. Search for "!FAILURE!" to see what failed
4. If it's a beta channel reset issue, add to SELF-SERVICE FBC EXCEPTION FILE

**Exception**: Some failures are expected (e.g., beta channel resets). Add to exception file: https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/exceptions/fbc-acme-prod.yaml

**Confidence**: 0.60 - requires deep investigation of FBC build logs

---

### 12. False alerts - can usually be ignored

**FBC single component failures**: Usually safe to ignore. FBC FIPS check only runs nightly (not on every push).

**Odh-llama-stack-core-rhel9**: Known issue - component not built by RHOAI, just referenced.

**Odh-vllm-gaudi-v2-25**: Has exception for signing key 05b555b38483c65d.

**Odh-th06-***: Workbench images - known Conforma issue with base image references.

---

## Analysis Guidelines

### Confidence Scoring

- 0.90+ When violation matches a known pattern exactly
- 0.70-0.90 When violation is recognized but context is unclear
- 0.50-0.70 When violation is unfamiliar but you can make educated guess
- Below 0.50 When you need more information - recommend contacting Konflux team

### Auto-fix Assessment

Mark `can_auto_fix: false` for almost all Conforma violations because:
- Most require policy exception approval (human decision)
- Some require legal agreements or architecture changes
- Even mechanical fixes (update task digest) need testing

Only mark `can_auto_fix: true` for:
- Rebuild-only fixes (version mismatch, outdated labels)
- Simple config changes with zero risk (hermetic: true)

### When to Suggest Policy Exception

If violation is:
- **Known and fixable** → Provide the fix, mention exception as alternative if fix is difficult
- **Known but unfixable** → Explain exception process clearly
- **Unknown or unclear** → Suggest checking #konflux-users Slack or filing exception to get ProdSec eyes on it

### Exception Request Process

Always include these details when recommending exception:
1. **What to request**: Specific violation rule to exclude (e.g., `rpm_signature.allowed:0x5b555b38483c65d`)
2. **Where to request**: JIRA link + exception file merge request
3. **Who approves**: @owatkins (ProdSec) in #wg-3_0-openshift-ai-release Slack
4. **What to explain**: Why the violation exists and why it can't be fixed

## Evidence Priority

1. **Violation summary** - shows what failed and why
2. **Violation details (JSON)** - rule name, package/image affected
3. **Commit context** - what changed recently (if violation is new)
4. **Snapshot info** - which images are in this build
5. **Component history** - is this a recurring issue?

## Output Format (CRITICAL)

Use the record_conforma_analysis tool.

**PLAIN TEXT ONLY — NO MARKDOWN:**
- Do NOT use markdown headers (#, ##, ###)
- Do NOT use bold (**text**) or italic (*text*)
- Do NOT use markdown tables (|col1|col2|)
- Do NOT use code blocks (```)
- Do NOT use numbered lists (1., 2., 3.)
- Use ONLY plain text with dash (-) bullet points

**root_cause formatting:**
- Start with a 1-sentence summary stating exactly what you observe
- Follow with 2-4 short paragraphs (2-3 sentences each)
- IMPORTANT: Separate paragraphs with TWO newlines (\\n\\n) for visual spacing
- State only what the evidence directly shows
- Cite the source for every claim:
  * For violations: "Violation rule `sbom_spdx.allowed_package_sources` reports: package X from source Y"
  * For images: "Image `quay.io/acme/component:sha` shows: violation in architecture amd64"

**recommended_fix formatting (CRITICAL - NO NUMBERED LISTS):**
- MUST use bullet points with dash (-) character
- NEVER use numbered lists (1., 2., 3., etc.)
- NEVER use markdown headers (###, ##)
- IMPORTANT: Add blank line (\\n\\n) between each bullet point for readability
- Start each bullet with the action verb
- Include exact file paths, URLs, or commands
- Keep each bullet to 2-3 lines maximum

Example recommended_fix format:
```
- Vendor the model files into a Red Hat-approved internal repository instead of fetching from huggingface.co at build time. The violation details show packages fetched from `https://huggingface.co/docling-project/` which is not an approved source.

- If vendoring is not feasible before the release deadline, request a policy exception. Create a JIRA issue at https://JIRA_CREATE_ISSUE_URL explaining the business justification.

- Add exclusion entries to the exception file at https://GITLAB_INTERNAL_HOST/releng/konflux-release-data for each affected package term.
```

**Evidence rules:**
- Distinguish between "violates policy" and "non-compliant by accident"
- Reference specific rules by name (e.g., sbom_spdx.allowed_package_sources)
- Provide both the immediate fix AND the exception path
- Be specific about which file/package/task is problematic
- If confidence is below 0.70, suggest reaching out to @konflux-users or ProdSec
"""


class ConformaAnalyzer:
    """Analyzes Conforma compliance violations using an LLM provider."""

    def __init__(self, config, db=None, ai_repo=None, llm=None, langfuse=None):
        # type: (CollectorConfig, ...) -> None
        """Initialize analyzer with dependency injection.

        Args:
            config: CollectorConfig with LLM settings
            db: Database connection (created if None)
            ai_repo: AIAnalysisRepository (created if None)
            llm: LLMProvider (created from config if None)
            langfuse: LangfuseTracker (created if None)
        """
        if db is None:
            db = DatabaseConnection(config.db)

        self.config = config
        self.ai_repo = ai_repo or AIAnalysisRepository(db)

        # Create LLM provider from config
        if llm is None:
            if not config.llm:
                raise ValueError("LLM not configured. Set LLM_PROVIDER in .env")
            llm = create_llm_provider(config.llm)
        self.llm = llm

        # Langfuse tracking (disabled if not configured)
        if langfuse is None:
            langfuse_enabled = bool(os.environ.get('LANGFUSE_PUBLIC_KEY'))
            langfuse = LangfuseTracker(enabled=langfuse_enabled)
        self.langfuse = langfuse

    def get_pending_violations(self, limit=5, component_filter=None, force=False):
        # type: (int, Optional[str], bool) -> List[Dict[str, Any]]
        """Get unanalyzed Conforma violations from DB.

        Args:
            limit: Maximum number of violations to fetch
            component_filter: If specified, only get violations for this component
            force: If True, include already-analyzed violations

        Returns:
            List of violation dicts ready for analysis
        """
        return self.ai_repo.get_pending_conforma_violations(
            self.config.k8s.application_name,
            limit=limit,
            component_filter=component_filter,
            force=force
        )

    def build_analysis_prompt(self, violation):
        # type: (Dict[str, Any],) -> Tuple[str, str]
        """Construct system + user prompts from violation data.

        Args:
            violation: Dict with component, violation_summary, scenario, etc.

        Returns:
            Tuple of (system_prompt, user_prompt)
        """
        # Truncate violation summary if too long
        summary = violation.get('violation_summary', '') or ''
        if len(summary) > 80000:
            summary = summary[:80000] + '\n\n... (truncated - summary too long)'

        # Build commit info section (some fields may be None)
        commit_info = []
        if violation.get('commit_sha'):
            commit_info.append("- Commit: {commit_sha}".format(**violation))
        if violation.get('commit_author'):
            commit_info.append("- Commit Author: {commit_author}".format(**violation))
        if violation.get('commit_message'):
            commit_info.append("- Commit Message: {commit_message}".format(**violation))

        commit_section = '\n'.join(commit_info) if commit_info else "- Commit: (not available)"

        user_prompt = """Analyze this Conforma (Enterprise Contract) compliance violation. Focus on identifying which policy is violated and the best path forward — fix vs. exception request.

## Component
- Component: {component}
- Repository: {repository}
- Snapshot: {snapshot}
{commit_info}

## Violation
- PipelineRun: {pipelinerun}
- Scenario: {scenario}
- Violations: {violations}
- Warnings: {warnings}
- Successes: {successes}

## Violation Details
```
{summary}
```

Use the record_conforma_analysis tool. Remember:
- Match against the 14 known patterns (hermetic build, unpinned task, package source, etc.)
- Explain WHICH policy is violated and WHY
- Provide both fix instructions AND exception request guidance
- Be specific: reference exact rules, packages, files from the violation details
- Set confidence based on pattern match strength
- If confidence is below 0.70, recommend contacting #konflux-users or @owatkins in Slack
- Mark can_auto_fix=false unless it's a simple rebuild or zero-risk config change
""".format(
            component=violation.get('component_name', 'unknown'),
            repository=violation.get('repository', 'unknown'),
            snapshot=violation.get('snapshot_name', 'unknown'),
            commit_info=commit_section,
            pipelinerun=violation.get('pipelinerun_name', 'unknown'),
            scenario=violation.get('scenario', 'unknown'),
            violations=violation.get('violations_count', 0),
            warnings=violation.get('warnings_count', 0),
            successes=violation.get('successes_count', 0),
            summary=summary
        )

        return (CONFORMA_SYSTEM_PROMPT, user_prompt)

    def parse_analysis_response(self, llm_response):
        # type: (Any,) -> Dict[str, Any]
        """Extract structured analysis from LLMResponse.tool_calls.

        Pure function with Pydantic validation.

        Args:
            llm_response: LLMResponse with tool_calls

        Returns:
            Dict with root_cause, failure_category, confidence_score, etc.

        Raises:
            ValueError: If tool_calls is empty, malformed, or validation fails
        """
        from pydantic import ValidationError
        from analyzers.models import ConformaAnalysisResult

        if not llm_response.tool_calls:
            raise ValueError("LLM did not return tool_use response")

        # Extract the record_conforma_analysis tool call
        analysis_call = None
        for call in llm_response.tool_calls:
            if call.get('name') == 'record_conforma_analysis':
                analysis_call = call
                break

        if not analysis_call:
            raise ValueError("LLM did not call record_conforma_analysis tool")

        input_data = analysis_call.get('input', {})

        # Validate with Pydantic (catches LLM hallucinations)
        try:
            result = ConformaAnalysisResult(**input_data)
            return result.model_dump()  # Convert back to dict for DB
        except ValidationError as e:
            logger.error("LLM returned invalid Conforma analysis: %s", e)
            logger.error("Raw input_data: %s", input_data)
            # Fall back to permissive dict (for backwards compatibility)
            # but log the validation failure for monitoring
            return {
                'root_cause': input_data.get('root_cause', 'Invalid LLM response'),
                'failure_category': 'infrastructure',  # Safe fallback
                'confidence_score': 0.0,
                'recommended_fix': input_data.get('recommended_fix', 'Manual review required'),
                'recommended_files': input_data.get('recommended_files', []),
                'can_auto_fix': False,
                'requires_human_review': True,
            }

    def analyze_violation(self, violation):
        # type: (Dict[str, Any],) -> Dict[str, Any]
        """Analyze one violation: build prompt -> call LLM -> parse response -> save to DB.

        Args:
            violation: Violation dict from get_pending_violations()

        Returns:
            Dict with analysis results

        Raises:
            Exception: If LLM call or parsing fails
        """
        system_prompt, user_prompt = self.build_analysis_prompt(violation)

        # Create Langfuse trace
        trace = self.langfuse.create_trace(
            name='conforma-violation-analysis',
            input_data={
                'component': violation['component_name'],
                'pipelinerun': violation['pipelinerun_name'],
                'violations': violation['violations_count'],
            },
            metadata={
                'violation_id': violation['id'],
                'scenario': violation.get('scenario'),
            }
        )

        # Call LLM
        start_time = time.time()
        response = self.llm.create_message(
            system=system_prompt,
            user_content=user_prompt,
            tools=[CONFORMA_ANALYSIS_TOOL],
        )
        duration = time.time() - start_time

        # Record in Langfuse
        self.langfuse.record_generation(
            trace,
            name='diagnose-compliance-violation',
            model=self.llm.model_name(),
            prompt=user_prompt[:5000],  # Truncate for Langfuse
            completion=str(response.tool_calls),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            duration_ms=int(duration * 1000),
        )

        # Parse response
        analysis = self.parse_analysis_response(response)

        # Estimate cost (same as build failures)
        cost_usd = (response.input_tokens * 0.000003) + (response.output_tokens * 0.000015)

        # Save to database
        self.ai_repo.insert_analysis(
            conforma_result_id=violation['id'],
            model_used=self.llm.model_name(),
            langfuse_trace_id=getattr(trace, 'id', None) if trace else None,
            tokens_used=response.input_tokens + response.output_tokens,
            cost_usd=cost_usd,
            analysis_duration=duration,
            analysis_json=response.tool_calls,
            **analysis
        )

        # Finalize trace
        self.langfuse.end_trace(trace, output=analysis)

        return analysis

    def run(self, limit=5, component_filter=None, force=False):
        # type: (int, Optional[str], bool) -> Dict[str, Any]
        """Analyze up to `limit` pending Conforma violations.

        Args:
            limit: Maximum number of violations to analyze
            component_filter: If specified, only analyze this component
            force: If True, re-analyze even if already analyzed

        Returns:
            Dict with stats: analyzed count, duration
        """
        start_time = time.time()

        logger.info("=" * 70)
        logger.info("Conforma Violation AI Analysis")
        logger.info("Application: %s", self.config.k8s.application_name)
        if component_filter:
            logger.info("Component filter: %s", component_filter)
        if force:
            logger.info("Force mode: Re-analyzing existing analyses")
        logger.info("=" * 70)

        pending = self.get_pending_violations(limit=limit, component_filter=component_filter, force=force)

        if not pending:
            logger.info("No pending Conforma violations to analyze")
            return {'analyzed': 0, 'duration': 0}

        logger.info("Found %d pending violations", len(pending))

        analyzed = 0
        for i, violation in enumerate(pending, 1):
            logger.info("[%d/%d] %s", i, len(pending), violation['component_name'])
            logger.info("PipelineRun: %s", violation['pipelinerun_name'])
            logger.info("Violations: %d", violation['violations_count'])

            try:
                analysis = self.analyze_violation(violation)
                analyzed += 1

                logger.info("Category: %s (confidence: %.2f)",
                           analysis['failure_category'],
                           analysis['confidence_score'])
                logger.info("Can auto-fix: %s", analysis['can_auto_fix'])
                logger.info("Root cause: %s", analysis['root_cause'][:200])
                logger.info("")

            except Exception as e:
                logger.error("Analysis failed for %s: %s",
                           violation['component_name'], e)

        # Flush Langfuse events
        self.langfuse.flush()

        duration = time.time() - start_time

        logger.info("=" * 70)
        logger.info("Analysis Complete")
        logger.info("=" * 70)
        logger.info("Analyzed: %d/%d violations", analyzed, len(pending))
        logger.info("Duration: %.1fs", duration)

        return {'analyzed': analyzed, 'duration': duration}
