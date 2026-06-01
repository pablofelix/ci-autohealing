"""ITS scenario configuration analyzer using LLM provider.

Proactively analyzes IntegrationTestScenario CRD configurations to detect
misconfigurations, coverage gaps, and improvement opportunities.
"""

import os
import time

from logger import setup_logger
from clients.konflux_client import KonfluxClient
from clients.llm_provider import create_llm_provider
from clients.langfuse_tracker import LangfuseTracker
from prompt_loader import load_prompt

logger = setup_logger(__name__)


SCENARIOS_ANALYSIS_TOOL = {
    'name': 'record_scenarios_analysis',
    'description': 'Record the analysis of IntegrationTestScenario configurations',
    'input_schema': {
        'type': 'object',
        'properties': {
            'findings': {
                'type': 'string',
                'description': 'Detailed findings about configuration issues, gaps, and observations'
            },
            'severity': {
                'type': 'string',
                'enum': ['critical', 'warning', 'info'],
                'description': 'Overall severity: critical (immediate action), warning (investigate), info (good to know)'
            },
            'confidence_score': {
                'type': 'number',
                'minimum': 0,
                'maximum': 1,
                'description': 'Confidence in this analysis (0.0-1.0)'
            },
            'recommendations': {
                'type': 'string',
                'description': 'Prioritized list of recommended actions'
            },
            'issues_count': {
                'type': 'integer',
                'minimum': 0,
                'description': 'Number of distinct issues found'
            },
        },
        'required': [
            'findings',
            'severity',
            'confidence_score',
            'recommendations',
            'issues_count',
        ]
    }
}

SCENARIOS_SYSTEM_PROMPT = load_prompt('scenarios_analyzer')


