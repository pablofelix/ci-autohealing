"""Onboarding blocker analyzer using LLM provider.

Analyzes component onboarding progress and diagnoses blockers using
structured report data from onboarding_jira.py utilities.
"""

import logging
import time

from prompt_loader import load_prompt

logger = logging.getLogger(__name__)

ONBOARDING_ANALYSIS_TOOL = {
    'name': 'record_onboarding_analysis',
    'description': 'Record the analysis of a component onboarding blocker',
    'input_schema': {
        'type': 'object',
        'properties': {
            'root_cause': {
                'type': 'string',
                'description': 'What is blocking this onboarding and why',
            },
            'failure_category': {
                'type': 'string',
                'enum': [
                    'automation_stuck',
                    'pr_review_needed',
                    'missing_prerequisite',
                    'configuration_error',
                    'infrastructure_issue',
                    'branch_conflict',
                    'first_build_failing',
                    'manual_intervention',
                    'upstream_dependency',
                ],
                'description': 'Category of the onboarding blocker',
            },
            'confidence_score': {
                'type': 'number',
                'minimum': 0,
                'maximum': 1,
                'description': 'Confidence in this analysis (0.0-1.0)',
            },
            'recommended_fix': {
                'type': 'string',
                'description': 'Specific actions to unblock onboarding',
            },
            'blocked_step': {
                'type': 'string',
                'description': 'Which step is blocked (automation key or Konflux step)',
            },
            'can_auto_fix': {
                'type': 'boolean',
                'description': 'Whether this can be automatically resolved',
            },
            'requires_human_review': {
                'type': 'boolean',
                'description': 'Whether human review is needed',
            },
            'evidence_references': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'type': {'type': 'string', 'enum': ['doc', 'config', 'log', 'policy']},
                        'url': {'type': 'string'},
                        'description': {'type': 'string'},
                    },
                    'required': ['type', 'description'],
                },
            },
            'source_transparency': {
                'type': 'object',
                'properties': {
                    'sources_consulted': {
                        'type': 'array',
                        'items': {'type': 'string'},
                    },
                    'sources_unavailable': {
                        'type': 'array',
                        'items': {'type': 'string'},
                    },
                    'limitations': {
                        'type': 'array',
                        'items': {'type': 'string'},
                    },
                },
            },
        },
        'required': [
            'root_cause',
            'failure_category',
            'confidence_score',
            'recommended_fix',
            'can_auto_fix',
        ],
    },
}

SYSTEM_PROMPT = load_prompt('onboarding_analyzer')


