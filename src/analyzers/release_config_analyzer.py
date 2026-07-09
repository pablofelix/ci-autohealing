"""Konflux release configuration analyzer using LLM provider.

Audits release readiness: conforma blockers, PCC freshness, exception
coverage, snapshot SHA drift, and nightly build health.
"""

import os
import time

from clients.langfuse_tracker import LangfuseTracker
from clients.llm_provider import create_llm_provider
from logger import setup_logger
from proactive.health_monitor import HealthMonitor
from prompt_loader import load_prompt
from repositories.conforma_repository import ConformaRepository
from repositories.connection import DatabaseConnection

logger = setup_logger(__name__)

RELEASE_CONFIG_ANALYSIS_TOOL = {
    'name': 'record_release_config_analysis',
    'description': 'Record the release configuration analysis findings',
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
                                'conforma_blocker', 'pcc_cache_stale',
                                'missing_exception', 'sha_drift',
                                'nightly_broken',
                            ],
                        },
                        'description': {'type': 'string'},
                        'recommendation': {'type': 'string'},
                        'affected_components': {
                            'type': 'array',
                            'items': {'type': 'string'},
                        },
                        'blocks_release': {'type': 'boolean'},
                        'fix_action': {
                            'type': 'string',
                            'enum': [
                                'rebuild', 'exception_request', 'pcc_regen',
                                'config_change', 'investigation',
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
            'release_blockers': {
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

RELEASE_CONFIG_SYSTEM_PROMPT = load_prompt('release_config_analyzer')


class ReleaseConfigAnalyzer:
    """Audits release configuration readiness using LLM analysis."""

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

    def _gather_conforma_blocker_data(self, application):
        try:
            repo = ConformaRepository(self.db)
            return repo.get_violation_summaries(application)
        except Exception as e:
            logger.warning("Could not gather conforma blocker data: %s", e)
            return []

    def _gather_pcc_freshness_data(self):
        try:
            hm = HealthMonitor(self.db)
            return hm._check_pcc_freshness()
        except Exception as e:
            logger.warning("Could not gather PCC freshness data: %s", e)
            return None

    def _gather_exception_coverage_data(self, application):
        try:
            from conforma.policy_tools import fetch_exceptions_by_policy
            return fetch_exceptions_by_policy(application)
        except Exception as e:
            logger.warning("Could not gather exception coverage data: %s", e)
            return {}

    def _gather_snapshot_freshness_data(self, application):
        try:
            hm = HealthMonitor(self.db)
            return hm.check_snapshot_freshness(application)
        except Exception as e:
            logger.warning("Could not gather snapshot freshness data: %s", e)
            return {}

    def _gather_nightly_status_data(self, application):
        try:
            hm = HealthMonitor(self.db)
            return hm.get_nightly_status(application)
        except Exception as e:
            logger.warning("Could not gather nightly status data: %s", e)
            return {}

    def build_analysis_prompt(self, gathered_data):
        """Build system + user prompt from gathered data sections."""
        sections = []

        violations = gathered_data.get('conforma_violations', [])
        if violations:
            sections.append("## Conforma Violations ({})".format(len(violations)))
            for v in violations[:30]:
                sections.append("  - {}: {} (x{})".format(
                    v.get('component', v.get('component_name', '')),
                    v.get('rule', v.get('violation_summary', '')),
                    v.get('count', v.get('violations_count', 1))))

        pcc = gathered_data.get('pcc_freshness')
        if pcc:
            sections.append("\n## PCC Cache Status")
            sections.append("Status: {}".format(pcc.get('status', 'unknown')))
            missing = pcc.get('missing_versions', [])
            if missing:
                sections.append("Missing versions: {}".format(
                    ', '.join(str(v) for v in missing[:10])))
            regen = pcc.get('last_regen')
            if regen:
                sections.append("Last regen: {} ({})".format(
                    regen.get('date', '?'), regen.get('conclusion', '?')))

        exceptions = gathered_data.get('exception_coverage', {})
        if exceptions:
            sections.append("\n## Exception Coverage")
            for policy, exc_list in exceptions.items():
                sections.append("  Policy: {} — {} exceptions".format(
                    policy, len(exc_list) if isinstance(exc_list, list) else '?'))

        snap = gathered_data.get('snapshot_freshness', {})
        stale_snaps = snap.get('stale', [])
        if stale_snaps:
            sections.append("\n## Snapshot Freshness Issues ({})".format(
                len(stale_snaps)))
            for s in stale_snaps[:15]:
                sections.append("  - {} — {}".format(
                    s.get('component', ''), s.get('reason', 'stale')))

        nightly = gathered_data.get('nightly_status', {})
        if nightly:
            fbc = nightly.get('fbc_health', {})
            blockers = nightly.get('blockers', [])
            if fbc or blockers:
                sections.append("\n## Nightly Build Status")
                if fbc:
                    sections.append("FBC fragment: {}".format(
                        fbc.get('current_status', 'unknown')))
                if blockers:
                    sections.append("Blockers ({}):")
                    for b in blockers[:10]:
                        sections.append("  - {}".format(
                            b.get('component', b) if isinstance(b, dict) else b))

        user_prompt = """Analyze the release configuration for {app}.

{data}

Use the record_release_config_analysis tool. For each finding:
1. Title: short description of the issue
2. Severity: critical/warning/info
3. Category: pick from the defined categories
4. Description: detailed explanation
5. Recommendation: specific action to take
6. affected_components: list of components
7. blocks_release: true if this will block the release gate
8. fix_action: rebuild/exception_request/pcc_regen/config_change/investigation

Include release_blockers for components that will block the release.
""".format(
            app=gathered_data.get('application', 'unknown'),
            data='\n'.join(sections) if sections else 'No data collected.',
        )

        return (RELEASE_CONFIG_SYSTEM_PROMPT, user_prompt)

    def parse_analysis_response(self, llm_response):
        """Extract tool call and validate with Pydantic."""
        from pydantic import ValidationError

        from analyzers.models import ReleaseConfigAnalysisResult

        if not llm_response.tool_calls:
            raise ValueError("LLM did not return tool_use response")

        analysis_call = None
        for call in llm_response.tool_calls:
            if call.get('name') == 'record_release_config_analysis':
                analysis_call = call
                break

        if not analysis_call:
            raise ValueError(
                "LLM did not call record_release_config_analysis tool")

        input_data = analysis_call.get('input', {})

        try:
            result = ReleaseConfigAnalysisResult(**input_data)
            return result.model_dump()
        except ValidationError as e:
            logger.error("LLM returned invalid release config analysis: %s", e)
            return {
                'findings': [],
                'overall_severity': 'info',
                'confidence_score': 0.0,
                'summary': input_data.get('summary', 'Validation failed'),
                'release_blockers': [],
            }

    def analyze(self, application=None):
        """Gather data, build prompt, call LLM, parse response."""
        application = application or os.environ.get(
            'APPLICATION_NAME', 'rhoai-v3-5')

        logger.info("Gathering release config data for %s...", application)

        conforma = self._gather_conforma_blocker_data(application)
        pcc = self._gather_pcc_freshness_data()
        exceptions = self._gather_exception_coverage_data(application)
        snapshot = self._gather_snapshot_freshness_data(application)
        nightly = self._gather_nightly_status_data(application)

        gathered = {
            'application': application,
            'conforma_violations': conforma,
            'pcc_freshness': pcc,
            'exception_coverage': exceptions,
            'snapshot_freshness': snapshot,
            'nightly_status': nightly,
        }

        system_prompt, user_prompt = self.build_analysis_prompt(gathered)

        trace = self.langfuse.create_trace(
            name='release-config-analysis',
            input_data={'application': application},
        )

        start_time = time.time()
        response = self.llm.create_message(
            system=system_prompt,
            user_content=user_prompt,
            tools=[RELEASE_CONFIG_ANALYSIS_TOOL],
            max_tokens=8192,
        )
        duration = time.time() - start_time

        self.langfuse.record_generation(
            trace,
            name='analyze-release-config',
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

        logger.info("Release config analysis complete in %.1fs", duration)
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
        logger.info("Release Configuration Analysis")
        if application:
            logger.info("Application: %s", application)
        logger.info("=" * 70)

        return self.analyze(application=application)
