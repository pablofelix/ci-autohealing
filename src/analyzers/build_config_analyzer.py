"""Konflux build pipeline configuration analyzer using LLM provider.

Audits build health across components: stale nudges, recurring transient
failures, webhook misconfigurations, and broken build chains.
"""

import os
import time

from clients.langfuse_tracker import LangfuseTracker
from clients.llm_provider import create_llm_provider
from logger import setup_logger
from proactive.health_monitor import HealthMonitor
from prompt_loader import load_prompt
from repositories.build_failure_repository import BuildFailureRepository
from repositories.connection import DatabaseConnection

logger = setup_logger(__name__)

BUILD_CONFIG_ANALYSIS_TOOL = {
    'name': 'record_build_config_analysis',
    'description': 'Record the build pipeline configuration analysis findings',
    'input_schema': {
        'type': 'object',
        'properties': {
            'findings': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'title': {'type': 'string'},
                        'severity': {
                            'type': 'string',
                            'enum': ['critical', 'warning', 'info'],
                        },
                        'category': {
                            'type': 'string',
                            'enum': [
                                'stale_component', 'recurring_transient',
                                'quota_bottleneck', 'missing_webhook',
                                'build_chain_broken',
                            ],
                        },
                        'description': {'type': 'string'},
                        'recommendation': {'type': 'string'},
                        'affected_components': {
                            'type': 'array',
                            'items': {'type': 'string'},
                        },
                        'can_auto_fix': {'type': 'boolean'},
                        'fix_action': {
                            'type': 'string',
                            'enum': [
                                'rebuild', 'nudge_fix', 'webhook_setup',
                                'quota_request', 'investigation',
                            ],
                        },
                    },
                    'required': [
                        'title', 'severity', 'category',
                        'description', 'recommendation',
                    ],
                },
            },
            'overall_severity': {
                'type': 'string',
                'enum': ['critical', 'warning', 'info'],
            },
            'confidence_score': {
                'type': 'number',
                'minimum': 0,
                'maximum': 1,
            },
            'summary': {'type': 'string'},
            'auto_rebuild_candidates': {
                'type': 'array',
                'items': {'type': 'string'},
            },
        },
        'required': [
            'findings', 'overall_severity',
            'confidence_score', 'summary',
        ],
    },
}

BUILD_CONFIG_SYSTEM_PROMPT = load_prompt('build_config_analyzer')


