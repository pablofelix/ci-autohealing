"""Jira integration for onboarding tracking.

Maps Jira automation labels to onboarding steps, extracts PR links
from bot comments, and computes heuristic analysis of blockers.

The automation bot (devtestops jira bot) in aiops-infra repo drives
RHOAI onboarding and tracks progress via labels on Jira tickets.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

AUTOMATION_STEPS = [
    {
        'key': 'yaml_attached',
        'label': 'YAML attached',
        'done_labels': ['yaml-attached'],
        'in_progress_labels': [],
        'verification': 'Attachment component_onboarding_details.yaml on Jira ticket',
    },
    {
        'key': 'schema_validated',
        'label': 'Schema validated',
        'done_labels': ['validation-successful'],
        'in_progress_labels': [],
        'verification': 'YAML passes onboarding schema validation',
    },
    {
        'key': 'quay_repo',
        'label': 'Quay repo created',
        'done_labels': ['quay-mr-merged'],
        'in_progress_labels': ['quay-mr-raised'],
        'verification': 'Container image pushable to Quay registry',
    },
    {
        'key': 'okc_pr',
        'label': 'odh-konflux-central PR',
        'done_labels': ['okc-pr-merged'],
        'in_progress_labels': ['okc-pr-raised'],
        'verification': 'Component config in odh-konflux-central repo',
    },
    {
        'key': 'krd_mr',
        'label': 'konflux-release-data MR',
        'done_labels': ['krd-mr-merged'],
        'in_progress_labels': ['krd-mr-raised'],
        'verification': 'Component registered in konflux-release-data',
    },
    {
        'key': 'tekton_pr',
        'label': 'Tekton pipeline PR',
        'done_labels': ['tekton-pr-merged'],
        'in_progress_labels': ['tekton-pr-raised'],
        'verification': 'Tekton pipeline config merged',
    },
    {
        'key': 'bundle_integration',
        'label': 'Bundle integration',
        'done_labels': ['bundle-changes-done'],
        'in_progress_labels': ['bundle-pr-raised'],
        'verification': 'relatedImages entry in Build-Config repo',
    },
    {
        'key': 'operator_pr',
        'label': 'Operator integration',
        'done_labels': ['operator-pr-merged'],
        'in_progress_labels': ['operator-pr-raised'],
        'verification': 'Component in operator managed list',
    },
    {
        'key': 'delivery_repo',
        'label': 'Delivery repo',
        'done_labels': ['delivery-repo-created', 'delivery-repo-exists'],
        'in_progress_labels': [],
        'verification': 'Delivery repo exists for product listing',
    },
    {
        'key': 'release_validation',
        'label': 'Release validation',
        'done_labels': ['release-validation-passed'],
        'in_progress_labels': [],
        'verification': 'Final release validation check passed',
    },
    {
        'key': 'auto_merge',
        'label': 'Auto-merge enabled',
        'done_labels': ['auto-merge-enabled'],
        'in_progress_labels': [],
        'verification': 'Auto-merge configured on component PRs',
    },
    {
        'key': 'product_listing',
        'label': 'Product listing',
        'done_labels': ['product-listing-created', 'product-listing-exists'],
        'in_progress_labels': ['product-listing-pr-raised'],
        'verification': 'Delivery repo product listing entry created',
    },
    {
        'key': 'renovate',
        'label': 'Renovate enabled',
        'done_labels': ['renovate-enabled'],
        'in_progress_labels': [],
        'verification': 'MintMaker/Renovate dependency updates enabled',
    },
    {
        'key': 'rkc',
        'label': 'RKC configuration',
        'done_labels': ['rkc-done', 'rkc-merged'],
        'in_progress_labels': ['rkc-pr-raised'],
        'verification': 'RHOAI Konflux Config step completed',
    },
]

_PR_URL_RE = re.compile(
    r'https?://(?:github\.com|gitlab\.cee\.redhat\.com)/[^\s)"\]]+/'
    r'(?:pull|merge_requests)/\d+',
)

# Error patterns extracted from real completed onboardings.
# Each pattern has a regex, category, and description for AI analysis.
ERROR_PATTERNS = [
    {
        'category': 'retry_storm',
        'regex': re.compile(
            r'(?:after \d+ attempts|retry|retrying|will retry)',
            re.IGNORECASE,
        ),
        'description': 'Bot retrying same step repeatedly without escalation',
        'automation_fix': (
            'Add exponential backoff and max-retry cap with human '
            'escalation in aiops-infra onboarder workflow'
        ),
    },
    {
        'category': 'branch_exists',
        'regex': re.compile(
            r'(?:branch .* already exists|git push (?:rejected|failed).*already exists)',
            re.IGNORECASE,
        ),
        'description': 'Bot cannot push branch because it already exists from a previous attempt',
        'automation_fix': (
            'Make branch creation idempotent in aiops-infra — check if branch '
            'exists and reuse it, or force-push with the updated content'
        ),
    },
    {
        'category': 'cluster_connectivity',
        'regex': re.compile(
            r'(?:Dial timeout|Could not (?:log in|connect) to.*cluster|'
            r'SSL certificate|TLS handshake)',
            re.IGNORECASE,
        ),
        'description': 'CI runner cannot reach Konflux cluster or GitLab',
        'automation_fix': (
            'Add cluster health pre-check before running steps that require '
            'cluster access; skip and reschedule instead of failing'
        ),
    },
    {
        'category': 'ci_environment',
        'regex': re.compile(
            r'(?:unable to auto-detect email|git config.*not set|'
            r'evalsymlink failure|no such file or directory.*builds)',
            re.IGNORECASE,
        ),
        'description': 'CI runner environment misconfigured (git identity, workspace paths)',
        'automation_fix': (
            'Ensure CI runner job sets git config user.email/name before '
            'running git operations in aiops-infra pipeline'
        ),
    },
    {
        'category': 'component_not_found',
        'regex': re.compile(
            r'(?:Component .* does NOT exist|component not registered|'
            r'HTTP 422.*component)',
            re.IGNORECASE,
        ),
        'description': 'Script expects component to exist but it is new (first-time onboarding)',
        'automation_fix': (
            'Handle "component not found" as expected state for new onboardings '
            'in run_step_krd.sh — don\'t use set -e for existence checks'
        ),
    },
    {
        'category': 'workflow_dispatch',
        'regex': re.compile(
            r'(?:no workflow run appeared|Could not trigger.*workflow|'
            r'workflow.*dispatch.*failed)',
            re.IGNORECASE,
        ),
        'description': 'GitHub Actions workflow dispatched but no run appeared',
        'automation_fix': (
            'Increase wait timeout from 60s to 180s for workflow dispatch; '
            'add polling with backoff instead of immediate failure'
        ),
    },
    {
        'category': 'dockerfile_missing',
        'regex': re.compile(
            r'(?:Dockerfile Digest Check|Could not fetch the Dockerfile|'
            r'branch .* does not exist yet)',
            re.IGNORECASE,
        ),
        'description': 'Dockerfile not found on target branch (branch may not exist yet)',
        'automation_fix': (
            'Allow validation to pass with warning when branch does not exist '
            'yet — this is a known chicken-and-egg issue for new components'
        ),
    },
    {
        'category': 'vendor_inconsistency',
        'regex': re.compile(
            r'(?:vendor directory inconsistency|go mod vendor|'
            r'hermeto.*strict mode)',
            re.IGNORECASE,
        ),
        'description': 'Go vendor directory out of sync — build prefetch fails',
        'automation_fix': (
            'Post-onboarding checklist should include "run go mod vendor '
            'and commit" for Go components'
        ),
    },
    {
        'category': 'edit_yaml_error',
        'regex': re.compile(
            r'edit_yaml\.py.*(?:invalid choice|error|argument)',
            re.IGNORECASE,
        ),
        'description': 'Operator integration script called with wrong command',
        'automation_fix': (
            'Update edit_yaml.py command mapping in aiops-infra — '
            'new operator types may need different yaml editing commands'
        ),
    },
    {
        'category': 'attachment_missing',
        'regex': re.compile(
            r'(?:attachment .* not found|required attachment|'
            r'component_onboarding_details\.yaml.*not found)',
            re.IGNORECASE,
        ),
        'description': 'YAML attachment not found on Jira ticket',
        'automation_fix': (
            'Template YAML generation should be triggered before validation; '
            'add clear instructions in ticket description'
        ),
    },
    {
        'category': 'http_422',
        'regex': re.compile(
            r'(?:HTTP 422|422.*Unprocessable|status.?code.?422)',
            re.IGNORECASE,
        ),
        'description': 'Konflux API returned 422 — component not registered or invalid request',
        'automation_fix': (
            'Verify component registration before calling Konflux API; '
            'add pre-check in onboarder workflow dispatch'
        ),
    },
    {
        'category': 'pr_merge_conflict',
        'regex': re.compile(
            r'(?:merge conflict|cannot merge|rebase.*fail|'
            r'base branch was modified)',
            re.IGNORECASE,
        ),
        'description': 'PR has merge conflicts or base branch changed',
        'automation_fix': (
            'Auto-rebase PR on conflict or re-create with fresh content'
        ),
    },
    {
        'category': 'build_failure_post_onboarding',
        'regex': re.compile(
            r'(?:build.*fail|pipeline.*fail|PipelineRun.*fail|'
            r'prefetch.*fail|hermeto.*fail)',
            re.IGNORECASE,
        ),
        'description': 'First build after onboarding failed',
        'automation_fix': (
            'Add post-onboarding build health check — verify first PipelineRun '
            'succeeds before marking onboarding complete'
        ),
    },
    {
        'category': 'krd_version_missing',
        'regex': re.compile(
            r'(?:ProjectDevelopmentStream.*not found|'
            r'Sprint onboarding.*pending)',
            re.IGNORECASE,
        ),
        'description': 'KRD version YAML not created yet — sprint onboarding prerequisite missing',
        'automation_fix': (
            'Check if ProjectDevelopmentStream YAML exists before starting '
            'component onboarding to KRD; create it or wait for sprint setup'
        ),
    },
]

_JIRA_BASE = 'https://issues.redhat.com/browse'


def search_onboarding_jira(component, jira_client):
    """Search Jira for onboarding tickets matching component name.

    Returns list of dicts with key, summary, status, labels, url.
    Searches both RHOAI and ODH onboarding tickets.
    """
    if not jira_client:
        return []

    results = []
    for prefix in ['RHOAI Konflux Onboarding', 'ODH Konflux CI Build Onboarding']:
        jql = (
            'project = RHOAIENG '
            'AND summary ~ "{prefix} {comp}" '
            'AND labels in (component-onboarding, devops-onboarding) '
            'ORDER BY created DESC'
        ).format(prefix=prefix, comp=_jql_escape(component))

        try:
            resp = jira_client._session.get(
                jira_client._api('search'),
                params={'jql': jql, 'maxResults': 3,
                        'fields': 'summary,status,labels,comment'},
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            for issue in data.get('issues', []):
                fields = issue.get('fields', {})
                status = fields.get('status', {})
                comments_data = fields.get('comment', {})
                comments = comments_data.get('comments', [])

                results.append({
                    'key': issue.get('key', ''),
                    'summary': fields.get('summary', ''),
                    'status': status.get('name', ''),
                    'status_category': status.get('statusCategory', {}).get(
                        'key', ''),
                    'labels': fields.get('labels', []),
                    'url': '{}/{}'.format(_JIRA_BASE, issue.get('key', '')),
                    'type': 'rhoai' if 'RHOAI' in prefix else 'odh',
                    'comments': comments,
                })
        except Exception as e:
            logger.debug('Jira search failed for %s: %s', component, e)

    return results


def map_labels_to_steps(labels):
    """Map Jira labels to automation step statuses.

    Returns list of step dicts with key, label, status, verification.
    Status is 'done', 'in_progress', or 'pending'.
    """
    label_set = set(labels) if labels else set()
    steps = []

    for step_def in AUTOMATION_STEPS:
        status = 'pending'
        matched_label = None

        for dl in step_def['done_labels']:
            if dl in label_set:
                status = 'done'
                matched_label = dl
                break

        if status == 'pending':
            for ipl in step_def['in_progress_labels']:
                if ipl in label_set:
                    status = 'in_progress'
                    matched_label = ipl
                    break

        steps.append({
            'key': step_def['key'],
            'label': step_def['label'],
            'status': status,
            'matched_label': matched_label,
            'verification': step_def['verification'],
        })

    return steps


def extract_pr_links(comments):
    """Extract PR/MR URLs from Jira bot comments.

    Returns dict mapping step key to list of URLs found in comments.
    """
    pr_links = {}
    all_urls = []

    for comment in comments:
        body = comment.get('body', '')
        urls = _PR_URL_RE.findall(body)
        for url in urls:
            all_urls.append({
                'url': url,
                'author': comment.get('author', {}).get('displayName', ''),
                'created': comment.get('created', ''),
            })

    for url_info in all_urls:
        url = url_info['url']
        step_key = _classify_pr_url(url)
        if step_key:
            pr_links.setdefault(step_key, []).append(url_info)

    return pr_links


def analyze_bot_comments(comments):
    """Analyze bot comments for error patterns from real onboarding failures.

    Returns dict with:
      - error_categories: list of detected error categories with counts
      - retry_count: total retry attempts detected
      - error_timeline: chronological list of errors with timestamps
      - stuck_steps: steps that failed 3+ times consecutively
      - resolution_hints: how similar errors were resolved in past onboardings
    """
    bot_comments = [
        c for c in (comments or [])
        if 'jira bot' in c.get('author', {}).get('displayName', '').lower()
    ]

    error_keywords = [
        'error', 'fail', 'retry', '422', '500', 'exception',
        'timeout', 'not found', 'halted', 'rejected',
    ]

    categories = {}
    error_timeline = []
    step_failures = {}

    for comment in bot_comments:
        body = comment.get('body', '')
        body_lower = body.lower()

        is_error = any(kw in body_lower for kw in error_keywords)
        if not is_error:
            continue

        created = comment.get('created', '')
        step_name = _extract_step_from_error(body)


        matched_patterns = []
        for pattern in ERROR_PATTERNS:
            if pattern['regex'].search(body):
                cat = pattern['category']
                if cat not in categories:
                    categories[cat] = {
                        'category': cat,
                        'description': pattern['description'],
                        'automation_fix': pattern['automation_fix'],
                        'count': 0,
                        'first_seen': created,
                        'last_seen': created,
                    }
                categories[cat]['count'] += 1
                categories[cat]['last_seen'] = created
                matched_patterns.append(cat)

        step_failures.setdefault(step_name, []).append(created)

        error_timeline.append({
            'timestamp': created,
            'step': step_name,
            'categories': matched_patterns or ['unclassified'],
            'excerpt': body[:200],
        })

    stuck_steps = {
        step: len(times)
        for step, times in step_failures.items()
        if len(times) >= 3
    }

    sorted_cats = sorted(
        categories.values(), key=lambda c: -c['count'])

    return {
        'error_categories': sorted_cats,
        'retry_count': sum(c['count'] for c in sorted_cats),
        'error_timeline': error_timeline,
        'stuck_steps': stuck_steps,
        'has_errors': len(error_timeline) > 0,
    }


def compute_heuristic_analysis(konflux_steps, automation_steps, pr_links=None):
    """Compute heuristic analysis of onboarding blockers.

    Returns dict with blocked_at, impact, fix_component, fix_automation.
    Uses rule-based logic (no LLM).
    """
    blocked_at = None
    blocked_reason = None

    for step in automation_steps:
        if step['status'] == 'in_progress':
            blocked_at = step
            blocked_reason = _step_in_progress_reason(step, pr_links)
            break
        if step['status'] == 'pending':
            prev_done = all(
                s['status'] == 'done'
                for s in automation_steps
                if automation_steps.index(s) < automation_steps.index(step)
            )
            if prev_done:
                blocked_at = step
                blocked_reason = 'Step not started despite previous steps complete'
            break

    konflux_blocked = None
    for step in konflux_steps:
        if step.get('status') in ('blocked', 'failed'):
            konflux_blocked = step
            break

    if not blocked_at and not konflux_blocked:
        return {
            'status': 'on_track',
            'summary': 'No blockers detected',
        }

    analysis = {'status': 'blocked'}

    if blocked_at:
        pending_count = sum(
            1 for s in automation_steps if s['status'] == 'pending')
        analysis['blocked_at'] = blocked_at['label']
        analysis['blocked_step_key'] = blocked_at['key']
        analysis['blocked_reason'] = blocked_reason
        analysis['impact'] = '{} downstream step(s) blocked'.format(
            pending_count)

        fix_comp, fix_auto = _suggest_fixes(blocked_at, pr_links)
        analysis['fix_component'] = fix_comp
        analysis['fix_automation'] = fix_auto

    if konflux_blocked:
        analysis['konflux_blocked_at'] = konflux_blocked.get('step', '')
        analysis['konflux_blocked_detail'] = konflux_blocked.get('detail', '')
        analysis['konflux_fix'] = konflux_blocked.get('fix', '')

    return analysis


def build_diff_data(konflux_steps, automation_steps):
    """Build expected-vs-actual diff for all steps.

    Returns list of dicts with step, expected, actual, status.
    """
    diffs = []

    for step in konflux_steps:
        expected = _konflux_step_expected(step)
        actual = _konflux_step_actual(step)
        diffs.append({
            'section': 'konflux',
            'step': step.get('step', step.get('key', '')),
            'label': step.get('label', ''),
            'status': step.get('status', 'unknown'),
            'expected': expected,
            'actual': actual,
        })

    for step in automation_steps:
        expected = _automation_step_expected(step)
        actual = _automation_step_actual(step)
        diffs.append({
            'section': 'automation',
            'step': step['key'],
            'label': step['label'],
            'status': step['status'],
            'expected': expected,
            'actual': actual,
        })

    return diffs


def get_jira_client_from_env():
    """Create a JiraClient from environment variables, or None."""
    from clients.jira_client import JiraClient
    base_url = os.environ.get('JIRA_BASE_URL', 'https://issues.redhat.com')
    email = os.environ.get('JIRA_EMAIL', '')
    token = os.environ.get('JIRA_TOKEN', '')
    project = os.environ.get('JIRA_PROJECT', 'RHOAIENG')

    if not token:
        return None

    return JiraClient(base_url, email, token, project)


_STEP_NAME_RE = re.compile(
    r'(?:step[: ]*(?:\d+\w*)?[: (]*)'
    r'([\w-]+(?:[\w_-]+[\w]))',
    re.IGNORECASE,
)

_KNOWN_STEP_ALIASES = {
    'create-quay-repo': 'quay_repo',
    'quay': 'quay_repo',
    'krd': 'krd_mr',
    'onboard-component-to-konflux-release-data': 'krd_mr',
    'okc': 'okc_pr',
    'run-odh-konflux-onboarder-workflow': 'onboarder_workflow',
    'onboarder_workflow': 'onboarder_workflow',
    'odh-konflux-onboarder': 'onboarder_workflow',
    'integrate-component-with-odh-operator': 'operator_pr',
    'operator': 'operator_pr',
    'bundle': 'bundle_integration',
    'enable-renovate-on-rhoai-component-repo': 'renovate',
    'renovate': 'renovate',
    'product_listing': 'product_listing',
    'product-listing': 'product_listing',
    'auto_merge': 'auto_merge',
    'auto-merge': 'auto_merge',
    'delivery_repo': 'delivery_repo',
    'delivery-repo': 'delivery_repo',
    'rkc': 'rkc',
    'rhoai-konflux-config': 'rkc',
}


def _extract_step_from_error(body):
    """Extract normalized step name from bot error message."""
    body_lower = body.lower()

    if 'projectdevelopmentstream' in body_lower:
        return 'krd_mr'

    for alias, normalized in _KNOWN_STEP_ALIASES.items():
        if alias in body_lower:
            return normalized

    match = _STEP_NAME_RE.search(body)
    if match:
        raw = match.group(1).strip('-_ ')
        return _KNOWN_STEP_ALIASES.get(raw, raw)

    return 'unknown'


def build_onboarding_report(component, jira_tickets, automation_steps,
                            pr_links, bot_analysis, analysis):
    """Build a structured report combining all onboarding data layers.

    Returns a dict ready for display by ic CLI or consumption by AI analysis.
    Designed to be useful both for humans (clear action items) and for
    LLM analysis (structured data with context).
    """
    report = {
        'component': component,
        'jira_tickets': [{
            'key': t.get('key', ''),
            'type': t.get('type', ''),
            'status': t.get('status', ''),
            'url': t.get('url', ''),
        } for t in (jira_tickets or [])],
    }

    if automation_steps:
        done = sum(1 for s in automation_steps if s['status'] == 'done')
        total = len(automation_steps)
        report['progress'] = {'done': done, 'total': total,
                              'pct': round(done / total * 100) if total else 0}
        report['current_step'] = next(
            (s for s in automation_steps
             if s['status'] in ('in_progress', 'pending')),
            None,
        )
        report['steps_with_prs'] = [
            {'step': s['key'], 'label': s['label'],
             'pr_links': s.get('pr_links', [])}
            for s in automation_steps
            if s.get('pr_links')
        ]

    if analysis and analysis.get('status') == 'blocked':
        report['blocker'] = {
            'step': analysis.get('blocked_at', ''),
            'reason': analysis.get('blocked_reason', ''),
            'fix_component': analysis.get('fix_component', ''),
            'fix_automation': analysis.get('fix_automation', ''),
        }

    if bot_analysis and bot_analysis.get('has_errors'):
        cats = bot_analysis.get('error_categories', [])
        stuck = bot_analysis.get('stuck_steps', {})
        report['error_history'] = {
            'total_errors': bot_analysis.get('retry_count', 0),
            'top_category': cats[0]['category'] if cats else None,
            'categories': [
                {'name': c['category'], 'count': c['count'],
                 'description': c['description'],
                 'fix': c['automation_fix']}
                for c in cats
            ],
            'stuck_steps': stuck,
        }

        if stuck:
            worst_step = max(stuck.items(), key=lambda x: x[1])
            report['action_items'] = report.get('action_items', [])
            report['action_items'].append({
                'priority': 'HIGH',
                'action': 'Investigate stuck step "{}" ({} consecutive '
                          'failures)'.format(worst_step[0], worst_step[1]),
                'type': 'automation_bug',
            })

        for cat in cats:
            if cat['count'] >= 5:
                report.setdefault('action_items', []).append({
                    'priority': 'HIGH',
                    'action': 'Fix {} pattern in aiops-infra ({} '
                              'occurrences): {}'.format(
                                  cat['category'], cat['count'],
                                  cat['automation_fix']),
                    'type': 'automation_improvement',
                })

    return report


def _jql_escape(s):
    return s.replace('"', '\\"')


def _classify_pr_url(url):
    """Map a PR/MR URL to the automation step it belongs to."""
    url_lower = url.lower()
    if 'app-interface' in url_lower:
        return 'quay_repo'
    if 'odh-konflux-central' in url_lower:
        return 'okc_pr'
    if 'konflux-release-data' in url_lower:
        return 'krd_mr'
    if 'odh-build-config' in url_lower or 'rhoai-build-config' in url_lower:
        return 'bundle_integration'
    if 'opendatahub-operator' in url_lower or 'rhods-operator' in url_lower:
        return 'operator_pr'
    return None


def _step_in_progress_reason(step, pr_links):
    key = step['key']
    matched = step.get('matched_label', '')

    if 'raised' in matched and 'merged' not in matched:
        links = (pr_links or {}).get(key, [])
        if links:
            return 'MR/PR raised but not merged: {}'.format(
                links[0]['url'])
        return 'MR/PR raised but not merged yet'

    return 'Step in progress'


def _suggest_fixes(blocked_step, pr_links):
    """Return (fix_component, fix_automation) suggestions."""
    key = blocked_step['key']
    links = (pr_links or {}).get(key, [])
    link_str = links[0]['url'] if links else ''

    fixes = {
        'yaml_attached': (
            'Attach component_onboarding_details.yaml to Jira ticket',
            'Validate YAML template is documented in onboarding playbook',
        ),
        'schema_validated': (
            'Fix YAML validation errors reported in Jira comments',
            'Update schema if new fields are required (aiops-infra)',
        ),
        'quay_repo': (
            'Review and merge Quay repo MR in app-interface' +
            (' — ' + link_str if link_str else ''),
            'Check app-interface MR template in aiops-infra onboarder',
        ),
        'okc_pr': (
            'Review and merge PR in odh-konflux-central' +
            (' — ' + link_str if link_str else ''),
            'Check OKC PR template in aiops-infra onboarder',
        ),
        'krd_mr': (
            'Review and merge MR in konflux-release-data' +
            (' — ' + link_str if link_str else ''),
            'Add auto-approve or timeout notification for KRD MRs (aiops-infra)',
        ),
        'tekton_pr': (
            'Review and merge Tekton pipeline PR' +
            (' — ' + link_str if link_str else ''),
            'Check Tekton PR template in aiops-infra onboarder',
        ),
        'bundle_integration': (
            'Review and merge bundle config PR' +
            (' — ' + link_str if link_str else ''),
            'Check bundle PR generation in aiops-infra onboarder',
        ),
        'operator_pr': (
            'Review and merge operator manifests PR' +
            (' — ' + link_str if link_str else ''),
            'Check operator PR template in aiops-infra onboarder',
        ),
        'delivery_repo': (
            'Create delivery repo manually or re-trigger automation',
            'Check delivery repo creation logic in aiops-infra',
        ),
        'release_validation': (
            'Investigate why release validation failed — check Jira comments',
            'Review release validation criteria in aiops-infra',
        ),
        'auto_merge': (
            'Enable auto-merge on component PRs via GitHub settings',
            'Check auto-merge step in aiops-infra onboarder workflow',
        ),
        'product_listing': (
            'Create or review product listing PR in delivery repo' +
            (' — ' + link_str if link_str else ''),
            'Check product listing PR template in aiops-infra',
        ),
        'renovate': (
            'Enable Renovate/MintMaker on the component repository',
            'Check Renovate enablement logic in aiops-infra workflow',
        ),
        'rkc': (
            'Review and merge RKC configuration PR' +
            (' — ' + link_str if link_str else ''),
            'Check RKC PR template in aiops-infra onboarder',
        ),
    }

    default = (
        'Check Jira ticket comments for error details',
        'Review automation logs in aiops-infra',
    )

    return fixes.get(key, default)


def _konflux_step_expected(step):
    expectations = {
        'component_cr': 'Component CR exists in namespace',
        'repository': 'spec.source.git.url points to valid repo',
        'branch': 'spec.source.git.revision set to release branch',
        'dockerfile': 'Downstream Dockerfile present in repo',
        'container_image': 'spec.containerImage set to Quay registry',
        'pac': 'PaC Repository CR matching component repo URL',
        'first_build': 'At least 1 PipelineRun with Succeeded=True',
        'nudges': 'spec.build-nudges-ref populated (optional for leaf)',
        'release_plan': 'ReleasePlan CR covers this component',
    }
    return expectations.get(
        step.get('step', step.get('key', '')),
        'Step completed successfully',
    )


def _konflux_step_actual(step):
    status = step.get('status', 'unknown')
    detail = step.get('detail', '')

    if status == 'done':
        return 'OK — {}'.format(detail) if detail else 'OK'
    if status == 'in_progress':
        return 'In progress — {}'.format(detail) if detail else 'In progress'
    if status in ('blocked', 'failed'):
        return 'FAILED — {}'.format(detail) if detail else 'Failed'
    if status == 'pending':
        return 'Not done — {}'.format(detail) if detail else 'Not done yet'
    return detail or 'Unknown'


def _automation_step_expected(step):
    if not isinstance(step, dict):
        return str(step)
    step_def = next(
        (s for s in AUTOMATION_STEPS if s['key'] == step.get('key')),
        None,
    )
    if step_def:
        done_labels = step_def['done_labels']
        return 'Label {} on Jira ticket'.format(' or '.join(done_labels))
    return step.get('verification', 'Step completed')


def _automation_step_actual(step):
    status = step.get('status', 'unknown')
    matched = step.get('matched_label', '')

    if status == 'done':
        return 'OK — label {}'.format(matched) if matched else 'OK'
    if status == 'in_progress':
        return 'In progress — label {} (not merged)'.format(
            matched) if matched else 'In progress'
    return 'No label found'
