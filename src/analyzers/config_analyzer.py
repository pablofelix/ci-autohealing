"""Konflux configuration analyzer using LLM provider.

Audits EC policies, ITS scenarios, and violation patterns to detect
misconfigurations, coverage gaps, and auto-rebuild opportunities.
"""

import os
import time

from clients.langfuse_tracker import LangfuseTracker
from clients.llm_provider import create_llm_provider
from logger import setup_logger
from prompt_loader import load_prompt
from repositories.connection import DatabaseConnection

logger = setup_logger(__name__)

CONFIG_ANALYSIS_TOOL = {
    'name': 'record_config_analysis',
    'description': 'Record the Konflux configuration analysis findings',
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
                                'expired_exceptions', 'policy_gap',
                                'scenario_coverage', 'pipeline_config',
                                'auto_rebuild_candidate', 'rule_catalog_gap',
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
                                'rebuild', 'config_change', 'exception',
                                'pipeline_update', 'investigation',
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

TRANSIENT_RULES = {
    'builtin.attestation.signature_check',
    'slsa_source_correlated.source_code_reference_provided',
    'tasks.successful_pipeline_tasks',
    'test.no_erred_tests',
    'rpm_packages.unique_version',
}

CONFIG_SYSTEM_PROMPT = load_prompt('config_analyzer')


class ConfigAnalyzer:
    """Audits Konflux configuration using LLM analysis."""

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

    def _gather_ec_policy_data(self):
        from clients.konflux_client import KonfluxClient
        ns = os.environ.get('NAMESPACE', '')
        name_filter = 'rhoai'
        try:
            client = KonfluxClient(namespace=ns)
            policies = client.get_ec_policies(name_filter=name_filter)
            all_exceptions = []
            for p in policies:
                all_exceptions.extend(client.extract_exceptions(p))
            active = [e for e in all_exceptions
                      if e['days_left'] is None or e['days_left'] >= 0]
            expired = [e for e in all_exceptions
                       if e['days_left'] is not None and e['days_left'] < 0]
            expiring = [e for e in all_exceptions
                        if e['days_left'] is not None and 0 <= e['days_left'] <= 30]
            expiring.sort(key=lambda e: e['days_left'])
            return {
                'policies_count': len(policies),
                'total_exceptions': len(all_exceptions),
                'active': active,
                'expired': expired,
                'expiring': expiring,
            }
        except Exception as e:
            logger.warning("Could not gather EC policy data: %s", e)
            return {'error': str(e)}

    def _gather_scenario_data(self, application):
        from clients.konflux_client import KonfluxClient
        ns = os.environ.get('NAMESPACE', '')
        try:
            client = KonfluxClient(namespace=ns)
            scenarios = client.get_integration_test_scenarios(
                namespace=ns, app_filter=application,
            )
            metadata = [KonfluxClient.extract_its_metadata(s) for s in scenarios]
            active = [s for s in metadata if not s['is_disabled']]
            disabled = [s for s in metadata if s['is_disabled']]
            conforma = [s for s in metadata if s['is_conforma']]
            apps = sorted(set(s['application'] for s in metadata))
            conforma_apps = set(s['application'] for s in conforma if not s['is_disabled'])
            gaps = [app for app in apps if app not in conforma_apps]
            return {
                'total': len(metadata),
                'active': len(active),
                'disabled': len(disabled),
                'conforma': len(conforma),
                'applications': apps,
                'gaps': gaps,
                'disabled_conforma': [
                    {'name': s['name'], 'application': s['application']}
                    for s in conforma if s['is_disabled']
                ],
            }
        except Exception as e:
            logger.warning("Could not gather scenario data: %s", e)
            return {'error': str(e)}

    def _gather_violation_data(self, application):
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT component_name, violation_summary, violations_count,
                       first_detected_at, scenario
                FROM conforma_results
                WHERE is_resolved = FALSE
                  AND application = %s
                ORDER BY violations_count DESC
            """, [application])
            unresolved = []
            for row in cursor.fetchall():
                unresolved.append({
                    'component': row[0],
                    'rule': row[1],
                    'count': row[2],
                    'since': str(row[3]) if row[3] else '',
                    'scenario': row[4],
                })

            cursor.execute("""
                SELECT
                    CASE
                        WHEN violation_summary LIKE '%%hermetic_task%%' THEN 'hermetic_task'
                        WHEN violation_summary LIKE '%%trusted_task%%' THEN 'trusted_task'
                        WHEN violation_summary LIKE '%%source_image%%' THEN 'source_image'
                        WHEN violation_summary LIKE '%%labels%%' THEN 'labels'
                        WHEN violation_summary LIKE '%%fips%%' THEN 'fips'
                        WHEN violation_summary LIKE '%%slsa%%' THEN 'slsa'
                        WHEN violation_summary LIKE '%%sbom%%' THEN 'sbom'
                        WHEN violation_summary LIKE '%%rpm%%' THEN 'rpm'
                        WHEN violation_summary LIKE '%%signature%%' THEN 'signature'
                        ELSE 'other'
                    END as rule_group,
                    COUNT(*) as total,
                    AVG(EXTRACT(EPOCH FROM (resolved_at - first_detected_at)) / 3600)::numeric(8,1)
                        as avg_hours
                FROM conforma_results
                WHERE is_resolved = TRUE AND application = %s
                GROUP BY rule_group ORDER BY total DESC
            """, [application])
            resolved_patterns = []
            for row in cursor.fetchall():
                resolved_patterns.append({
                    'rule_group': row[0],
                    'count': row[1],
                    'avg_hours_to_resolve': float(row[2]) if row[2] else None,
                })

            return {
                'unresolved': unresolved,
                'unresolved_count': len(unresolved),
                'resolved_patterns': resolved_patterns,
            }

    def _classify_rebuild_candidates(self, violations):
        candidates = []
        for v in violations:
            rule = v.get('rule', '')
            is_transient = any(tr in rule for tr in TRANSIENT_RULES)
            if is_transient:
                candidates.append(v['component'])
        component_rules = {}
        for v in violations:
            comp = v['component']
            if comp not in component_rules:
                component_rules[comp] = []
            component_rules[comp].append(v['rule'])

        pure_transient = []
        for comp, rules in component_rules.items():
            all_transient = all(
                any(tr in r for tr in TRANSIENT_RULES) for r in rules
            )
            if all_transient:
                pure_transient.append(comp)

        return sorted(set(pure_transient))

    def build_analysis_prompt(self, gathered_data):
        sections = []

        ec = gathered_data.get('ec_policy', {})
        if ec and 'error' not in ec:
            sections.append("## EC Policy Summary")
            sections.append("Policies: {}".format(ec.get('policies_count', 0)))
            sections.append("Active exceptions: {}".format(len(ec.get('active', []))))
            sections.append("Expired exceptions: {}".format(len(ec.get('expired', []))))
            sections.append("Expiring within 30 days: {}".format(
                len(ec.get('expiring', []))))
            if ec.get('expiring'):
                sections.append("\nExpiring soon:")
                for e in ec['expiring'][:10]:
                    sections.append("  - {} ({}d left, policy: {})".format(
                        e.get('value', ''), e.get('days_left', '?'),
                        e.get('policy', '')))
            if ec.get('expired'):
                sections.append("\nExpired:")
                for e in ec['expired'][:10]:
                    sections.append("  - {} (expired {}d ago, policy: {})".format(
                        e.get('value', ''), abs(e.get('days_left', 0)),
                        e.get('policy', '')))

        sc = gathered_data.get('scenarios', {})
        if sc and 'error' not in sc:
            sections.append("\n## ITS Scenario Coverage")
            sections.append("Total: {}, Active: {}, Disabled: {}, Conforma: {}".format(
                sc.get('total', 0), sc.get('active', 0),
                sc.get('disabled', 0), sc.get('conforma', 0)))
            if sc.get('gaps'):
                sections.append("Apps without conforma: {}".format(
                    ', '.join(sc['gaps'])))
            if sc.get('disabled_conforma'):
                sections.append("Disabled conforma scenarios:")
                for d in sc['disabled_conforma']:
                    sections.append("  - {} ({})".format(
                        d['name'], d['application']))

        vd = gathered_data.get('violations', {})
        if vd:
            sections.append("\n## Unresolved Violations ({})".format(
                vd.get('unresolved_count', 0)))
            for v in vd.get('unresolved', [])[:30]:
                sections.append("  - {}: {} (x{}, since {})".format(
                    v['component'], v['rule'], v['count'], v['since']))

            sections.append("\n## Resolved Patterns")
            for rp in vd.get('resolved_patterns', []):
                avg = rp['avg_hours_to_resolve']
                sections.append("  - {}: {} resolved, avg {:.0f}h".format(
                    rp['rule_group'], rp['count'],
                    avg if avg else 0))

        rb = gathered_data.get('rebuild_candidates', [])
        if rb:
            sections.append("\n## Auto-rebuild Candidates (all violations transient)")
            for comp in rb:
                sections.append("  - {}".format(comp))

        user_prompt = """Analyze the Konflux configuration for {app}.

{data}

Use the record_config_analysis tool. For each finding:
1. Title: short description of the issue
2. Severity: critical/warning/info
3. Category: pick from the defined categories
4. Description: detailed explanation
5. Recommendation: specific action to take
6. affected_components: list of components
7. can_auto_fix: true if a rebuild or simple config change fixes it
8. fix_action: rebuild/config_change/exception/pipeline_update/investigation

Include auto_rebuild_candidates in the top-level response.
""".format(
            app=gathered_data.get('application', 'unknown'),
            data='\n'.join(sections),
        )

        return (CONFIG_SYSTEM_PROMPT, user_prompt)

    def parse_analysis_response(self, llm_response):
        from pydantic import ValidationError

        from analyzers.models import ConfigAnalysisResult

        if not llm_response.tool_calls:
            raise ValueError("LLM did not return tool_use response")

        analysis_call = None
        for call in llm_response.tool_calls:
            if call.get('name') == 'record_config_analysis':
                analysis_call = call
                break

        if not analysis_call:
            raise ValueError("LLM did not call record_config_analysis tool")

        input_data = analysis_call.get('input', {})

        try:
            result = ConfigAnalysisResult(**input_data)
            return result.model_dump()
        except ValidationError as e:
            logger.error("LLM returned invalid config analysis: %s", e)
            return {
                'findings': [],
                'overall_severity': 'info',
                'confidence_score': 0.0,
                'summary': input_data.get('summary', 'Validation failed'),
                'auto_rebuild_candidates': [],
            }

    def analyze(self, application=None):
        application = application or os.environ.get(
            'APPLICATION_NAME', 'rhoai-v3-5')

        logger.info("Gathering configuration data for %s...", application)

        ec_policy = self._gather_ec_policy_data()
        scenarios = self._gather_scenario_data(application)
        violations = self._gather_violation_data(application)
        rebuild_candidates = self._classify_rebuild_candidates(
            violations.get('unresolved', []))

        gathered = {
            'application': application,
            'ec_policy': ec_policy,
            'scenarios': scenarios,
            'violations': violations,
            'rebuild_candidates': rebuild_candidates,
        }

        system_prompt, user_prompt = self.build_analysis_prompt(gathered)

        trace = self.langfuse.create_trace(
            name='config-analysis',
            input_data={'application': application},
        )

        start_time = time.time()
        response = self.llm.create_message(
            system=system_prompt,
            user_content=user_prompt,
            tools=[CONFIG_ANALYSIS_TOOL],
            max_tokens=8192,
        )
        duration = time.time() - start_time

        self.langfuse.record_generation(
            trace,
            name='analyze-config',
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

        logger.info("Analysis complete in %.1fs", duration)
        logger.info("Severity: %s, Findings: %d, Confidence: %.2f",
                     analysis['overall_severity'],
                     len(analysis['findings']),
                     analysis['confidence_score'])
        logger.info("Cost: $%.4f (%d input + %d output tokens)",
                     cost_usd, response.input_tokens, response.output_tokens)

        return {
            'analyzed': True,
            'analysis': analysis,
            'duration': duration,
            'cost_usd': cost_usd,
            'model': self.llm.model_name(),
            'gathered_data_summary': {
                'violations_count': violations.get('unresolved_count', 0),
                'rebuild_candidates': rebuild_candidates,
            },
        }

    def run(self, application=None):
        logger.info("=" * 70)
        logger.info("Konflux Configuration Analysis")
        if application:
            logger.info("Application: %s", application)
        logger.info("=" * 70)

        return self.analyze(application=application)