class BuildConfigAnalyzer:
    """Audits build pipeline configuration using LLM analysis."""

    def __init__(self, config, db=None, llm=None, langfuse=None):
        self.config = config

        if db is None:
            db = DatabaseConnection(config.db)
        self.db = db

        if llm is None:
            if not config.llm:
                raise ValueError("LLM not configured. Set LLM_PROVIDER in .env")
            llm = create_llm_provider(config.llm)
        self.llm = llm

        if langfuse is None:
            langfuse_enabled = bool(os.environ.get('LANGFUSE_PUBLIC_KEY'))
            langfuse = LangfuseTracker(enabled=langfuse_enabled)
        self.langfuse = langfuse

    def _gather_stale_component_data(self, application):
        try:
            hm = HealthMonitor(self.db)
            return hm.get_stale_components(application, diagnose=True)
        except Exception as e:
            logger.warning("Could not gather stale component data: %s", e)
            return {'error': str(e), 'stale': [], 'stale_count': 0}

    def _gather_recurring_transient_data(self, application):
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT
                        component_name,
                        error_type,
                        COUNT(*) as failure_count,
                        MAX(first_detected_at) as latest,
                        MIN(first_detected_at) as earliest
                    FROM build_failures
                    WHERE application = %s
                      AND first_detected_at > NOW() - INTERVAL '7 days'
                      AND is_resolved = TRUE
                    GROUP BY component_name, error_type
                    HAVING COUNT(*) >= 3
                    ORDER BY COUNT(*) DESC
                """, [application])
                cols = [d[0] for d in cursor.description]
                return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as e:
            logger.warning("Could not gather recurring transient data: %s", e)
            return []

    def _gather_build_duration_data(self, application):
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT
                        component_name,
                        AVG(build_duration_seconds) as avg_duration,
                        MAX(build_duration_seconds) as max_duration,
                        COUNT(*) as build_count
                    FROM build_failures
                    WHERE application = %s
                      AND build_duration_seconds IS NOT NULL
                      AND first_detected_at > NOW() - INTERVAL '7 days'
                    GROUP BY component_name
                    HAVING AVG(build_duration_seconds) > 1800
                    ORDER BY AVG(build_duration_seconds) DESC
                """, [application])
                cols = [d[0] for d in cursor.description]
                return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as e:
            logger.warning("Could not gather build duration data: %s", e)
            return []

    def _gather_webhook_issues(self, stale_data):
        if not stale_data or 'stale' not in stale_data:
            return []
        return [
            s for s in stale_data.get('stale', [])
            if s.get('diagnosis', {}).get('cause') == 'missing_pac_repository'
        ]

    def _gather_build_chain_data(self, application, stale_data):
        try:
            repo = BuildFailureRepository(self.db)
            failing = repo.find_failing_component_names(application)
            if not failing:
                return []

            chain_issues = []
            for s in stale_data.get('stale', []):
                diag = s.get('diagnosis', {})
                cause = diag.get('cause', '')
                if cause in ('nudge_misconfiguration', 'upstream_failure'):
                    upstream = diag.get('detail', '')
                    if any(f in upstream for f in (failing or set())):
                        chain_issues.append({
                            'upstream': upstream,
                            'downstream': [s.get('component', s.get('name', ''))],
                        })
            return chain_issues
        except Exception as e:
            logger.warning("Could not gather build chain data: %s", e)
            return []

    def _gather_repeat_failure_data(self):
        try:
            hm = HealthMonitor(self.db)
            warnings = hm.get_repeat_failures()
            return [
                {
                    'component': w.component_name,
                    'application': w.application,
                    'severity': w.severity,
                    'message': w.message,
                }
                for w in warnings
            ]
        except Exception as e:
            logger.warning("Could not gather repeat failure data: %s", e)
            return []

    def build_analysis_prompt(self, gathered_data):
        """Build system + user prompt from gathered data sections."""
        sections = []

        stale = gathered_data.get('stale_components', {})
        stale_list = stale.get('stale', [])
        if stale_list:
            sections.append("## Stale Components ({})".format(len(stale_list)))
            for s in stale_list[:20]:
                comp = s.get('component', s.get('name', ''))
                days = s.get('days_behind', '?')
                diag = s.get('diagnosis', {})
                cause = diag.get('cause', 'unknown')
                sections.append("  - {} ({}d behind, cause: {})".format(
                    comp, days, cause))

        recurring = gathered_data.get('recurring_transient', [])
        if recurring:
            sections.append("\n## Recurring Transient Failures")
            for r in recurring[:15]:
                sections.append("  - {} — {} failures of '{}' in 7 days".format(
                    r.get('component', r.get('component_name', '')),
                    r.get('failure_count', '?'),
                    r.get('error_type', 'unknown')))

        durations = gathered_data.get('build_durations', [])
        if isinstance(durations, list) and durations:
            sections.append("\n## Quota/Duration Bottlenecks")
            for d in durations[:10]:
                avg = d.get('avg_duration', 0)
                sections.append("  - {} — avg {:.0f}s ({:.0f} min)".format(
                    d.get('component_name', ''),
                    float(avg) if avg else 0,
                    float(avg) / 60 if avg else 0))

        webhooks = gathered_data.get('webhook_issues', [])
        if webhooks:
            sections.append("\n## Webhook/Trigger Issues")
            for w in webhooks:
                comp = w.get('component', w.get('name', ''))
                sections.append("  - {} — missing PaC repository".format(comp))

        chains = gathered_data.get('build_chain', [])
        if chains:
            sections.append("\n## Build Chain Issues")
            for c in chains:
                downstream = ', '.join(c.get('downstream', []))
                sections.append("  - {} failing → blocks {}".format(
                    c.get('upstream', '?'), downstream))

        repeats = gathered_data.get('repeat_failures', [])
        if repeats:
            sections.append("\n## Repeat Failures (failed fix attempts)")
            for r in repeats[:10]:
                sections.append("  - {} — {}".format(
                    r.get('component', ''), r.get('message', '')))

        user_prompt = """Analyze the build pipeline configuration for {app}.

{data}

Use the record_build_config_analysis tool. For each finding:
1. Title: short description of the issue
2. Severity: critical/warning/info
3. Category: pick from the defined categories
4. Description: detailed explanation
5. Recommendation: specific action to take
6. affected_components: list of components
7. can_auto_fix: true if a rebuild fixes it
8. fix_action: rebuild/nudge_fix/webhook_setup/quota_request/investigation

Include auto_rebuild_candidates for components with recurring transient failures.
""".format(
            app=gathered_data.get('application', 'unknown'),
            data='\n'.join(sections) if sections else 'No data collected.',
        )

        return (BUILD_CONFIG_SYSTEM_PROMPT, user_prompt)

    def parse_analysis_response(self, llm_response):
        """Extract tool call and validate with Pydantic."""
        from pydantic import ValidationError

        from analyzers.models import BuildConfigAnalysisResult

        if not llm_response.tool_calls:
            raise ValueError("LLM did not return tool_use response")

        analysis_call = None
        for call in llm_response.tool_calls:
            if call.get('name') == 'record_build_config_analysis':
                analysis_call = call
                break

        if not analysis_call:
            raise ValueError("LLM did not call record_build_config_analysis tool")

        input_data = analysis_call.get('input', {})

        try:
            result = BuildConfigAnalysisResult(**input_data)
            return result.model_dump()
        except ValidationError as e:
            logger.error("LLM returned invalid build config analysis: %s", e)
            return {
                'findings': [],
                'overall_severity': 'info',
                'confidence_score': 0.0,
                'summary': input_data.get('summary', 'Validation failed'),
                'auto_rebuild_candidates': [],
            }

    def analyze(self, application=None):
        """Gather data, build prompt, call LLM, parse response."""
        application = application or os.environ.get(
            'APPLICATION_NAME', 'rhoai-v3-5')

        logger.info("Gathering build config data for %s...", application)

        stale_data = self._gather_stale_component_data(application)
        recurring = self._gather_recurring_transient_data(application)
        durations = self._gather_build_duration_data(application)
        webhook_issues = self._gather_webhook_issues(stale_data)
        build_chain = self._gather_build_chain_data(application, stale_data)
        repeat_failures = self._gather_repeat_failure_data()

        gathered = {
            'application': application,
            'stale_components': stale_data,
            'recurring_transient': recurring,
            'build_durations': durations,
            'webhook_issues': webhook_issues,
            'build_chain': build_chain,
            'repeat_failures': repeat_failures,
        }

        system_prompt, user_prompt = self.build_analysis_prompt(gathered)

        trace = self.langfuse.create_trace(
            name='build-config-analysis',
            input_data={'application': application},
        )

        start_time = time.time()
        response = self.llm.create_message(
            system=system_prompt,
            user_content=user_prompt,
            tools=[BUILD_CONFIG_ANALYSIS_TOOL],
            max_tokens=8192,
        )
        duration = time.time() - start_time

        self.langfuse.record_generation(
            trace,
            name='analyze-build-config',
            model=self.llm.model_name(),
            prompt=user_prompt[:5000],
            completion=str(response.tool_calls),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            duration_ms=int(duration * 1000),
        )

        analysis = self.parse_analysis_response(response)

        cost_usd = (
            (response.input_tokens * 0.000003)
            + (response.output_tokens * 0.000015)
        )

        self.langfuse.end_trace(trace, output=analysis)
        self.langfuse.flush()

        logger.info("Build config analysis complete in %.1fs", duration)
        logger.info("Severity: %s, Findings: %d, Confidence: %.2f",
                     analysis['overall_severity'],
                     len(analysis['findings']),
                     analysis['confidence_score'])

        return {
            'analyzed': True,
            'analysis': analysis,
            'duration': duration,
            'cost_usd': cost_usd,
            'model': self.llm.model_name(),
        }

    def run(self, application=None):
        """Entry point with logging header."""
        logger.info("=" * 70)
        logger.info("Build Pipeline Configuration Analysis")
        if application:
            logger.info("Application: %s", application)
        logger.info("=" * 70)

        return self.analyze(application=application)