class OnboardingAnalyzer:
    """Analyzes onboarding blockers using an LLM provider."""

    def __init__(self, llm):
        self.llm = llm

    def build_analysis_prompt(self, onboarding_data):
        """Build user prompt from the structured onboarding report."""
        component = onboarding_data.get('component', '?')
        phase = onboarding_data.get('phase', '?')
        progress = onboarding_data.get('progress', 0)
        comp_type = onboarding_data.get('type', 'unknown')

        sections = []
        sections.append('## Component: {}'.format(component))
        sections.append('- Type: {}'.format(comp_type))
        sections.append('- Phase: {}'.format(phase))
        sections.append('- Progress: {}%'.format(progress))

        # Jira tickets
        tickets = onboarding_data.get('jira_tickets', [])
        if tickets:
            sections.append('\n## Jira Tickets')
            for t in tickets:
                sections.append('- {} ({}) — {} [{}]'.format(
                    t.get('key', ''), t.get('type', ''),
                    t.get('summary', '')[:80], t.get('status', '')))

        # Konflux steps
        steps = onboarding_data.get('steps', [])
        if steps:
            sections.append('\n## Konflux Steps')
            for s in steps:
                status = s.get('status', '?')
                label = s.get('label', s.get('step', '?'))
                detail = s.get('detail', '')
                fix = s.get('fix', '')
                line = '- [{}] {}'.format(status.upper(), label)
                if detail:
                    line += ': {}'.format(detail)
                if fix:
                    line += ' (fix: {})'.format(fix)
                sections.append(line)

        # Automation steps
        automation = onboarding_data.get('automation_steps', [])
        if automation:
            sections.append('\n## Automation Steps (Jira bot)')
            for s in automation:
                status = s.get('status', 'pending')
                label = s.get('label', '?')
                matched = s.get('matched_label', '')
                prs = s.get('pr_links', [])
                line = '- [{}] {}'.format(status.upper(), label)
                if matched:
                    line += ' (label: {})'.format(matched)
                if prs:
                    line += ' PR: {}'.format(prs[0])
                sections.append(line)

        # Bot error analysis
        bot_analysis = onboarding_data.get('bot_error_analysis', {})
        if bot_analysis and bot_analysis.get('has_errors'):
            sections.append('\n## Bot Error History')
            sections.append('Total errors: {}'.format(
                bot_analysis.get('retry_count', 0)))
            for cat in bot_analysis.get('error_categories', []):
                sections.append('- {} ({}x): {}'.format(
                    cat['category'], cat['count'], cat['description']))
                sections.append('  Automation fix: {}'.format(
                    cat.get('automation_fix', 'none')))
            stuck = bot_analysis.get('stuck_steps', {})
            if stuck:
                sections.append('\nStuck steps (3+ consecutive failures):')
                for step, count in sorted(stuck.items(), key=lambda x: -x[1]):
                    sections.append('- {}: {} failures'.format(step, count))
            timeline = bot_analysis.get('error_timeline', [])
            if timeline:
                sections.append('\nRecent errors (last 5):')
                for e in timeline[-5:]:
                    sections.append('- [{}] step={} cats={} — {}'.format(
                        e.get('timestamp', '')[:19],
                        e.get('step', '?'),
                        ','.join(e.get('categories', [])),
                        e.get('excerpt', '')[:120]))

        # ODH bot errors
        odh_bot = onboarding_data.get('odh_bot_error_analysis', {})
        if odh_bot and odh_bot.get('has_errors'):
            sections.append('\n## ODH Bot Error History')
            sections.append('Total errors: {}'.format(
                odh_bot.get('retry_count', 0)))
            for cat in odh_bot.get('error_categories', []):
                sections.append('- {} ({}x): {}'.format(
                    cat['category'], cat['count'], cat['description']))

        # Heuristic analysis
        analysis = onboarding_data.get('analysis', {})
        if analysis:
            sections.append('\n## Heuristic Analysis')
            sections.append('- Status: {}'.format(analysis.get('status', '?')))
            if analysis.get('blocked_at'):
                sections.append('- Blocked at: {}'.format(
                    analysis['blocked_at']))
                sections.append('- Reason: {}'.format(
                    analysis.get('blocked_reason', '?')))
                sections.append('- Impact: {}'.format(
                    analysis.get('impact', '?')))
            if analysis.get('fix_component'):
                sections.append('- Fix (component): {}'.format(
                    analysis['fix_component']))
            if analysis.get('fix_automation'):
                sections.append('- Fix (automation): {}'.format(
                    analysis['fix_automation']))

        # Report action items
        report = onboarding_data.get('report', {})
        items = report.get('action_items', [])
        if items:
            sections.append('\n## Action Items')
            for item in items:
                sections.append('- [{}] {}'.format(
                    item.get('priority', 'MED'), item['action']))

        # Nudge PRs
        nudge_prs = onboarding_data.get('nudge_prs', [])
        if nudge_prs:
            sections.append('\n## Nudge PRs (dependency updates)')
            for pr in nudge_prs:
                merged = 'merged' if pr.get('merged') else 'open'
                sections.append('- {} {} -> {} [{}] {}'.format(
                    pr.get('package', '?'),
                    pr.get('from_version', '?'),
                    pr.get('to_version', '?'),
                    merged,
                    pr.get('pr_url', '')))

        user_prompt = '\n'.join(sections)
        user_prompt += (
            '\n\nUse the record_onboarding_analysis tool. '
            'Identify the PRIMARY blocker and provide specific actions '
            'to resolve it. Include ic commands when applicable.'
        )

        return (SYSTEM_PROMPT, user_prompt)

    def parse_analysis_response(self, llm_response):
        """Extract structured analysis from tool call response."""
        from pydantic import ValidationError

        from analyzers.models import OnboardingAnalysisResult

        if not llm_response.tool_calls:
            raise ValueError("LLM did not return tool_use response")

        analysis_call = None
        for call in llm_response.tool_calls:
            if call.get('name') == 'record_onboarding_analysis':
                analysis_call = call
                break

        if not analysis_call:
            raise ValueError("LLM did not call record_onboarding_analysis")

        input_data = analysis_call.get('input', {})

        try:
            result = OnboardingAnalysisResult(**input_data)
            return result.model_dump()
        except ValidationError as e:
            logger.error("LLM returned invalid onboarding analysis: %s", e)
            return {
                'root_cause': input_data.get('root_cause', 'Invalid response'),
                'failure_category': 'manual_intervention',
                'confidence_score': 0.0,
                'recommended_fix': input_data.get('recommended_fix',
                                                  'Manual review required'),
                'blocked_step': input_data.get('blocked_step', ''),
                'can_auto_fix': False,
                'requires_human_review': True,
            }

    def analyze(self, onboarding_data):
        """Full analysis: build prompt -> call LLM -> parse response."""
        system_prompt, user_prompt = self.build_analysis_prompt(onboarding_data)

        start_time = time.time()
        response = self.llm.create_message(
            system=system_prompt,
            user_content=user_prompt,
            tools=[ONBOARDING_ANALYSIS_TOOL],
        )
        duration = time.time() - start_time

        result = self.parse_analysis_response(response)
        result['tokens_used'] = response.input_tokens + response.output_tokens
        cost = ((response.input_tokens * 0.000003) +
                (response.output_tokens * 0.000015))
        result['cost_usd'] = cost
        result['duration'] = duration
        result['model_used'] = self.llm.model_name()

        return result
