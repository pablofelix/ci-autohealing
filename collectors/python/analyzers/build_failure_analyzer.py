"""Build failure analyzer using LLM provider.

Orchestrates AI analysis of build failures: fetches pending failures,
constructs prompts, calls LLM, parses responses, stores results.

Follows the collector pattern: thin orchestration delegating to clients
(LLMProvider) and repositories.
"""

import os
import time
from typing import Any, Dict, List, Optional, Tuple

from config import CollectorConfig
from logger import setup_logger
from repositories import DatabaseConnection, BuildFailureRepository, AIAnalysisRepository
from clients.llm_provider import create_llm_provider
from clients.langfuse_tracker import LangfuseTracker

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


SYSTEM_PROMPT = """You are a CI/CD troubleshooting specialist analysing Konflux pipeline failures for the RHOAI (Red Hat OpenShift AI) project. Your role is to help the team understand what went wrong and suggest a path forward.

## Tone and Style

Write as a knowledgeable colleague sharing findings with the team — not as an authority issuing verdicts. Use tentative, collaborative language:

- "From what I can see, this appears to be..." rather than "This is caused by..."
- "Looking at the commit history, there seem to be..." rather than "The commit history shows..."
- "It might be worth exploring whether..." rather than "You should..."
- "I could be misreading the history here" when you're not fully certain

Never state root causes as absolute fact. Present your analysis as informed interpretation of the evidence, acknowledging where you might be wrong. Use hedging language naturally: "appears to", "seems to", "could be", "likely", "it looks like".

## Output Format (CRITICAL)

Your `root_cause` and `recommended_fix` fields will be displayed to developers in a terminal. Format them for maximum clarity.

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
- IMPORTANT: Separate paragraphs with TWO newlines (\n\n) for visual spacing
- State only what the evidence directly shows - do not infer or speculate
- Cite the source for every claim using this format:
  * For files: "File `.tekton/pipeline.yaml` shows: `path-context: .`"
  * For logs: "Build logs line 142: `ERROR: cannot find module`"
  * For diffs: "Commit diff for `file.yaml`: removed line 10 `old value`, added `new value`"

**recommended_fix formatting (CRITICAL - NO NUMBERED LISTS):**
- MUST use bullet points with dash (-) character
- NEVER use numbered lists (1., 2., 3., etc.)
- IMPORTANT: Add blank line (\n\n) between each bullet point for readability
- Start each bullet with the action verb
- Include exact file paths, line numbers, or commands
- Format: "- Action to take. Source: file X line Y shows current value `Z`."
- Keep each bullet to 2-3 lines maximum

Example recommended_fix format:
```
- Update the path-context value. File `.tekton/pipeline.yaml` line 15 shows: `path-context: ./source` which needs to be `path-context: source`.

- Remove the .git suffix from repository URL. File `requirements.txt` references `git+https://example.com/repo.git` but the URL should be `git+https://example.com/repo`.

- Pin the dependency version. Build logs show `ERROR: No matching distribution found for package>=1.0` but file `requirements.txt` line 42 should specify exact version `package==1.2.3`.
```

**Evidence rules (CRITICAL):**
- Only state what you directly observe in: commit diff, logs, error messages, configs
- Do NOT infer intent, speculate about causes, or assume what "might" be wrong
- Quote exact text when referencing: error messages, config values, code snippets
- Always cite the source: which file, which line, which log section
- If evidence is missing, state: "Evidence not available: commit diff not provided"

## What Makes a Good Analysis

DO:
- Trace the failure back to the specific commit or change that introduced it
- Explain the chain of causation: what changed → why it broke → what to do about it
- Reference specific files, line numbers, or config parameters from the commit diff
- Distinguish between the immediate error and the underlying cause
- Suggest both a quick fix and a lasting solution when applicable
- Note related failures across other components if the pattern is similar

DO NOT:
- Simply restate the error message as the root cause
- Give generic advice like "check the configuration" without specifics
- Provide high confidence scores without strong evidence
- Ignore the commit diff when it's available — it's the most important evidence

## Evidence Priority

1. **Commit diff** (most important) — What actually changed? This usually reveals the cause
2. **Pipeline/Tekton configs** — How is the build configured? Context paths, Dockerfile locations
3. **Dockerfile** — Build steps, base images, dependencies
4. **Build logs** — The error output, stack traces
5. **Error message** — Useful for classification but not root cause

## Confidence Scoring

- 0.9+ Only when the commit diff clearly shows the breaking change
- 0.7-0.9 When the evidence strongly suggests a cause but you can't be 100% certain
- 0.5-0.7 When you have a reasonable hypothesis but limited evidence
- Below 0.5 When you're largely guessing — be honest about it

## Auto-fix Assessment

Mark `can_auto_fix: true` only when:
- The fix is a specific, mechanical change (version pin, path correction)
- You can identify the exact file and line to change
- No architectural or design judgement is required
- The change is low-risk and easily reversible

## When to Suggest Human Contact

If your confidence is below 0.6 or you cannot identify a clear root cause:
- Suggest reaching out to the commit author for context
- If the failure involves specific files, recommend contacting whoever last modified them
- Frame it as "it might be worth checking with [author] who made the recent changes to understand the intent"
- Don't just say "ask the author" — explain WHAT to ask them about (e.g., "ask about the intent behind changing CURDIR to REPO_ROOT")

## Known Konflux/Tekton Patterns

**CONTEXT parameter escapes source directory:**
- If error says "CONTEXT parameter ($CONTEXT) is invalid because it escapes the source"
- Check the Tekton config's `path-context` parameter
- Common cause: Leading `./` prefix (e.g., `./jobs/async-upload` instead of `jobs/async-upload`)
- Buildah interprets `./` as a path traversal attempt
- Fix: Remove the `./` prefix from the path-context value in the .tekton/ YAML file
- This often comes from konflux-central syncs — check the upstream config
- Confidence should be HIGH (0.9+) if you see this pattern in the Tekton config"""


