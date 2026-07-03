"""Build failure analyzer using LLM provider.

Orchestrates AI analysis of build failures: fetches pending failures,
constructs prompts, calls LLM, parses responses, stores results.

Follows the collector pattern: thin orchestration delegating to clients
(LLMProvider) and repositories.
"""

import json
import os
import re
import time

from logger import setup_logger
from repositories import DatabaseConnection, BuildFailureRepository, AIAnalysisRepository, ErrorPatternRepository
from clients.blob_store import get_blob_store, make_blob_key, should_offload
from clients.llm_provider import create_llm_provider
from clients.langfuse_tracker import LangfuseTracker
from prompt_loader import load_prompt

logger = setup_logger(__name__)


# Tool schema for structured output from Claude
ANALYSIS_TOOL = {
    'name': 'record_analysis',
    'description': 'Record the root cause analysis of a CI build failure',
    'input_schema': {
        'type': 'object',
        'properties': {
            'root_cause': {
                'type': 'string',
                'description': 'Clear explanation of what caused the failure'
            },
            'failure_category': {
                'type': 'string',
                'enum': [
                    'dependency_issue',
                    'build_error',
                    'test_failure',
                    'resource_limit',
                    'config_error',
                    'git_sync_issue',
                    'infrastructure'
                ],
                'description': 'Category of failure'
            },
            'confidence_score': {
                'type': 'number',
                'minimum': 0,
                'maximum': 1,
                'description': 'Confidence in this analysis (0.0-1.0)'
            },
            'recommended_fix': {
                'type': 'string',
                'description': 'Specific fix recommendation'
            },
            'recommended_files': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': 'Files that need to be modified'
            },
            'can_auto_fix': {
                'type': 'boolean',
                'description': 'Whether this can be automatically fixed'
            },
            'requires_human_review': {
                'type': 'boolean',
                'description': 'Whether human review is needed before fixing'
            },
            'evidence_references': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'type': {'type': 'string', 'enum': ['doc', 'config', 'log', 'policy']},
                        'url': {'type': 'string', 'description': 'URL to the resource'},
                        'description': {'type': 'string', 'description': 'What this reference shows'},
                    },
                    'required': ['type', 'description']
                },
                'description': 'Links to docs, config files, policy YAML, or log evidence supporting the diagnosis'
            },
            'source_transparency': {
                'type': 'object',
                'properties': {
                    'sources_consulted': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Data sources actually used (e.g., "build logs", "commit diff", "Dockerfile")'
                    },
                    'sources_unavailable': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Sources attempted but not available (e.g., "Tekton config not provided", "pre-build logs truncated")'
                    },
                    'limitations': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Factors that could change the diagnosis (e.g., "log was truncated, earlier errors may exist")'
                    },
                },
                'description': 'Academic-style transparency: what was used, what was missing, and analysis limitations'
            },
            'differential_diagnosis': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'hypothesis': {'type': 'string', 'description': 'One-sentence explanation'},
                        'category': {'type': 'string', 'description': 'failure_category enum value'},
                        'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1},
                        'supporting_evidence': {'type': 'array', 'items': {'type': 'string'}},
                        'contradicting_evidence': {'type': 'array', 'items': {'type': 'string'}},
                    },
                    'required': ['hypothesis', 'category', 'confidence']
                },
                'description': '2-3 competing hypotheses ranked by evidence strength. First = primary diagnosis.'
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


SYSTEM_PROMPT = load_prompt('build_failure_analyzer')


