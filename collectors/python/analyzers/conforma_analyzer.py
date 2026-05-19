"""Conforma violation analyzer using LLM provider.

Analyzes Enterprise Contract compliance violations. Similar to BuildFailureAnalyzer
but focused on policy compliance rather than code bugs.
"""

import os
import time

from logger import setup_logger
from repositories import DatabaseConnection, AIAnalysisRepository, ErrorPatternRepository
from clients.llm_provider import create_llm_provider
from clients.langfuse_tracker import LangfuseTracker
from prompt_loader import load_prompt

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


CONFORMA_SYSTEM_PROMPT = load_prompt('conforma_analyzer')


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

    def get_pending_violations(self, limit=5, component_filter=None, force=False, application=None):
        # type: (int, Optional[str], bool, Optional[str]) -> List[Dict[str, Any]]
        """Get unanalyzed Conforma violations from DB.

        Args:
            limit: Maximum number of violations to fetch
            component_filter: If specified, only get violations for this component
            force: If True, include already-analyzed violations
            application: Override config app. None uses config default.

        Returns:
            List of violation dicts ready for analysis
        """
        return self.ai_repo.get_pending_conforma_violations(
            application or self.config.k8s.application_name,
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
        # Known-pattern context from previous occurrences of the same failure category
        pattern_section = self._format_pattern_section(violation)

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
{pattern_section}
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
            summary=summary,
            pattern_section=pattern_section,
        )

        return (CONFORMA_SYSTEM_PROMPT, user_prompt)

    def _format_pattern_section(self, violation):
        # type: (Dict[str, Any],) -> str
        """Return a prompt section with institutional memory from prior occurrences."""
        fix = violation.get('pattern_typical_fix')
        doc = violation.get('pattern_doc_context')
        name = violation.get('pattern_name', 'prior occurrence')
        if not fix and not doc:
            return ""
        parts = ["\n## Known Pattern: {}\n".format(name)]
        if fix:
            parts.append("### Previous Solution\n{}\n".format(fix))
        if doc:
            parts.append("### Relevant Documentation\n{}\n".format(doc[:2000]))
        return '\n'.join(parts)

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
        analysis_id = self.ai_repo.insert_analysis(
            conforma_result_id=violation['id'],
            model_used=self.llm.model_name(),
            langfuse_trace_id=getattr(trace, 'id', None) if trace else None,
            tokens_used=response.input_tokens + response.output_tokens,
            cost_usd=cost_usd,
            analysis_duration=duration,
            analysis_json=response.tool_calls,
            **analysis
        )

        # Update pattern library with this occurrence
        pattern = self.pattern_repo.find_or_create('conforma', analysis['failure_category'])
        self.pattern_repo.record_occurrence(pattern['id'], analysis['confidence_score'])
        if analysis_id:
            self.pattern_repo.link_analysis(analysis_id, pattern['id'])

        # Finalize trace
        self.langfuse.end_trace(trace, output=analysis)

        return analysis

    MAX_RETRIES = 3

    def run(self, limit=5, component_filter=None, force=False, application=None):
        # type: (int, Optional[str], bool, Optional[str]) -> Dict[str, Any]
        """Analyze up to `limit` pending Conforma violations.

        Args:
            limit: Maximum number of violations to analyze
            component_filter: If specified, only analyze this component
            force: If True, re-analyze even if already analyzed
            application: Override config app. None uses config default.

        Returns:
            Dict with stats: analyzed, duration
        """
        app = application or self.config.k8s.application_name
        start_time = time.time()

        logger.info("=" * 70)
        logger.info("Conforma Violation AI Analysis")
        logger.info("Application: %s", app)
        if component_filter:
            logger.info("Component filter: %s", component_filter)
        if force:
            logger.info("Force mode: Re-analyzing existing analyses")
        logger.info("=" * 70)

        pending = self.get_pending_violations(
            limit=limit, component_filter=component_filter, force=force, application=app
        )

        if not pending:
            logger.info("No pending Conforma violations to analyze")
            return {'analyzed': 0, 'duration': 0}

        logger.info("Found %d pending violations", len(pending))

        analyzed = 0
        for i, violation in enumerate(pending, 1):
            logger.info("[%d/%d] %s", i, len(pending), violation['component_name'])
            logger.info("PipelineRun: %s", violation['pipelinerun_name'])
            logger.info("Violations: %d", violation['violations_count'])

            attempts = 0
            try:
                attempts = self.ai_repo.increment_attempts(
                    conforma_result_id=violation['id']
                )
                analysis = self.analyze_violation(violation)
                analyzed += 1

                logger.info("Category: %s (confidence: %.2f)",
                           analysis['failure_category'],
                           analysis['confidence_score'])
                logger.info("Can auto-fix: %s", analysis['can_auto_fix'])
                logger.info("Root cause: %s", analysis['root_cause'][:200])
                logger.info("")

            except Exception as e:
                logger.error("Analysis failed for %s (attempt %d): %s",
                             violation['component_name'], attempts, e)
                if attempts >= self.MAX_RETRIES:
                    self.ai_repo.mark_skipped(
                        'max_retries', conforma_result_id=violation['id']
                    )
                    logger.warning(
                        "Skipping %s permanently after %d failed attempts",
                        violation['component_name'], self.MAX_RETRIES,
                    )

        # Flush Langfuse events
        self.langfuse.flush()

        duration = time.time() - start_time

        logger.info("=" * 70)
        logger.info("Analysis Complete")
        logger.info("=" * 70)
        logger.info("Analyzed: %d/%d violations", analyzed, len(pending))
        logger.info("Duration: %.1fs", duration)

        return {'analyzed': analyzed, 'duration': duration}