class BuildFailureAnalyzer:
    """Analyzes build failures using an LLM provider."""

    def __init__(self, config, db=None, build_repo=None,
                 ai_repo=None, llm=None, langfuse=None):
        # type: (CollectorConfig, ...) -> None
        """Initialize analyzer with dependency injection.

        Args:
            config: CollectorConfig with LLM settings
            db: Database connection (created if None)
            build_repo: BuildFailureRepository (created if None)
            ai_repo: AIAnalysisRepository (created if None)
            llm: LLMProvider (created from config if None)
            langfuse: LangfuseTracker (created if None)
        """
        if db is None:
            db = DatabaseConnection(config.db)

        self.config = config
        self.build_repo = build_repo or BuildFailureRepository(db)
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

    def get_pending_failures(self, limit=5, component_filter=None, force=False):
        # type: (int, Optional[str], bool) -> List[Dict[str, Any]]
        """Get unanalyzed failures from DB.

        Args:
            limit: Maximum number of failures to fetch
            component_filter: If specified, only get failures for this component
            force: If True, include already-analyzed failures

        Returns:
            List of failure dicts ready for analysis
        """
        return self.ai_repo.get_pending_failures(
            self.config.k8s.application_name,
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

        # Build commit context section
        commit_context_section = self._format_commit_context(
            failure.get('commit_context')
        )

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
{commit_context}
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
            logs=logs
        )

        return (SYSTEM_PROMPT, user_prompt)

    def _format_commit_context(self, commit_context):
        # type: (Optional[Dict[str, Any]],) -> str
        """Format commit context dict into prompt text."""
        if not commit_context:
            return "\n## Commit Context\n(Not available — commit diff not fetched yet)\n"

        import json
        if isinstance(commit_context, str):
            try:
                commit_context = json.loads(commit_context)
            except (json.JSONDecodeError, TypeError):
                return "\n## Commit Context\n(Not available)\n"

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

        # Estimate cost (rough approximation: $3/MTok input, $15/MTok output for Claude Sonnet)
        cost_usd = (response.input_tokens * 0.000003) + (response.output_tokens * 0.000015)

        # Save to database
        self.ai_repo.insert_analysis(
            build_failure_id=failure['id'],
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
        """Analyze up to `limit` pending failures.

        Args:
            limit: Maximum number of failures to analyze
            component_filter: If specified, only analyze this component
            force: If True, re-analyze even if already analyzed

        Returns:
            Dict with stats: analyzed count, duration
        """
        start_time = time.time()

        logger.info("=" * 70)
        logger.info("Build Failure AI Analysis")
        logger.info("Application: %s", self.config.k8s.application_name)
        if component_filter:
            logger.info("Component filter: %s", component_filter)
        if force:
            logger.info("Force mode: Re-analyzing existing analyses")
        logger.info("=" * 70)

        pending = self.get_pending_failures(limit=limit, component_filter=component_filter, force=force)

        if not pending:
            logger.info("No pending failures to analyze")
            return {'analyzed': 0, 'duration': 0}

        logger.info("Found %d pending failures", len(pending))

        analyzed = 0
        for i, failure in enumerate(pending, 1):
            logger.info("[%d/%d] %s", i, len(pending), failure['component_name'])
            logger.info("PipelineRun: %s", failure['pipelinerun_name'])

            try:
                analysis = self.analyze_failure(failure)
                analyzed += 1

                logger.info("Category: %s (confidence: %.2f)",
                           analysis['failure_category'],
                           analysis['confidence_score'])
                logger.info("Can auto-fix: %s", analysis['can_auto_fix'])
                logger.info("Root cause: %s", analysis['root_cause'][:200])
                logger.info("")

            except Exception as e:
                logger.error("Analysis failed for %s: %s",
                           failure['component_name'], e)

        # Flush Langfuse events
        self.langfuse.flush()

        duration = time.time() - start_time

        logger.info("=" * 70)
        logger.info("Analysis Complete")
        logger.info("=" * 70)
        logger.info("Analyzed: %d/%d failures", analyzed, len(pending))
        logger.info("Duration: %.1fs", duration)

        return {'analyzed': analyzed, 'duration': duration}