class BuildFailureAnalyzer:
    """Analyzes build failures using an LLM provider."""

    def __init__(self, config, db=None, build_repo=None,
                 ai_repo=None, llm=None, langfuse=None, pattern_service=None,
                 github_client=None):
        if db is None:
            db = DatabaseConnection(config.db)

        self.config = config
        self.db = db
        self._github_client = github_client
        self._cheap_llm = None
        self.build_repo = build_repo or BuildFailureRepository(db)
        self.ai_repo = ai_repo or AIAnalysisRepository(db)
        self.pattern_repo = ErrorPatternRepository(db)

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

        # Pattern matching service (for confidence boosting and pattern context)
        if pattern_service is None:
            from patterns.category_matcher import CategoryBasedMatcher
            from patterns.pattern_matching_service import PatternMatchingService
            matcher = CategoryBasedMatcher(self.pattern_repo)
            pattern_service = PatternMatchingService(matcher, self.pattern_repo)
        self.pattern_service = pattern_service

    def get_pending_failures(self, limit=5, component_filter=None, force=False, application=None):
        """Get unanalyzed failures from DB.

        Args:
            limit: Maximum number of failures to fetch
            component_filter: If specified, only get failures for this component
            force: If True, include already-analyzed failures
            application: Override config app. None uses config default.

        Returns:
            List of failure dicts ready for analysis
        """
        return self.ai_repo.get_pending_failures(
            application or self.config.k8s.application_name,
            limit=limit,
            component_filter=component_filter,
            force=force
        )

    def _ensure_context(self, failure):
        """Fetch missing commit context from GitHub if DB data is incomplete.

        Checks commit_context for completeness (commit diff, tekton configs).
        If incomplete and we have repository_url + commit_sha, fetches live
        from GitHub and updates both the in-memory dict and the DB.
        """
        commit_ctx = failure.get('commit_context')

        if isinstance(commit_ctx, str):
            try:
                commit_ctx = json.loads(commit_ctx)
            except (json.JSONDecodeError, TypeError):
                commit_ctx = None

        has_commit = commit_ctx and commit_ctx.get('commit') is not None

        if has_commit:
            return

        repo_url = failure.get('repository_url')
        sha = failure.get('commit_sha')
        branch = failure.get('branch')

        if not repo_url or not sha:
            return

        if self._github_client is None:
            token = getattr(self.config, 'github_token', None) or os.environ.get('GITHUB_TOKEN')
            if not token:
                logger.warning("No GitHub token — cannot live-fetch context for %s", sha[:8])
                return
            from clients.github_client import GitHubClient
            self._github_client = GitHubClient(token)

        logger.info("Live-fetching context from GitHub for %s@%s", repo_url, sha[:8])
        ctx = self._github_client.get_commit_context(repo_url, sha, branch)

        if not ctx:
            return

        failure['commit_context'] = ctx

        try:
            self._store_field(failure, 'commit_context', ctx)
            logger.info("Stored live-fetched context for failure %s", failure['id'])
        except Exception as e:
            logger.warning("Failed to store context in DB: %s", e)

    def _ensure_enrichment(self, failure):
        """Run enrichment if not yet done for this failure."""
        if failure.get('enriched_context'):
            return

        try:
            from enrichment.enrichment_orchestrator import EnrichmentOrchestrator
            from enrichment.sources.dependency_context import DependencyContextSource
            from enrichment.sources.related_failures import RelatedFailuresSource
            from enrichment.sources.build_history import BuildHistorySource
            from enrichment.sources.open_prs import OpenPRsSource

            orchestrator = EnrichmentOrchestrator(self.config, self.db)
            orchestrator.register_source(DependencyContextSource(self.config))
            orchestrator.register_source(RelatedFailuresSource(self.config, self.db))
            orchestrator.register_source(BuildHistorySource(self.config, self._github_client))
            orchestrator.register_source(OpenPRsSource(self.config, self._github_client))

            logger.info("Auto-enriching context for %s", failure.get('component_name'))
            result = orchestrator.enrich_failure(failure)

            if result.success:
                logger.info("Enrichment succeeded: %d/%d sources",
                            result.sources_succeeded, result.sources_attempted)
                with self.db.connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT enriched_context FROM build_failures WHERE id = %s",
                        (failure['id'],)
                    )
                    row = cursor.fetchone()
                    if row and row[0]:
                        failure['enriched_context'] = row[0]
            else:
                logger.info("Enrichment found no additional context for %s",
                            failure.get('component_name'))
        except Exception as e:
            logger.warning("Auto-enrichment failed: %s", e)

    _BLOB_FIELDS = frozenset({'build_logs', 'commit_context'})

    def _store_field(self, failure, field, data):
        """Write a field to DB, offloading to blob store if above threshold."""
        if field not in self._BLOB_FIELDS:
            raise ValueError("invalid field for blob storage: {}".format(field))
        serialized = json.dumps(data) if not isinstance(data, str) else data
        with self.db.connection() as conn:
            cursor = conn.cursor()
            if should_offload(serialized):
                ext = 'json' if not isinstance(data, str) else 'txt'
                key = make_blob_key('build-failures', failure['component_name'],
                                    failure['pipelinerun_name'], field, ext)
                get_blob_store().put(key, serialized)
                cursor.execute(
                    """UPDATE build_failures
                       SET {field} = NULL,
                           blob_refs = COALESCE(blob_refs, '{{}}') || %s::jsonb
                       WHERE id = %s""".format(field=field),
                    (json.dumps({field: key}), failure['id'])
                )
            else:
                cursor.execute(
                    "UPDATE build_failures SET {field} = %s WHERE id = %s".format(field=field),
                    (serialized, failure['id'])
                )

    def _ensure_logs(self, failure):
        """Process logs so the analyzer sees the actual error, not noise.

        Four escalating steps:
        1. Structural parsing: extract only the failed step section
        2. Live re-fetch from Tekton Results if logs are truncated
        3. Error keyword filtering: keep only error lines + context
        4. AI extraction via Haiku if still too large
        """
        logs = failure.get('build_logs') or ''
        failed_task = failure.get('failed_task_name') or ''

        # Step 0: Fetch per-TaskRun logs when we'd benefit from more specific data:
        # - no logs, task-specific failure, truncated, or missing platform info
        error_msg = failure.get('error_message') or ''
        is_task_specific_failure = any(t in error_msg.lower() for t in
                                       ('fips-check', 'init-task', 'sast-', 'clair-'))
        is_truncated_build = len(logs) >= 199000
        already_has_platform = '=== Per-platform build status ===' in logs
        if not logs or is_task_specific_failure or is_truncated_build or not already_has_platform:
            taskrun_logs = self._fetch_failed_taskrun_logs(failure)
            if taskrun_logs:
                logs = taskrun_logs

        if not logs:
            return

        original_len = len(logs)

        # Step 1: Extract only the failed step section
        if failed_task:
            section = self._extract_failed_section(logs, failed_task)
            if section and len(section) > 100:
                logger.info("Extracted failed step section '%s': %d -> %d chars",
                            failed_task, len(logs), len(section))
                logs = section

        # Step 2: Re-fetch if truncated
        is_truncated = original_len >= 199000
        section_looks_cut = logs.rstrip() != '' and not logs.rstrip().endswith('\n')

        if is_truncated and section_looks_cut:
            refetched = self._refetch_logs(failure, failed_task)
            if refetched:
                logs = refetched

        # Step 3: Error keyword filtering
        MAX_LOG_CHARS = 100000
        if len(logs) > MAX_LOG_CHARS:
            filtered = self._filter_error_lines(logs)
            if filtered and len(filtered) >= 200:
                logger.info("Keyword filtering: %d -> %d chars", len(logs), len(filtered))
                logs = filtered

        # Step 4: AI extraction if still too large
        if len(logs) > MAX_LOG_CHARS:
            extracted = self._ai_extract_error(logs, failure)
            if extracted:
                logs = extracted
            else:
                head_size = 5000
                tail_size = MAX_LOG_CHARS - head_size
                omitted = len(logs) - MAX_LOG_CHARS
                logs = (logs[:head_size]
                        + '\n\n... ({} chars omitted) ...\n\n'.format(omitted)
                        + logs[-tail_size:])

        failure['build_logs'] = logs

    def _extract_failed_section(self, logs, task_name):
        """Extract log section for the failed TaskRun using markers."""
        pattern = r'(===== TaskRun: [^/]*{task}[^/]* /.*?(?=\n=====|$))'.format(
            task=re.escape(task_name))
        matches = re.findall(pattern, logs, re.DOTALL)
        if matches:
            return '\n'.join(matches)
        return None

    def _refetch_logs(self, failure, failed_task=''):
        """Re-fetch logs from Tekton Results with a higher size limit."""
        pr_name = failure.get('pipelinerun_name')
        if not pr_name:
            return None

        try:
            from clients.tekton_results import TektonResultsClient
            ns = self.config.k8s.namespace
            tr = TektonResultsClient(namespace=ns)
            logger.info("Re-fetching logs from Tekton Results for %s (500KB limit)", pr_name)
            full_logs = tr.get_pipelinerun_logs(pr_name, max_log_size=500000, failed_only=True)
        except Exception as e:
            logger.warning("Failed to re-fetch logs: %s", e)
            return None

        if not full_logs:
            full_logs = self._fetch_oci_logs(failure)
            if full_logs:
                logger.info("Fetched logs from OCI artifact for %s", pr_name)

        if not full_logs:
            return None

        logger.info("Re-fetched %d chars (was %d in DB)",
                     len(full_logs), len(failure.get('build_logs', '')))

        try:
            self._store_field(failure, 'build_logs', full_logs)
            logger.info("Updated re-fetched logs for failure %s", failure['id'])
        except Exception as e:
            logger.warning("Failed to update logs in DB: %s", e)

        # Extract the failed step section from the fuller logs
        if failed_task:
            section = self._extract_failed_section(full_logs, failed_task)
            if section and len(section) > 100:
                return section

        return full_logs

    def _fetch_oci_logs(self, failure):
        """Fetch logs from OCI registry artifact as fallback when Tekton Results fails."""
        pr_name = failure.get('pipelinerun_name')
        component = failure.get('component_name')
        if not pr_name or not component:
            return None

        try:
            from clients.kubernetes import KubernetesClient
            kc = KubernetesClient(namespace=self.config.k8s.namespace)
            meta = kc.get_component_metadata(component)
            if not meta or not meta.get('container_image'):
                return None

            from clients.registry_client import RegistryClient
            registry, repository, _ = RegistryClient.parse_image_ref(meta['container_image'])
            rc = RegistryClient()

            logs = rc.fetch_log_artifact(registry, repository, pr_name)
            if not logs:
                return None

            failed_task = failure.get('failed_task_name', '')
            if failed_task:
                for marker in ('--- LOGS FOR', '===== TaskRun:'):
                    pattern = r'(?:{}[^\n]*{}[^\n]*\n)(.*?)(?=\n{}|\Z)'.format(
                        re.escape(marker), re.escape(failed_task), re.escape(marker))
                    match = re.search(pattern, logs, re.DOTALL)
                    if match:
                        return match.group(1).strip()

            return logs
        except Exception as e:
            logger.debug("OCI log fetch failed for %s: %s", pr_name, e)
            return None

    def _fetch_sarif_for_failure(self, failure):
        """Fetch SARIF scan results if this is a scan-related failure."""
        error_msg = (failure.get('error_message') or '').lower()
        failed_task = (failure.get('failed_task_name') or '').lower()
        is_scan = any(kw in error_msg or kw in failed_task
                      for kw in ('clair', 'sast', 'scan', 'vulnerability'))
        if not is_scan:
            return ''

        component = failure.get('component_name')
        if not component:
            return ''

        try:
            from clients.kubernetes import KubernetesClient
            kc = KubernetesClient(namespace=self.config.k8s.namespace)
            meta = kc.get_component_metadata(component)
            if not meta or not meta.get('container_image'):
                return ''

            from clients.registry_client import RegistryClient
            image = meta['container_image']
            registry, repository, tag_or_digest = RegistryClient.parse_image_ref(image)

            rc = RegistryClient()
            if not tag_or_digest.startswith('sha256:'):
                return ''

            results = rc.fetch_sarif_results(registry, repository, tag_or_digest)
            return RegistryClient.format_sarif_summary(results)
        except Exception as e:
            logger.debug("SARIF fetch failed for %s: %s", component, e)
            return ''

    def _fetch_failed_taskrun_logs(self, failure):
        """Fetch logs for the specific failed TaskRun via Tekton Results.

        For multi-arch builds, also adds a per-platform status summary so
        the analyzer knows which architectures passed and which failed.
        """
        pr_name = failure.get('pipelinerun_name')
        if not pr_name:
            return None
        try:
            from clients.tekton_results import TektonResultsClient
            ns = self.config.k8s.namespace
            tr = TektonResultsClient(namespace=ns)

            taskruns = tr.query_taskrun_records(pr_name)

            failed_logs = None
            platform_summary = []
            test_outputs = []
            test_warnings = []

            for td, record_name in taskruns:
                task = td.get('metadata', {}).get('labels', {}).get(
                    'tekton.dev/pipelineTask', '')
                conditions = td.get('status', {}).get('conditions', [])
                if not conditions:
                    continue
                last_cond = conditions[-1]
                succeeded = last_cond.get('status') == 'True'

                params = td.get('spec', {}).get('params', [])
                platform = ''
                for p in params:
                    if p.get('name') == 'PLATFORM':
                        platform = p.get('value', '')

                if platform:
                    status_str = 'PASSED' if succeeded else 'FAILED'
                    platform_summary.append('{}: {} ({})'.format(
                        platform, status_str, task))

                for res in td.get('status', {}).get('results', []):
                    name = res.get('name', '')
                    if name in ('TEST_OUTPUT', 'SCAN_OUTPUT', 'IMAGES_PROCESSED'):
                        try:
                            data = json.loads(res.get('value', '{}'))
                            result = data.get('result', '')
                            note = data.get('note', '')
                            if result and result != 'SUCCESS':
                                entry = '{}{}: {} — {}'.format(
                                    task,
                                    ' [{}]'.format(platform) if platform else '',
                                    result, note[:500])
                                if result == 'WARNING':
                                    test_warnings.append(entry)
                                else:
                                    test_outputs.append(entry)
                        except (json.JSONDecodeError, TypeError):
                            pass

                if not succeeded and not failed_logs:
                    logs = tr.get_taskrun_logs(record_name)
                    if not logs:
                        msg = last_cond.get('message', '')
                        reason = last_cond.get('reason', '')
                        if msg:
                            logs = '{}: {}'.format(reason, msg)
                    if logs:
                        failed_logs = logs
                        logger.info("Fetched TaskRun logs for '%s' (%s): %d chars",
                                    task, platform or 'no-platform', len(logs))

            sarif_summary = self._fetch_sarif_for_failure(failure)

            if failed_logs:
                header_parts = []
                if sarif_summary:
                    header_parts.append(sarif_summary)
                    header_parts.append('')
                if test_outputs:
                    header_parts.append('=== Structured Test Results (FAILURES) ===')
                    header_parts.extend(test_outputs)
                    header_parts.append('')
                if test_warnings:
                    header_parts.append('=== Structured Test Results (WARNINGS) ===')
                    header_parts.extend(test_warnings)
                    header_parts.append('')
                if platform_summary:
                    header_parts.append('=== Per-platform build status ===')
                    header_parts.extend(sorted(platform_summary))
                    header_parts.append('')
                if header_parts:
                    header_parts.append('=== Failed TaskRun logs ===')
                    failed_logs = '\n'.join(header_parts) + '\n' + failed_logs

                failure['build_logs'] = failed_logs
                self._store_field(failure, 'build_logs', failed_logs)
                return failed_logs
        except Exception as e:
            logger.warning("Failed to fetch TaskRun logs: %s", e)
        return None

    _ERROR_KEYWORDS = re.compile(
        r'error[:\s]|fatal|failed|failure|exit code|exit \d|traceback|exception|cannot\s|'
        r'warning[:\s]|warn[:\s]|deprecated|'
        r'denied|timeout|killed|oom|no such|not found|permission denied|command not found|'
        r'segmentation fault|segfault|sigsegv|sigkill|sigterm|signal \d|abort|core dump|'
        r'panic|refused|rejected|conflict|broken|missing|undefined|unresolved|'
        r'out of memory|no space|disk full|quota exceeded|import error|module.*not found|'
        r'skipping step because',
        re.IGNORECASE
    )

    def _filter_error_lines(self, logs, context_lines=20):
        """Keep only lines matching error keywords plus surrounding context."""
        from utils.log_filter import filter_error_lines
        result = filter_error_lines(logs, context_lines)
        return result if result != logs else None

    def _ai_extract_error(self, logs, failure):
        """Use a cheap LLM (Haiku) to extract error-relevant log lines."""
        if self._cheap_llm is None:
            try:
                from clients.llm_provider import create_llm_provider
                self._cheap_llm = create_llm_provider(self.config.llm)
            except Exception as e:
                logger.warning("Cannot create LLM for log extraction: %s", e)
                return None

        prompt = (
            "Extract ONLY the error-relevant portion from these build logs. "
            "Include 20 lines of context before and after each error. "
            "Return ONLY the extracted log lines with no commentary or explanation.\n\n"
            "Component: {component}\n"
            "Failed step: {step}\n"
            "Error message: {error}\n\n"
            "BUILD LOGS:\n{logs}"
        ).format(
            component=failure.get('component_name', ''),
            step=failure.get('failed_step_name', ''),
            error=failure.get('error_message', ''),
            logs=logs[:200000],
        )

        try:
            logger.info("AI log extraction via Haiku (%d chars input)", len(logs))
            response = self._cheap_llm.create_message(
                system="You extract error-relevant sections from CI build logs. Return only log lines.",
                user_content=prompt,
                max_tokens=8192,
            )
            extracted = response.content_text.strip()
            if extracted and len(extracted) > 100:
                logger.info("AI extraction: %d -> %d chars", len(logs), len(extracted))
                return extracted
        except Exception as e:
            logger.warning("AI log extraction failed: %s", e)

        return None

    def _get_dependency_updates(self, failure):
        """Fetch recent MintMaker dependency updates for correlation."""
        component = failure.get('component_name')
        if not component:
            return ''
        try:
            from clients.konflux_client import KonfluxClient
            kc = KonfluxClient(namespace=self.config.k8s.namespace)
            updates = kc.get_dependency_updates(component_filter=component, hours=48)
            if not updates:
                return ''
            lines = ['\n## Recent Dependency Updates (MintMaker)']
            for u in updates[:10]:
                merged = ' (merged)' if u.get('merged') else ' (pending)'
                lines.append('- {}: {} {} → {}{}'.format(
                    u.get('created', '')[:16],
                    u.get('package', 'unknown'),
                    u.get('from_version', '?'),
                    u.get('to_version', '?'),
                    merged,
                ))
                if u.get('pr_url'):
                    lines.append('  PR: {}'.format(u['pr_url']))
            lines.append('')
            return '\n'.join(lines)
        except Exception as e:
            logger.debug("Dependency update fetch failed: %s", e)
            return ''

    def build_analysis_prompt(self, failure):
        """Construct system + user prompts from failure data.

        Pure function extracted for testability.
        Includes commit context (diff, Dockerfile, .tekton/ configs) when available.

        Args:
            failure: Dict with component, error_message, logs, commit info

        Returns:
            Tuple of (system_prompt, user_prompt)
        """
        logs = failure.get('build_logs', '') or ''

        # Build commit context section (includes enriched_context if available)
        commit_context_section = self._format_commit_context(
            failure.get('commit_context'),
            failure.get('enriched_context')
        )

        # Known-pattern context from previous occurrences (top 3 patterns)
        pattern_section = self.pattern_service.get_matches_for_prompt(failure)

        dep_updates_section = self._get_dependency_updates(failure)

        # Targeted knowledge graph context (fails silently if Neo4j unavailable)
        try:
            from utils.graph_context import build_context
            graph_section = build_context(failure)
        except Exception:
            graph_section = ""

        user_prompt = """Analyse this CI build failure. Focus on identifying what changed and why it broke — don't just restate the error message.

## Component
- Component: {component}
- Repository: {repository}
- Branch: {branch}
- Commit: {commit_sha}
- Commit Author: {commit_author}
- Commit Message: {commit_message}

## Failure
- PipelineRun: {pipelinerun}
- Failed Task: {failed_task}
- Failed Step: {failed_step}
- Error Type: {error_type}
- Error Message: {error_message}
{commit_context}{pattern_section}{dep_updates}{graph_context}
## Build Logs
```
{logs}
```

Use the record_analysis tool. Remember:
- State ONLY what you observe in the evidence - do not infer or speculate
- Quote exact text from logs, diffs, and configs
- Cite the source for every claim (file name, line number, log section)
- Set confidence based on evidence quality: high evidence = high confidence
- If confidence is below 0.6, suggest contacting {commit_author} for clarification
- If Recent Dependency Updates are listed, check if the failure correlates with a recent package bump

CRITICAL FORMATTING RULES:
1. root_cause field:
   - Line 1: One-sentence summary of what the evidence shows
   - Line 2: Empty line (\n\n)
   - Paragraph 1: What the commit diff shows (quote exact changes)
   - Line: Empty line (\n\n)
   - Paragraph 2: What the logs show (quote exact errors)
   - Line: Empty line (\n\n)
   - Paragraph 3: What the configs show (cite files and values)

2. recommended_fix field:
   - Bullet with dash: "- First action. Source: file X line Y."
   - Empty line (\n\n)
   - Bullet with dash: "- Second action. Evidence: log shows `error`."
   - Empty line (\n\n)
   - Continue pattern for all steps

3. Evidence citations (use this exact format):
   - File changes: "Commit diff `.tekton/file.yaml` line 42: removed `old` added `new`"
   - Log errors: "Build log line 156: `ERROR: module not found`"
   - Config values: "File `Dockerfile` line 10: `FROM registry/image:tag`"

4. Include evidence_references with links to docs, config files, or log evidence.
5. Include source_transparency: list what data you actually used, what was missing, and what limitations affect your diagnosis.

## Reference Documentation
Use these URLs in evidence_references when relevant:
- Build troubleshooting: https://konflux-ci.dev/docs/troubleshooting/builds/
- Hermetic builds: https://konflux-ci.dev/docs/building/hermetic-builds/
- Prefetching dependencies: https://konflux-ci.dev/docs/building/prefetching-dependencies/
- Customizing the build pipeline: https://konflux-ci.dev/docs/building/customizing-the-build/
- Build pipeline tasks: https://konflux-ci.dev/docs/testing/build/
{dynamic_urls}
""".format(
            component=failure.get('component_name', 'unknown'),
            repository=failure.get('repository', 'unknown'),
            branch=failure.get('branch', 'unknown'),
            commit_sha=failure.get('commit_sha', 'unknown'),
            commit_author=failure.get('commit_author', 'unknown'),
            commit_message=failure.get('commit_message', 'unknown'),
            pipelinerun=failure.get('pipelinerun_name', 'unknown'),
            failed_task=failure.get('failed_task_name', 'unknown'),
            failed_step=failure.get('failed_step_name', 'unknown'),
            error_type=failure.get('error_type', 'unknown'),
            error_message=failure.get('error_message', 'unknown'),
            commit_context=commit_context_section,
            pattern_section=pattern_section,
            dep_updates=dep_updates_section,
            graph_context=graph_section,
            logs=logs,
            dynamic_urls=self._build_dynamic_urls(failure),
        )

        return (SYSTEM_PROMPT, user_prompt)

    def _build_dynamic_urls(self, failure):
        """Construct component-specific URLs for the AI to use in evidence_references."""
        urls = []
        repo_url = failure.get('repository_url', '') or failure.get('repository', '')
        sha = failure.get('commit_sha', '')
        if repo_url and sha and len(sha) >= 7:
            repo_url = repo_url.rstrip('/')
            if repo_url.endswith('.git'):
                repo_url = repo_url[:-4]
            urls.append('- Component .tekton config: {}/blob/{}/.tekton/'.format(repo_url, sha[:12]))
            urls.append('- Component Containerfile: {}/blob/{}/Containerfile'.format(repo_url, sha[:12]))
            urls.append('- Commit diff: {}/commit/{}'.format(repo_url, sha))
        elif repo_url:
            urls.append('- Component repo: {}'.format(repo_url))
        return '\n'.join(urls)

    def _annotate_fix_status(self, analysis, failure):
        """Append fix-status notes when commit context shows a fix may already exist."""
        notes = []
        commit_context = failure.get('commit_context')
        if commit_context and isinstance(commit_context, str):
            try:
                commit_context = json.loads(commit_context)
            except (json.JSONDecodeError, TypeError):
                commit_context = None

        if commit_context:
            commit = commit_context.get('commit', {})
            msg = (commit.get('message') or '').lower()
            if any(kw in msg for kw in ['fix', 'revert', 'hotfix', 'patch']):
                notes.append('- A recent commit ({}) mentions a fix — this failure may already be addressed'.format(
                    commit.get('sha', '?')[:8]))

        triage = failure.get('triage_items') or []
        active = [t for t in triage if t.get('status') == 'active']
        if active:
            notes.append('- Triage item #{} is actively tracking this failure'.format(
                active[0].get('id', '?')))

        if notes:
            fix_status = '\n\nFix Status (auto-detected):\n' + '\n\n'.join(notes)
            analysis['recommended_fix'] = analysis.get('recommended_fix', '') + fix_status

        return analysis

    def _format_commit_context(self, commit_context, enriched_context=None):
        """Format commit context and enriched context into prompt text."""
        if not commit_context:
            return "\n## Commit Context\n(Not available — commit diff not fetched yet)\n"

        if isinstance(commit_context, str):
            try:
                commit_context = json.loads(commit_context)
            except (json.JSONDecodeError, TypeError):
                return "\n## Commit Context\n(Not available)\n"

        if enriched_context and isinstance(enriched_context, str):
            try:
                enriched_context = json.loads(enriched_context)
            except (json.JSONDecodeError, TypeError):
                enriched_context = None

        sections = ["\n## Commit Context"]

        # Commit diff
        commit = commit_context.get('commit')
        if commit:
            files = commit.get('files', [])
            stats = commit.get('stats', {})
            sections.append(
                "\n### Commit Diff ({} files changed, +{} -{})".format(
                    len(files),
                    stats.get('additions', 0),
                    stats.get('deletions', 0),
                )
            )
            for f in files:
                patch = f.get('patch', '')
                if patch and patch != '(diff truncated — total diff too large)':
                    sections.append(
                        "\n**{}** ({}, +{} -{}):\n```diff\n{}\n```".format(
                            f['filename'], f.get('status', ''),
                            f.get('additions', 0), f.get('deletions', 0),
                            patch,
                        )
                    )
                else:
                    sections.append(
                        "\n**{}** ({}, +{} -{})".format(
                            f['filename'], f.get('status', ''),
                            f.get('additions', 0), f.get('deletions', 0),
                        )
                    )

        # PR info
        pr = commit_context.get('pr')
        if pr:
            sections.append(
                "\n### Pull Request #{}: {}".format(
                    pr.get('number', '?'), pr.get('title', '')
                )
            )
            body = pr.get('body', '')
            if body:
                if len(body) > 3000:
                    body = body[:3000] + '...'
                sections.append(body)

        # Dockerfile
        dockerfile = commit_context.get('dockerfile')
        if dockerfile:
            sections.append(
                "\n### Dockerfile ({})\n```dockerfile\n{}\n```".format(
                    dockerfile.get('path', 'Dockerfile'),
                    dockerfile.get('content', ''),
                )
            )

        # Tekton configs
        tekton = commit_context.get('tekton_configs', {})
        if tekton:
            sections.append("\n### Tekton Pipeline Configs")
            for fname, content in tekton.items():
                if len(content) > 5000:
                    content = content[:5000] + '\n... (truncated)'
                sections.append(
                    "\n**{}**:\n```yaml\n{}\n```".format(fname, content)
                )

        # Enriched context (dependency changes + related failures)
        if enriched_context:
            dep_changes = enriched_context.get('dependency_changes')
            if dep_changes:
                sections.append("\n### Dependency File Changes")
                for fname, change in dep_changes.items():
                    patch = change.get('patch', '')
                    sections.append(
                        "\n**{}** ({}, +{} -{})".format(
                            fname,
                            change.get('status', 'modified'),
                            change.get('additions', 0),
                            change.get('deletions', 0)
                        )
                    )
                    if patch:
                        sections.append("```diff\n{}\n```".format(patch))

            related = enriched_context.get('related_failures')
            if related:
                sections.append("\n### Related Recent Failures (last 7 days)")
                for rf in related:
                    analyzed_status = "analyzed" if rf.get('ai_analyzed') else "pending"
                    category = rf.get('failure_category', '')
                    category_label = " ({})".format(category) if category else ""
                    sections.append(
                        "- **{}**: {} - {}{}  [{}]".format(
                            rf.get('component_name', ''),
                            rf.get('error_type', ''),
                            rf.get('error_message', '')[:150],
                            category_label,
                            analyzed_status
                        )
                    )
                    if rf.get('root_cause'):
                        sections.append("  Previous root cause: {}".format(
                            rf['root_cause'][:200]
                        ))

            resolved = enriched_context.get('resolved_examples')
            if resolved:
                sections.append("\n### Previously Resolved Similar Failures")
                sections.append("These similar failures were fixed. Use them as reference:")
                for rx in resolved:
                    sections.append(
                        "\n- **{}** ({}): {}".format(
                            rx.get('component_name', ''),
                            rx.get('error_type', ''),
                            rx.get('error_message', '')[:150],
                        )
                    )
                    if rx.get('root_cause'):
                        sections.append("  Root cause: {}".format(rx['root_cause'][:200]))
                    if rx.get('recommended_fix'):
                        sections.append("  Fix applied: {}".format(rx['recommended_fix'][:200]))
                    if rx.get('commit_url'):
                        sections.append("  Resolution commit: {}".format(rx['commit_url']))

            open_prs = enriched_context.get('open_prs')
            if open_prs:
                sections.append("\n### Open PRs Against This Branch")
                sections.append("These PRs are currently open against the component's branch:")
                for pr in open_prs:
                    merged_label = ' (merged)' if pr.get('merged') else ''
                    sections.append(
                        "- PR #{}: {} by {} {}{}".format(
                            pr.get('number', '?'),
                            pr.get('title', ''),
                            pr.get('author', ''),
                            pr.get('url', ''),
                            merged_label,
                        )
                    )

        return '\n'.join(sections) + '\n'


    def parse_analysis_response(self, llm_response):
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
        from analyzers.models import AnalysisResult

        if not llm_response.tool_calls:
            raise ValueError("LLM did not return tool_use response")

        # Extract the record_analysis tool call
        analysis_call = None
        for call in llm_response.tool_calls:
            if call.get('name') == 'record_analysis':
                analysis_call = call
                break

        if not analysis_call:
            raise ValueError("LLM did not call record_analysis tool")

        input_data = analysis_call.get('input', {})

        # Validate with Pydantic (catches LLM hallucinations)
        try:
            result = AnalysisResult(**input_data)
            return result.model_dump()  # Convert back to dict for DB
        except ValidationError as e:
            logger.error("LLM returned invalid analysis: %s", e)
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

    def analyze_failure(self, failure):
        """Analyze one failure: build prompt -> call LLM -> parse response -> save to DB.

        Args:
            failure: Failure dict from get_pending_failures()

        Returns:
            Dict with analysis results

        Raises:
            Exception: If LLM call or parsing fails
        """
        self._ensure_context(failure)
        self._ensure_enrichment(failure)
        self._ensure_logs(failure)

        system_prompt, user_prompt = self.build_analysis_prompt(failure)

        # Create Langfuse trace
        trace = self.langfuse.create_trace(
            name='build-failure-analysis',
            input_data={
                'component': failure['component_name'],
                'pipelinerun': failure['pipelinerun_name'],
            },
            metadata={
                'failure_id': failure['id'],
                'error_type': failure.get('error_type'),
            }
        )

        # Call LLM
        start_time = time.time()
        response = self.llm.create_message(
            system=system_prompt,
            user_content=user_prompt,
            tools=[ANALYSIS_TOOL],
        )
        duration = time.time() - start_time

        # Record in Langfuse
        self.langfuse.record_generation(
            trace,
            name='diagnose-root-cause',
            model=self.llm.model_name(),
            prompt=user_prompt[:5000],  # Truncate for Langfuse
            completion=str(response.tool_calls),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            duration_ms=int(duration * 1000),
        )

        # Parse response
        analysis = self.parse_analysis_response(response)
        analysis = self._annotate_fix_status(analysis, failure)

        # Apply pattern confidence boost if applicable
        enhancement = self.pattern_service.enhance_analysis(
            failure=failure,
            llm_confidence=analysis['confidence_score'],
            llm_category=analysis['failure_category']
        )

        # Store boost metadata separately
        pattern_boost_metadata = None
        if enhancement.boost_applied:
            # Update confidence score with boosted value
            analysis['confidence_score'] = enhancement.boosted_confidence
            # Store boost metadata for transparency
            pattern_boost_metadata = {
                'original_confidence': enhancement.original_confidence,
                'boosted_confidence': enhancement.boosted_confidence,
                'boost_amount': enhancement.boost_amount,
                'pattern_id': enhancement.matched_patterns[0].pattern_id if enhancement.matched_patterns else None,
                'pattern_name': enhancement.matched_patterns[0].pattern_name if enhancement.matched_patterns else None
            }

        # Estimate cost (rough approximation: $3/MTok input, $15/MTok output for Claude Sonnet)
        cost_usd = (response.input_tokens * 0.000003) + (response.output_tokens * 0.000015)

        # Prepare analysis_json with pattern boost metadata if applicable
        analysis_json = {
            'tool_calls': response.tool_calls,
            'pattern_boost': pattern_boost_metadata
        }

        # Save to database
        matched_pattern_id = (
            enhancement.matched_patterns[0].pattern_id
            if enhancement.boost_applied and enhancement.matched_patterns
            else None
        )
        # These fields live in analysis_json, not DB columns
        analysis.pop('evidence_references', None)
        analysis.pop('source_transparency', None)
        analysis_id = self.ai_repo.insert_analysis(
            build_failure_id=failure['id'],
            model_used=self.llm.model_name(),
            langfuse_trace_id=getattr(trace, 'id', None) if trace else None,
            tokens_used=response.input_tokens + response.output_tokens,
            cost_usd=cost_usd,
            analysis_duration=duration,
            analysis_json=analysis_json,
            error_pattern_id=matched_pattern_id,
            **analysis
        )

        # Update pattern library with this occurrence
        pattern = self.pattern_repo.find_or_create('build', analysis['failure_category'])
        self.pattern_repo.record_occurrence(pattern['id'], analysis['confidence_score'])
        if analysis_id:
            self.pattern_repo.link_analysis(analysis_id, pattern['id'])

        # Finalize trace
        self.langfuse.end_trace(trace, output=analysis)

        return analysis

    MAX_RETRIES = 3

    def run(self, limit=5, component_filter=None, force=False, application=None):
        """Analyze up to `limit` pending failures.

        Args:
            limit: Maximum number of failures to analyze
            component_filter: If specified, only analyze this component
            force: If True, re-analyze even if already analyzed
            application: Override config app. None uses config default.

        Returns:
            Dict with stats: analyzed, skipped_new, duration
        """
        app = application or self.config.k8s.application_name
        start_time = time.time()

        logger.info("=" * 70)
        logger.info("Build Failure AI Analysis")
        logger.info("Application: %s", app)
        if component_filter:
            logger.info("Component filter: %s", component_filter)
        if force:
            logger.info("Force mode: Re-analyzing existing analyses")
        logger.info("=" * 70)

        skipped_new = self.ai_repo.skip_no_logs_timeouts(app)
        if skipped_new:
            logger.info("Marked %d no-logs failures as skipped (timeout)", skipped_new)

        pending = self.get_pending_failures(
            limit=limit, component_filter=component_filter, force=force, application=app
        )

        if not pending:
            logger.info("No pending failures to analyze")
            return {'analyzed': 0, 'skipped_new': skipped_new, 'duration': 0}

        logger.info("Found %d pending failures", len(pending))

        analyzed = 0
        for i, failure in enumerate(pending, 1):
            logger.info("[%d/%d] %s", i, len(pending), failure['component_name'])
            logger.info("PipelineRun: %s", failure['pipelinerun_name'])

            attempts = 0
            try:
                attempts = self.ai_repo.increment_attempts(
                    build_failure_id=failure['id']
                )
                analysis = self.analyze_failure(failure)
                analyzed += 1

                logger.info("Category: %s (confidence: %.2f)",
                           analysis['failure_category'],
                           analysis['confidence_score'])
                logger.info("Can auto-fix: %s", analysis['can_auto_fix'])
                logger.info("Root cause: %s", analysis['root_cause'][:200])
                logger.info("")

            except Exception as e:
                logger.error("Analysis failed for %s (attempt %d): %s",
                             failure['component_name'], attempts, e)
                if attempts >= self.MAX_RETRIES:
                    self.ai_repo.mark_skipped(
                        'max_retries', build_failure_id=failure['id']
                    )
                    logger.warning(
                        "Skipping %s permanently after %d failed attempts",
                        failure['component_name'], self.MAX_RETRIES,
                    )

        # Flush Langfuse events
        self.langfuse.flush()

        duration = time.time() - start_time

        logger.info("=" * 70)
        logger.info("Analysis Complete")
        logger.info("=" * 70)
        logger.info("Analyzed: %d/%d failures", analyzed, len(pending))
        logger.info("Duration: %.1fs", duration)

        return {'analyzed': analyzed, 'skipped_new': skipped_new, 'duration': duration}
