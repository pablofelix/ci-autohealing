"""Build failure analyzer using LLM provider.

Orchestrates AI analysis of build failures: fetches pending failures,
constructs prompts, calls LLM, parses responses, stores results.

Follows the collector pattern: thin orchestration delegating to clients
(LLMProvider) and repositories.
"""

import os
import time

from logger import setup_logger
from repositories import DatabaseConnection, BuildFailureRepository, AIAnalysisRepository, ErrorPatternRepository
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
                 ai_repo=None, llm=None, langfuse=None, pattern_service=None):
        # type: (CollectorConfig, ...) -> None
        """Initialize analyzer with dependency injection.

        Args:
            config: CollectorConfig with LLM settings
            db: Database connection (created if None)
            build_repo: BuildFailureRepository (created if None)
            ai_repo: AIAnalysisRepository (created if None)
            llm: LLMProvider (created from config if None)
            langfuse: LangfuseTracker (created if None)
            pattern_service: PatternMatchingService (created if None)
        """
        if db is None:
            db = DatabaseConnection(config.db)

        self.config = config
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
        # type: (int, Optional[str], bool, Optional[str]) -> List[Dict[str, Any]]
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

    def build_analysis_prompt(self, failure):
        # type: (Dict[str, Any],) -> Tuple[str, str]
        """Construct system + user prompts from failure data.

        Pure function extracted for testability.
        Includes commit context (diff, Dockerfile, .tekton/ configs) when available.

        Args:
            failure: Dict with component, error_message, logs, commit info

        Returns:
            Tuple of (system_prompt, user_prompt)
        """
        logs = failure.get('build_logs', '') or ''
        if len(logs) > 50000:
            logs = logs[-50000:]

        # Build commit context section (includes enriched_context if available)
        commit_context_section = self._format_commit_context(
            failure.get('commit_context'),
            failure.get('enriched_context')
        )

        # Known-pattern context from previous occurrences (top 3 patterns)
        pattern_section = self.pattern_service.get_matches_for_prompt(failure)

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
{commit_context}{pattern_section}
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
            logs=logs
        )

        return (SYSTEM_PROMPT, user_prompt)

    def _format_commit_context(self, commit_context, enriched_context=None):
        # type: (Optional[Dict[str, Any]], Optional[Dict[str, Any]]) -> str
        """Format commit context and enriched context into prompt text."""
        import json

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

        return '\n'.join(sections) + '\n'


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
        # type: (Dict[str, Any],) -> Dict[str, Any]
        """Analyze one failure: build prompt -> call LLM -> parse response -> save to DB.

        Args:
            failure: Failure dict from get_pending_failures()

        Returns:
            Dict with analysis results

        Raises:
            Exception: If LLM call or parsing fails
        """
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
        # type: (int, Optional[str], bool, Optional[str]) -> Dict[str, Any]
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