class ScenariosAnalyzer:
    """Proactively analyzes ITS configurations using an LLM provider."""

    def __init__(self, config, llm=None, langfuse=None):
        self.config = config

        if llm is None:
            if not config.llm:
                raise ValueError("LLM not configured. Set LLM_PROVIDER in .env")
            llm = create_llm_provider(config.llm)
        self.llm = llm

        if langfuse is None:
            langfuse_enabled = bool(os.environ.get('LANGFUSE_PUBLIC_KEY'))
            langfuse = LangfuseTracker(enabled=langfuse_enabled)
        self.langfuse = langfuse

    def fetch_scenarios(self, namespace, app_filter=None):
        client = KonfluxClient()
        scenarios = client.get_integration_test_scenarios(
            namespace=namespace, app_filter=app_filter,
        )
        return [KonfluxClient.extract_its_metadata(s) for s in scenarios]

    def build_analysis_prompt(self, scenarios, app_filter=None):
        active = [s for s in scenarios if not s['is_disabled']]
        disabled = [s for s in scenarios if s['is_disabled']]
        future = [s for s in scenarios if s['is_future']]
        conforma = [s for s in scenarios if s['is_conforma']]

        apps = sorted(set(s['application'] for s in scenarios))

        scenario_lines = []
        for s in sorted(scenarios, key=lambda x: (x['application'], x['is_disabled'], x['name'])):
            status = "DISABLED" if s['is_disabled'] else "active"
            stype = "conforma" if s['is_conforma'] else "other"
            if s['is_future']:
                stype += "+future"
            policy = s['policy_ref'].split('/')[-1] if s['policy_ref'] else "(none)"
            scenario_lines.append(
                "  {name}  |  {app}  |  {status}  |  {policy}  |  {stype}".format(
                    name=s['name'], app=s['application'],
                    status=status, policy=policy, stype=stype,
                )
            )

        label = app_filter or "all apps in namespace"

        user_prompt = """Analyze the IntegrationTestScenario configuration for {label}. Identify misconfigurations, coverage gaps, and improvement opportunities.

## Summary
- Applications: {app_count} ({app_list})
- Total scenarios: {total}
- Active: {active}
- Disabled: {disabled}
- Future (preview): {future}
- Conforma: {conforma}

## Scenarios
Name  |  Application  |  Status  |  Policy  |  Type
{scenarios}

Use the record_scenarios_analysis tool. Focus on:
- Are all expected policies covered? (registry-prod, fbc-prod, chart-prod as applicable)
- Any active scenarios with missing or empty policy references?
- Any duplicate active scenarios pointing to the same policy?
- Are disabled scenarios intentional or potentially forgotten?
- Does the active/disabled pattern make sense?
- If analyzing multiple apps, are there outliers with unusual configurations?
""".format(
            label=label,
            app_count=len(apps),
            app_list=', '.join(apps[:10]) + ('...' if len(apps) > 10 else ''),
            total=len(scenarios),
            active=len(active),
            disabled=len(disabled),
            future=len(future),
            conforma=len(conforma),
            scenarios='\n'.join(scenario_lines),
        )

        return (SCENARIOS_SYSTEM_PROMPT, user_prompt)

    def parse_analysis_response(self, llm_response):
        from pydantic import ValidationError
        from analyzers.models import ScenariosAnalysisResult

        if not llm_response.tool_calls:
            raise ValueError("LLM did not return tool_use response")

        analysis_call = None
        for call in llm_response.tool_calls:
            if call.get('name') == 'record_scenarios_analysis':
                analysis_call = call
                break

        if not analysis_call:
            raise ValueError("LLM did not call record_scenarios_analysis tool")

        input_data = analysis_call.get('input', {})

        try:
            result = ScenariosAnalysisResult(**input_data)
            return result.model_dump()
        except ValidationError as e:
            logger.error("LLM returned invalid scenarios analysis: %s", e)
            return {
                'findings': input_data.get('findings', 'Invalid LLM response'),
                'severity': 'info',
                'confidence_score': 0.0,
                'recommendations': input_data.get('recommendations', 'Manual review required'),
                'issues_count': 0,
            }

    def analyze(self, namespace, app_filter=None):
        logger.info("Fetching ITS scenarios from %s...", namespace)
        scenarios = self.fetch_scenarios(namespace, app_filter=app_filter)

        if not scenarios:
            logger.info("No scenarios found")
            return {'analyzed': False, 'reason': 'no_scenarios'}

        logger.info("Found %d scenarios for %s", len(scenarios), app_filter or "all apps")

        system_prompt, user_prompt = self.build_analysis_prompt(scenarios, app_filter)

        trace = self.langfuse.create_trace(
            name='scenarios-analysis',
            input_data={
                'application': app_filter or 'all',
                'total_scenarios': len(scenarios),
            },
        )

        start_time = time.time()
        response = self.llm.create_message(
            system=system_prompt,
            user_content=user_prompt,
            tools=[SCENARIOS_ANALYSIS_TOOL],
        )
        duration = time.time() - start_time

        self.langfuse.record_generation(
            trace,
            name='analyze-its-scenarios',
            model=self.llm.model_name(),
            prompt=user_prompt[:5000],
            completion=str(response.tool_calls),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            duration_ms=int(duration * 1000),
        )

        analysis = self.parse_analysis_response(response)

        cost_usd = (response.input_tokens * 0.000003) + (response.output_tokens * 0.000015)

        self.langfuse.end_trace(trace, output=analysis)
        self.langfuse.flush()

        logger.info("Analysis complete in %.1fs", duration)
        logger.info("Severity: %s, Issues: %d, Confidence: %.2f",
                     analysis['severity'], analysis['issues_count'],
                     analysis['confidence_score'])
        logger.info("Cost: $%.4f (%d input + %d output tokens)",
                     cost_usd, response.input_tokens, response.output_tokens)

        return {
            'analyzed': True,
            'analysis': analysis,
            'duration': duration,
            'cost_usd': cost_usd,
            'model': self.llm.model_name(),
            'scenarios_count': len(scenarios),
        }

    def run(self, namespace=None, app_filter=None):
        logger.info("=" * 70)
        logger.info("ITS Scenario Configuration Analysis")
        if app_filter:
            logger.info("Application: %s", app_filter)
        logger.info("Namespace: %s", namespace)
        logger.info("=" * 70)

        return self.analyze(namespace, app_filter=app_filter)
