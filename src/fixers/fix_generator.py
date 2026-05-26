#!/usr/bin/env python3
"""Fix generator for CI build failures.

Two modes:
  --mode pr    Fetch relevant files, call Claude, print unified diff.
               No writes (--execute not yet implemented).
  --mode jira  Read ticket text from stdin, interactive edit loop, POST to Jira.

Usage:
  # PR dry-run (called by ic fix <num>)
  python3 fix_generator.py --mode pr --component odh-mlmd-grpc-server-v3-4 --failure-id 42

  # Jira with edit loop (ic pipes export_jira() output via stdin)
  ic export <num> jira | python3 fix_generator.py --mode jira --component odh-mlmd-grpc-server-v3-4
"""

import argparse
import difflib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

# Add parent directory to path so we can import project modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import CollectorConfig
from clients.github_client import GitHubClient, parse_github_repo
from clients.jira_client import JiraClient
from clients.llm_provider import create_llm_provider
from repositories.connection import DatabaseConnection
from logger import setup_logger
from prompt_loader import load_prompt

logger = setup_logger(__name__)

KONFLUX_CENTRAL_REPO = os.environ.get('KONFLUX_CENTRAL_REPO', '')


def conforma_branch_name(component, conforma_id):
    # type: (str, int) -> str
    return 'ci-autohealing/conforma/{}/{}'.format(component, conforma_id)


FIX_PROMPT_SYSTEM = load_prompt('fix_generator_pr')
_BASE_BRANCH = 'main'

# Matches quay.io refs with a floating tag (no @sha256: digest appended).
# (?![a-z0-9._\-]) forces the tag to end at a real token boundary before the
# (?!@sha256:) lookahead, preventing backtracking into a partial tag that would
# incorrectly match already-pinned refs (e.g. matching "0." in "0.1@sha256:...").
_FLOATING_REF_RE = re.compile(
    r'quay\.io/([a-z0-9][a-z0-9._/\-]*):([a-z0-9][a-z0-9._\-]*)(?![a-z0-9._\-])(?!@sha256:)',
    re.IGNORECASE,
)


def load_failure_and_analysis(db_conn, failure_id=None, component=None, application=None):
    # type: (DatabaseConnection, Optional[int], Optional[str], Optional[str]) -> Tuple[Optional[Dict], Optional[Dict]]
    """Load build failure, AI analysis, pattern data, and resolution history.

    Returns (failure_row, analysis_row). Either can be None.
    failure_row includes enriched_context, pattern_data, and previous_attempts.
    """
    with db_conn.connection() as conn:
        with conn.cursor() as cur:
            if failure_id:
                cur.execute("""
                    SELECT id, component_name, repository_url, branch, commit_sha,
                           error_type, error_message, failed_step_name,
                           LEFT(build_logs, 50000) as build_logs,
                           application, enriched_context
                    FROM build_failures
                    WHERE id = %s
                """, (failure_id,))
            else:
                cur.execute("""
                    SELECT id, component_name, repository_url, branch, commit_sha,
                           error_type, error_message, failed_step_name,
                           LEFT(build_logs, 50000) as build_logs,
                           application, enriched_context
                    FROM build_failures
                    WHERE component_name = %s
                      AND application = %s
                      AND is_resolved = FALSE
                    ORDER BY last_updated_at DESC
                    LIMIT 1
                """, (component, application))

            row = cur.fetchone()
            if not row:
                return None, None

            cols = [d[0] for d in cur.description]
            failure = dict(zip(cols, row))

            cur.execute("""
                SELECT a.failure_category, a.confidence_score, a.root_cause,
                       a.recommended_fix, a.recommended_files, a.can_auto_fix,
                       a.requires_human_review,
                       ep.pattern_name, ep.typical_fix, ep.doc_context,
                       ep.occurrence_count, ep.avg_confidence AS pattern_confidence
                FROM ai_analysis a
                LEFT JOIN error_patterns ep ON ep.id = a.error_pattern_id
                WHERE a.build_failure_id = %s
                ORDER BY a.created_at DESC
                LIMIT 1
            """, (failure['id'],))

            analysis_row = cur.fetchone()
            analysis = None
            if analysis_row:
                analysis_cols = [d[0] for d in cur.description]
                analysis = dict(zip(analysis_cols, analysis_row))

            cur.execute("""
                SELECT attempt_number, changes_description, files_modified,
                       was_successful, verification_notes, status, pr_url
                FROM resolution_attempts
                WHERE build_failure_id = %s
                ORDER BY attempt_number ASC
            """, (failure['id'],))
            prev_cols = [d[0] for d in cur.description]
            failure['previous_attempts'] = [
                dict(zip(prev_cols, r)) for r in cur.fetchall()
            ]

    return failure, analysis


def determine_target_repo(failure, analysis, fix_response):
    # type: (Dict, Optional[Dict], Dict) -> str
    """Return the repo URL to target for the PR."""
    if fix_response.get('target_repo') == 'konflux-central':
        return KONFLUX_CENTRAL_REPO
    return failure['repository_url']


def fetch_files_for_fix(github_client, repo_url, branch, recommended_files):
    # type: (GitHubClient, str, str, List[str]) -> Dict[str, Optional[str]]
    """Fetch current content of files Claude needs to see."""
    parsed = parse_github_repo(repo_url)
    if not parsed:
        return {}

    owner, repo = parsed
    ref = branch or 'main'
    contents = {}

    for path in (recommended_files or []):
        content = github_client.get_file_content(owner, repo, path, ref=ref)
        contents[path] = content
        if content:
            logger.info("Fetched %s (%d chars)", path, len(content))
        else:
            logger.warning("Could not fetch %s from %s@%s", path, repo, ref)

    return contents


def generate_unified_diff(path, old_content, new_content):
    # type: (str, str, str) -> str
    """Generate a unified diff between old and new file content."""
    old_lines = (old_content or '').splitlines()
    new_lines = (new_content or '').splitlines()

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile='a/{}'.format(path),
        tofile='b/{}'.format(path),
        lineterm='',
    )
    return '\n'.join(diff)


def _format_pattern_section(analysis):
    # type: (Dict) -> List[str]
    """Format known pattern data for prompt injection."""
    if not analysis or not analysis.get('pattern_name'):
        return []

    parts = [
        '',
        '## Known Pattern (Institutional Memory)',
        'Pattern: {} ({} occurrences, {:.0%} confidence)'.format(
            analysis['pattern_name'],
            analysis.get('occurrence_count', 0),
            analysis.get('pattern_confidence') or 0,
        ),
    ]
    if analysis.get('typical_fix'):
        parts += ['', '**Previously successful fix:**', analysis['typical_fix']]
    if analysis.get('doc_context'):
        parts += ['', '**Relevant documentation:**', analysis['doc_context'][:1500]]
    return parts


def _format_enrichment_section(enriched_context):
    # type: (Optional[Dict]) -> List[str]
    """Format enriched context (dependency changes, related failures)."""
    if not enriched_context or not isinstance(enriched_context, dict):
        return []

    parts = ['', '## Enrichment Context']

    dep_changes = enriched_context.get('dependency_changes')
    if dep_changes and isinstance(dep_changes, dict):
        parts += ['', '### Dependency Changes']
        for filename, change in dep_changes.items():
            diff = change.get('diff', '') if isinstance(change, dict) else str(change)
            parts += ['**{}:**'.format(filename), diff[:2000]]

    related = enriched_context.get('related_failures')
    if related and isinstance(related, list):
        parts += ['', '### Related Failures']
        for rf in related[:3]:
            cross = ' (cross-app: {})'.format(rf['source_application']) if rf.get('cross_app') else ''
            parts.append('- {} [{}]{}: {}'.format(
                rf.get('component_name', '?'),
                rf.get('failure_category') or rf.get('error_type', '?'),
                cross,
                (rf.get('root_cause') or rf.get('error_message', ''))[:200],
            ))

    return parts


def _format_previous_attempts(attempts):
    # type: (List[Dict]) -> List[str]
    """Format previous resolution attempts for prompt injection."""
    if not attempts:
        return []

    parts = ['', '## Previous Fix Attempts']
    for a in attempts:
        outcome = a.get('status', 'unknown')
        files = ', '.join(a.get('files_modified') or [])
        parts.append('- Attempt #{}: {} — {} [{}]'.format(
            a.get('attempt_number', '?'),
            a.get('changes_description', '?')[:200],
            outcome,
            files or 'no files recorded',
        ))
        if a.get('verification_notes'):
            parts.append('  Notes: {}'.format(a['verification_notes'][:200]))

    parts.append('')
    parts.append('If a previous attempt failed, try a DIFFERENT approach.')
    return parts


def build_fix_prompt(failure, analysis, file_contents):
    # type: (Dict, Optional[Dict], Dict[str, Optional[str]]) -> str
    """Build the user message for Claude's fix generation.

    Includes enriched context, pattern data, and resolution history
    when available — giving the LLM maximum context for generating fixes.
    """
    parts = [
        '## Build Failure',
        'Component: {}'.format(failure['component_name']),
        'Application: {}'.format(failure.get('application', '')),
        'Error type: {}'.format(failure.get('error_type', 'unknown')),
        'Failed step: {}'.format(failure.get('failed_step_name', 'unknown')),
        '',
        '### Error message',
        (failure.get('error_message') or 'N/A')[:2000],
    ]

    if failure.get('build_logs'):
        parts += ['', '### Build logs (truncated)', failure['build_logs'][:10000]]

    if analysis:
        parts += [
            '',
            '## AI Analysis',
            'Category: {}'.format(analysis.get('failure_category', '')),
            'Confidence: {:.0%}'.format(analysis.get('confidence_score') or 0),
            'Root cause: {}'.format(analysis.get('root_cause', '')),
            'Recommended fix: {}'.format(analysis.get('recommended_fix', '')),
        ]

    parts += _format_pattern_section(analysis)
    parts += _format_enrichment_section(failure.get('enriched_context'))
    parts += _format_previous_attempts(failure.get('previous_attempts', []))

    if file_contents:
        parts += ['', '## Current file contents']
        for path, content in file_contents.items():
            parts += [
                '',
                '### {}'.format(path),
                content if content else '(could not fetch — file may not exist or repo is private)',
            ]

    parts += [
        '',
        '## Request',
        'Generate a specific fix for this failure. Return valid JSON only — no markdown, no explanation outside the JSON.',
    ]

    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# PR mode
# ---------------------------------------------------------------------------

def run_pr_mode(config, component, failure_id, application, execute=False):
    # type: (CollectorConfig, Optional[str], Optional[int], str, bool) -> int
    """Fetch files, call Claude, print unified diff.

    With execute=True: creates branch, pushes files, opens PR on GitHub.
    Default (dry-run): prints diff only, no writes.
    """
    if not config.llm:
        print("Error: LLM not configured — set ANTHROPIC_API_KEY or LLM_PROVIDER in .env", file=sys.stderr)
        return 1

    if not config.github_token:
        print("Error: GITHUB_TOKEN not set in .env", file=sys.stderr)
        return 1

    db_conn = DatabaseConnection(config.db)
    failure, analysis = load_failure_and_analysis(db_conn, failure_id, component, application)

    if not failure:
        print("Error: No build failure found for {}".format(component or failure_id), file=sys.stderr)
        return 1

    if not failure.get('repository_url'):
        print("Error: No repository URL in failure record", file=sys.stderr)
        return 1

    # Get recommended files from analysis, or ask Claude to determine them
    recommended = []
    if analysis and analysis.get('recommended_files'):
        rf = analysis['recommended_files']
        if isinstance(rf, list):
            recommended = rf
        elif isinstance(rf, str):
            try:
                recommended = json.loads(rf)
            except Exception:
                recommended = [f.strip() for f in rf.split(',') if f.strip()]

    github = GitHubClient(token=config.github_token)

    # Try component repo first
    file_contents = fetch_files_for_fix(
        github, failure['repository_url'], failure.get('branch'), recommended
    )

    # Also fetch from konflux-central if we have .tekton files
    tekton_files = [f for f in recommended if f.startswith('.tekton/')]
    if tekton_files:
        central_contents = fetch_files_for_fix(
            github, KONFLUX_CENTRAL_REPO, 'main', tekton_files
        )
        file_contents.update({'konflux-central:' + k: v for k, v in central_contents.items()})

    llm = create_llm_provider(config.llm)
    prompt = build_fix_prompt(failure, analysis, file_contents)

    print("\nGenerating fix with Claude...\n")
    response = llm.create_message(
        system=FIX_PROMPT_SYSTEM,
        user_content=prompt,
        max_tokens=4096,
    )

    response_text = response.content if isinstance(response.content, str) else ''

    # Strip markdown code fences if present (```json ... ```)
    clean = response_text.strip()
    if clean.startswith('```'):
        clean = '\n'.join(clean.split('\n')[1:])
    if clean.endswith('```'):
        clean = '\n'.join(clean.split('\n')[:-1])

    try:
        fix = json.loads(clean.strip())
    except Exception:
        print("Claude response (raw):\n{}".format(response_text))
        return 1

    target_repo = determine_target_repo(failure, analysis, fix)
    branch_name = 'ci-autohealing/{}/{}'.format(
        failure['component_name'], failure['id']
    )
    base_branch = failure.get('branch') or 'main'
    if fix.get('target_repo') == 'konflux-central':
        base_branch = 'main'

    header = "  PR DRY-RUN — no changes pushed" if not execute else "  PR EXECUTE — will push to GitHub"
    print("=" * 70)
    print(header)
    print("=" * 70)
    print("Target repo : {}".format(target_repo))
    print("Branch      : {}".format(branch_name))
    print("Base branch : {}".format(base_branch))
    print("PR title    : {}".format(fix.get('pr_title', '')))
    print("Confidence  : {:.0%}".format(fix.get('confidence', 0)))
    if fix.get('target_repo_reason'):
        print("Repo reason : {}".format(fix['target_repo_reason']))
    if fix.get('caveat'):
        print("Caveat      : {}".format(fix['caveat']))
    print()

    for file_fix in fix.get('files', []):
        path = file_fix['path']
        new_content = file_fix.get('new_content', '')
        old_content = file_contents.get(path) or file_contents.get('konflux-central:' + path) or ''

        print("-" * 70)
        print("File   : {}".format(path))
        print("Change : {}".format(file_fix.get('change_summary', '')))
        print("Why    : {}".format(file_fix.get('change_reason', '')))
        print()

        diff = generate_unified_diff(path, old_content, new_content)
        if diff:
            print(diff)
        else:
            print("(no diff — content unchanged or file not fetchable)")
        print()

    print("=" * 70)

    if not execute:
        print("  To create this PR: add --execute flag")
        print("=" * 70)
        return 0

    # --- Execute: create branch, push files, open PR ---
    parsed = parse_github_repo(target_repo)
    if not parsed:
        print("Error: Cannot parse target repo URL: {}".format(target_repo), file=sys.stderr)
        return 1
    owner, repo = parsed

    # Find the SHA of base branch to branch from
    base_sha = github.get_ref_sha(owner, repo, base_branch)
    if not base_sha:
        print("Error: Could not resolve {} branch SHA in {}/{}".format(
            base_branch, owner, repo), file=sys.stderr)
        return 1

    print("Creating branch {}...".format(branch_name))
    if not github.create_branch(owner, repo, branch_name, base_sha):
        print("Error: Failed to create branch", file=sys.stderr)
        return 1

    # Push each file
    files_to_push = fix.get('files', [])
    for file_fix in files_to_push:
        path = file_fix['path']
        new_content = file_fix.get('new_content', '')
        commit_msg = 'fix({}): {}'.format(
            failure['component_name'],
            file_fix.get('change_summary', 'update {}'.format(path)),
        )
        # Fetch existing SHA for update (None if new file)
        existing_sha = github.get_file_sha(owner, repo, path, branch_name)
        print("Pushing {} ...".format(path))
        if not github.put_file(owner, repo, path, new_content, commit_msg,
                               branch_name, existing_sha):
            print("Error: Failed to push {}".format(path), file=sys.stderr)
            return 1

    # Create PR
    pr_body = fix.get('pr_body', '')
    pr_body += '\n\n---\n_Automated fix by ci-autohealing (failure id: {})_'.format(
        failure['id']
    )
    pr_result = github.create_pull_request(
        owner, repo,
        title=fix.get('pr_title', 'fix({}): automated CI fix'.format(failure['component_name'])),
        body=pr_body,
        head=branch_name,
        base=base_branch,
    )
    if not pr_result:
        print("\nError: Failed to create PR — check logs above.", file=sys.stderr)
        return 1

    pr_url = pr_result['url']
    pr_number = pr_result['number']
    print("\nPR created: {}".format(pr_url))

    # Record the attempt in resolution_attempts
    try:
        from repositories.resolution_attempt_repository import ResolutionAttemptRepository
        repo_obj = ResolutionAttemptRepository(db_conn)
        changes_desc = '; '.join(
            f.get('change_summary', f.get('path', ''))
            for f in files_to_push
        )
        attempt_id = repo_obj.record_pr_created(
            build_failure_id=failure['id'],
            pr_url=pr_url,
            pr_number=pr_number,
            pr_branch=branch_name,
            files_modified=[f['path'] for f in files_to_push],
            changes_description=changes_desc,
            notes=fix.get('caveat'),
        )
        logger.info("Recorded resolution attempt #%d", attempt_id)
    except Exception as e:
        logger.warning("Could not record resolution attempt: %s", str(e)[:100])

    return 0


# ---------------------------------------------------------------------------
# Conforma PR mode
# ---------------------------------------------------------------------------

def load_conforma_and_analysis(db_conn, conforma_id=None, component=None, application=None):
    # type: (DatabaseConnection, Optional[int], Optional[str], Optional[str]) -> Tuple[Optional[Dict], Optional[Dict]]
    """Load conforma_results row and any associated AI analysis.

    Returns (conforma_row, analysis_row). Either can be None.
    """
    with db_conn.connection() as conn:
        with conn.cursor() as cur:
            if conforma_id:
                cur.execute("""
                    SELECT id, component_name, repository_url, scenario,
                           violations_count, warnings_count, violation_summary,
                           violation_details, commit_sha, application
                    FROM conforma_results
                    WHERE id = %s
                """, (conforma_id,))
            else:
                cur.execute("""
                    SELECT id, component_name, repository_url, scenario,
                           violations_count, warnings_count, violation_summary,
                           violation_details, commit_sha, application
                    FROM conforma_results
                    WHERE component_name = %s
                      AND application = %s
                      AND is_resolved = FALSE
                    ORDER BY last_updated_at DESC
                    LIMIT 1
                """, (component, application))

            row = cur.fetchone()
            if not row:
                return None, None

            cols = [d[0] for d in cur.description]
            conforma = dict(zip(cols, row))

            cur.execute("""
                SELECT failure_category, confidence_score, root_cause,
                       recommended_fix, can_auto_fix, requires_human_review
                FROM ai_analysis
                WHERE conforma_result_id = %s
                ORDER BY created_at DESC
                LIMIT 1
            """, (conforma['id'],))

            analysis_row = cur.fetchone()
            analysis = None
            if analysis_row:
                analysis_cols = [d[0] for d in cur.description]
                analysis = dict(zip(analysis_cols, analysis_row))

    return conforma, analysis


def parse_deprecated_task_fixes(violation_details):
    # type: (Any) -> List[Dict[str, str]]
    """Extract old→new bundle ref pairs from policy_deprecated_task violation details.

    violation_details is a JSONB object with structure:
      {components: [{violations: [{rule, msg, solution}]}]}

    Returns list of {old_ref, new_ref} dicts. Empty if none found or not parseable.
    """
    if not violation_details:
        return []

    if isinstance(violation_details, str):
        try:
            violation_details = json.loads(violation_details)
        except Exception:
            return []

    fixes = []
    seen = set()

    components = violation_details.get('components', [])
    if not components:
        # Flat structure: {violations: [...]}
        components = [violation_details]

    for component in components:
        for violation in component.get('violations', []):
            if violation.get('rule') != 'policy_deprecated_task':
                continue
            solution = violation.get('solution', '')
            # Pattern: "Replace ... `oci://...@sha256:OLD` with ... `oci://...@sha256:NEW`"
            # or plain text without backticks
            refs = re.findall(
                r'oci://[^\s`\'"]+@sha256:[a-f0-9]+',
                solution,
            )
            if len(refs) >= 2:
                old_ref, new_ref = refs[0], refs[-1]
                key = (old_ref, new_ref)
                if key not in seen and old_ref != new_ref:
                    seen.add(key)
                    fixes.append({'old_ref': old_ref, 'new_ref': new_ref})

    return fixes


def apply_hermetic_fix(content):
    # type: (str) -> str
    """Return content with hermetic pipeline param set to 'true'.

    Handles both 'value: false' and 'value: "false"' forms.
    Returns content unchanged if the param is not found.
    """
    pattern = r'(- name: hermetic\s*\n(\s+)(?:value|default): )(?:"false"|false)(?=\s|$)'
    return re.sub(pattern, r'\1"true"', content)


def _has_prefetch_configured(content):
    # type: (str) -> bool
    return bool(re.search(r'-\s*name:\s*prefetch-dependencies\b', content))


def apply_sbom_vendor_label_fix(content):
    # type: (str) -> str
    """Add 'LABEL vendor="Red Hat, Inc."' to a Containerfile if not already present.

    Inserts after the last existing LABEL line, or before the first
    CMD/ENTRYPOINT/USER instruction, or at the end of the file.
    """
    if re.search(r'^\s*LABEL\s+vendor\s*=', content, re.MULTILINE):
        return content

    vendor_line = 'LABEL vendor="Red Hat, Inc."'
    lines = content.splitlines(keepends=True)

    last_label_idx = None
    fallback_idx = None
    for i, line in enumerate(lines):
        if re.match(r'\s*LABEL\s+', line):
            last_label_idx = i
        elif fallback_idx is None and re.match(r'\s*(CMD|ENTRYPOINT|USER)\b', line):
            fallback_idx = i

    if last_label_idx is not None:
        insert_idx = last_label_idx + 1
    elif fallback_idx is not None:
        insert_idx = fallback_idx
    else:
        insert_idx = len(lines)

    lines.insert(insert_idx, vendor_line + '\n')
    return ''.join(lines)


def apply_rpm_repo_id_fix(content):
    # type: (str) -> str
    """Replace generic RPM repo section IDs with arch-parameterised format.

    [ubi-9-baseos-rpms] → [ubi-9-for-$basearch-baseos-rpms]
    Skips IDs that already contain 'for-'.
    """
    return re.sub(
        r'\[ubi-(\d+)-(?!for-)([a-z][a-z0-9-]*)-rpms\]',
        r'[ubi-\1-for-$basearch-\2-rpms]',
        content,
    )


def find_floating_bundle_refs(content):
    # type: (str) -> List[Tuple[str, str, str]]
    """Return deduplicated list of (full_ref, repo_path, tag) for floating quay.io refs."""
    seen = set()
    results = []
    for m in _FLOATING_REF_RE.finditer(content):
        full_ref = m.group(0)
        if full_ref not in seen:
            seen.add(full_ref)
            results.append((full_ref, m.group(1), m.group(2)))
    return results


def resolve_quay_digest(repo_path, tag):
    # type: (str, str) -> str
    """Resolve a quay.io tag to its content-addressable digest via the v2 registry API.

    Returns 'sha256:...' string, or empty string if resolution fails.
    """
    url = 'https://quay.io/v2/{}/manifests/{}'.format(repo_path, tag)
    req = urllib.request.Request(url)
    req.add_header(
        'Accept',
        'application/vnd.docker.distribution.manifest.v2+json,'
        'application/vnd.oci.image.manifest.v1+json,'
        'application/vnd.docker.distribution.manifest.list.v2+json,'
        'application/vnd.oci.image.index.v1+json',
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.headers.get('Docker-Content-Digest', '')
    except Exception as exc:
        logger.debug("Could not resolve digest for %s:%s — %s", repo_path, tag, exc)
        return ''


def _push_pr_and_record(github, db_conn, conforma, owner, repo,
                        branch_name, changed_files, make_commit_msg,
                        pr_title, pr_body, changes_description,
                        attempted_by='ic-fix'):
    # type: (...) -> int
    """Create branch, push changed_files, open PR, record resolution attempt."""
    base_sha = github.get_ref_sha(owner, repo, _BASE_BRANCH)
    if not base_sha:
        print("Error: Could not resolve {} branch SHA".format(_BASE_BRANCH), file=sys.stderr)
        return 1

    print("Creating branch {}...".format(branch_name))
    if not github.create_branch(owner, repo, branch_name, base_sha):
        print("Error: Failed to create branch", file=sys.stderr)
        return 1

    files_pushed = []
    for path, (_, new_content) in changed_files.items():
        existing_sha = github.get_file_sha(owner, repo, path, branch_name)
        print("Pushing {} ...".format(path))
        if not github.put_file(owner, repo, path, new_content, make_commit_msg(path),
                               branch_name, existing_sha):
            print("Error: Failed to push {}".format(path), file=sys.stderr)
            return 1
        files_pushed.append(path)

    pr_result = github.create_pull_request(
        owner, repo,
        title=pr_title,
        body=pr_body,
        head=branch_name,
        base=_BASE_BRANCH,
    )
    if not pr_result:
        print("\nError: Failed to create PR — check logs above.", file=sys.stderr)
        return 1

    print("\nPR created: {}".format(pr_result['url']))

    try:
        from repositories.resolution_attempt_repository import ResolutionAttemptRepository
        repo_obj = ResolutionAttemptRepository(db_conn)
        attempt_id = repo_obj.record_conforma_pr_created(
            conforma_result_id=conforma['id'],
            pr_url=pr_result['url'],
            pr_number=pr_result['number'],
            pr_branch=branch_name,
            files_modified=files_pushed,
            changes_description=changes_description,
            attempted_by=attempted_by,
        )
        logger.info("Recorded conforma resolution attempt #%d", attempt_id)
    except Exception as e:
        logger.warning("Could not record resolution attempt: %s", str(e)[:100])

    return 0


def run_conforma_hermetic_mode(config, component, conforma_id, application, execute=False, attempted_by='ic-fix'):
    # type: (CollectorConfig, Optional[str], Optional[int], str, bool, str) -> int
    """Fix policy_hermetic_build: set hermetic=true in .tekton YAML files.

    Searches konflux-central first (shared pipeline repo), then the component's
    own repository. Creates a PR against whichever repo has the hermetic param.
    """
    if not config.github_token:
        print("Error: GITHUB_TOKEN not set in .env", file=sys.stderr)
        return 1

    db_conn = DatabaseConnection(config.db)
    conforma, analysis = load_conforma_and_analysis(db_conn, conforma_id, component, application)

    if not conforma:
        print("Error: No conforma violation found for {}".format(component or conforma_id),
              file=sys.stderr)
        return 1

    github = GitHubClient(token=config.github_token)
    comp_name = conforma['component_name']

    # Build list of repos to search: konflux-central first, then component repo
    repos_to_search = []
    central_parsed = parse_github_repo(KONFLUX_CENTRAL_REPO)
    if central_parsed:
        repos_to_search.append((KONFLUX_CENTRAL_REPO, central_parsed[0], central_parsed[1]))
    comp_url = conforma.get('repository_url', '')
    if comp_url:
        comp_parsed = parse_github_repo(comp_url)
        if comp_parsed:
            repos_to_search.append((comp_url, comp_parsed[0], comp_parsed[1]))

    changed_files = {}
    target_repo_url = target_owner = target_repo_name = None

    for repo_url, owner, repo in repos_to_search:
        tekton_files = github.get_directory_listing(owner, repo, '.tekton', ref='main') or []
        yaml_files = [f for f in tekton_files if f.endswith(('.yaml', '.yml'))]

        # Prefer files whose name contains the component name
        comp_yamls = [f for f in yaml_files if comp_name in f]
        search_yamls = comp_yamls if comp_yamls else yaml_files

        for fname in search_yamls:
            path = '.tekton/{}'.format(fname)
            content = github.get_file_content(owner, repo, path, ref='main')
            if not content:
                continue
            if not comp_yamls and comp_name not in content:
                continue
            new_content = apply_hermetic_fix(content)
            if new_content != content:
                changed_files[path] = (content, new_content)

        if changed_files:
            target_repo_url = repo_url
            target_owner = owner
            target_repo_name = repo
            break

    if not changed_files:
        print("No .tekton files with 'hermetic: false' found for {}.".format(comp_name))
        print("Checked:")
        for url, _, _ in repos_to_search:
            print("  {}/.tekton/".format(url))
        print("The hermetic param may already be true, or may not be set in these files.")
        return 1

    comp_id = conforma['id']
    branch_name = conforma_branch_name(comp_name, comp_id)
    is_central = target_repo_url == KONFLUX_CENTRAL_REPO

    # Enabling hermetic=true without prefetch cuts all network access during build.
    has_prefetch = any(
        _has_prefetch_configured(orig)
        for orig, _ in changed_files.values()
    )
    prefetch_warning = (
        '\n\n> [!WARNING]\n'
        '> **prefetch-dependencies not detected** in the patched file(s).\n'
        '> Enabling `hermetic: "true"` blocks all outbound network access during the build.\n'
        '> If this component downloads packages at runtime (rpm, pip, npm, go mod, etc.)\n'
        '> the build will fail after this PR merges.\n'
        '> Configure the `prefetch-dependencies` Tekton task before merging, or confirm\n'
        '> that this component does not require network access during the build step.'
    ) if not has_prefetch else ''

    pr_title = 'fix({}): enable hermetic build'.format(comp_name)
    pr_body = (
        'Fixes policy_hermetic_build conforma violation on `{}`.\n\n'
        'Sets `hermetic: "true"` in {} .tekton pipeline file(s){}.'
        '{}\n\n'
        '---\n_Automated fix by ci-autohealing (conforma id: {})_'
    ).format(
        comp_name,
        len(changed_files),
        ' in konflux-central' if is_central else '',
        prefetch_warning,
        comp_id,
    )

    header = "  HERMETIC DRY-RUN — no changes pushed" if not execute else "  HERMETIC EXECUTE — will push to GitHub"
    print("=" * 70)
    print(header)
    print("=" * 70)
    print("Component    : {}".format(comp_name))
    print("Target repo  : {}/{}".format(target_owner, target_repo_name))
    print("Branch       : {}".format(branch_name))
    print("PR title     : {}".format(pr_title))
    print("Files changed: {}".format(len(changed_files)))
    if not has_prefetch:
        print()
        print("  WARNING: prefetch-dependencies not found in patched file(s).")
        print("  Verify this component does not download packages at build time,")
        print("  or configure prefetch-dependencies before merging.")
    print()

    for path, (old_content, new_content) in changed_files.items():
        print("-" * 70)
        print("File: {}".format(path))
        print()
        diff = generate_unified_diff(path, old_content, new_content)
        if diff:
            print(diff)
        print()

    print("=" * 70)

    if not execute:
        print("  To create this PR: add --execute flag")
        print("=" * 70)
        return 0

    return _push_pr_and_record(
        github, db_conn, conforma, target_owner, target_repo_name,
        branch_name, changed_files,
        lambda path: 'fix({}): enable hermetic build in {}'.format(comp_name, path),
        pr_title, pr_body,
        'Enabled hermetic build in {} file(s)'.format(len(changed_files)),
        attempted_by=attempted_by,
    )


def run_conforma_sbom_vendor_label_mode(config, component, conforma_id, application, execute=False, attempted_by='ic-fix'):
    # type: (CollectorConfig, Optional[str], Optional[int], str, bool, str) -> int
    """Fix policy_sbom_vendor_label: add LABEL vendor="Red Hat, Inc." to Containerfile."""
    if not config.github_token:
        print("Error: GITHUB_TOKEN not set in .env", file=sys.stderr)
        return 1

    db_conn = DatabaseConnection(config.db)
    conforma, analysis = load_conforma_and_analysis(db_conn, conforma_id, component, application)

    if not conforma:
        print("Error: No conforma violation found for {}".format(component or conforma_id),
              file=sys.stderr)
        return 1

    if not conforma.get('repository_url'):
        print("Error: No repository URL in conforma record", file=sys.stderr)
        return 1

    github = GitHubClient(token=config.github_token)
    parsed = parse_github_repo(conforma['repository_url'])
    if not parsed:
        print("Error: Cannot parse repo URL: {}".format(conforma['repository_url']),
              file=sys.stderr)
        return 1
    owner, repo = parsed
    comp_name = conforma['component_name']

    candidate_paths = ['Containerfile', 'Dockerfile', 'containers/Containerfile',
                       'containers/Dockerfile', 'docker/Dockerfile']
    changed_files = {}
    for path in candidate_paths:
        content = github.get_file_content(owner, repo, path, ref='main')
        if not content:
            continue
        new_content = apply_sbom_vendor_label_fix(content)
        if new_content != content:
            changed_files[path] = (content, new_content)
            break

    if not changed_files:
        print("No Containerfile/Dockerfile found missing the vendor label for {}.".format(comp_name))
        print("Either the vendor label is already present, or the file path is non-standard.")
        return 1

    comp_id = conforma['id']
    branch_name = conforma_branch_name(comp_name, comp_id)

    pr_title = 'fix({}): add vendor label to Containerfile'.format(comp_name)
    pr_body = (
        'Fixes policy_sbom_vendor_label conforma violation on `{}`.\n\n'
        'Adds `LABEL vendor="Red Hat, Inc."` to {} Containerfile(s) '
        'to satisfy SBOM metadata requirements.\n\n'
        '---\n_Automated fix by ci-autohealing (conforma id: {})_'
    ).format(comp_name, len(changed_files), comp_id)

    header = "  VENDOR LABEL DRY-RUN — no changes pushed" if not execute else "  VENDOR LABEL EXECUTE — will push to GitHub"
    print("=" * 70)
    print(header)
    print("=" * 70)
    print("Component    : {}".format(comp_name))
    print("Target repo  : {}/{}".format(owner, repo))
    print("Branch       : {}".format(branch_name))
    print("PR title     : {}".format(pr_title))
    print("Files changed: {}".format(len(changed_files)))
    print()

    for path, (old_content, new_content) in changed_files.items():
        print("-" * 70)
        print("File: {}".format(path))
        print()
        diff = generate_unified_diff(path, old_content, new_content)
        if diff:
            print(diff)
        print()

    print("=" * 70)

    if not execute:
        print("  To create this PR: add --execute flag")
        print("=" * 70)
        return 0

    return _push_pr_and_record(
        github, db_conn, conforma, owner, repo,
        branch_name, changed_files,
        lambda path: 'fix({}): add vendor SBOM label to {}'.format(comp_name, path),
        pr_title, pr_body,
        'Added vendor SBOM label to {} Containerfile(s)'.format(len(changed_files)),
        attempted_by=attempted_by,
    )


def run_conforma_rpm_repo_id_mode(config, component, conforma_id, application, execute=False, attempted_by='ic-fix'):
    # type: (CollectorConfig, Optional[str], Optional[int], str, bool, str) -> int
    """Fix policy_rpm_repository: replace generic RPM repo IDs with arch-specific format.

    Searches rpms.in.yaml and any *.repo files in the component's repository root.
    Converts [ubi-N-xxx-rpms] to [ubi-N-for-$basearch-xxx-rpms].
    """
    if not config.github_token:
        print("Error: GITHUB_TOKEN not set in .env", file=sys.stderr)
        return 1

    db_conn = DatabaseConnection(config.db)
    conforma, analysis = load_conforma_and_analysis(db_conn, conforma_id, component, application)

    if not conforma:
        print("Error: No conforma violation found for {}".format(component or conforma_id),
              file=sys.stderr)
        return 1

    if not conforma.get('repository_url'):
        print("Error: No repository URL in conforma record", file=sys.stderr)
        return 1

    github = GitHubClient(token=config.github_token)
    parsed = parse_github_repo(conforma['repository_url'])
    if not parsed:
        print("Error: Cannot parse repo URL: {}".format(conforma['repository_url']),
              file=sys.stderr)
        return 1
    owner, repo = parsed
    comp_name = conforma['component_name']

    root_files = github.get_directory_listing(owner, repo, '', ref='main') or []
    candidate_paths = ['rpms.in.yaml']
    candidate_paths += [f for f in root_files if f.endswith('.repo')]

    changed_files = {}
    for path in candidate_paths:
        content = github.get_file_content(owner, repo, path, ref='main')
        if not content:
            continue
        new_content = apply_rpm_repo_id_fix(content)
        if new_content != content:
            changed_files[path] = (content, new_content)

    if not changed_files:
        print("No files with generic RPM repo IDs found for {}.".format(comp_name))
        print("Checked: {}".format(', '.join(candidate_paths)))
        print("The repo IDs may already use the arch-specific format, or the files "
              "may be in a non-standard location.")
        return 1

    comp_id = conforma['id']
    branch_name = conforma_branch_name(comp_name, comp_id)

    pr_title = 'fix({}): use arch-specific RPM repo IDs'.format(comp_name)
    pr_body = (
        'Fixes policy_rpm_repository conforma violation on `{}`.\n\n'
        'Replaces generic RPM repository section IDs (e.g. `[ubi-9-baseos-rpms]`) '
        'with arch-parameterised format (`[ubi-9-for-$basearch-baseos-rpms]`) '
        'in {} file(s).\n\n'
        '> [!NOTE]\n'
        '> After merging, rebuild `rpms.lock.yaml` with the prefetch-dependencies task:\n'
        '> https://konflux.pages.redhat.com/docs/users/building/prefetching-dependencies.html#rpm\n\n'
        '---\n_Automated fix by ci-autohealing (conforma id: {})_'
    ).format(comp_name, len(changed_files), comp_id)

    header = "  RPM REPO ID DRY-RUN — no changes pushed" if not execute else "  RPM REPO ID EXECUTE — will push to GitHub"
    print("=" * 70)
    print(header)
    print("=" * 70)
    print("Component    : {}".format(comp_name))
    print("Target repo  : {}/{}".format(owner, repo))
    print("Branch       : {}".format(branch_name))
    print("PR title     : {}".format(pr_title))
    print("Files changed: {}".format(len(changed_files)))
    print()

    for path, (old_content, new_content) in changed_files.items():
        print("-" * 70)
        print("File: {}".format(path))
        print()
        diff = generate_unified_diff(path, old_content, new_content)
        if diff:
            print(diff)
        print()

    print("=" * 70)

    if not execute:
        print("  To create this PR: add --execute flag")
        print("=" * 70)
        return 0

    return _push_pr_and_record(
        github, db_conn, conforma, owner, repo,
        branch_name, changed_files,
        lambda path: 'fix({}): use arch-specific RPM repo IDs in {}'.format(comp_name, path),
        pr_title, pr_body,
        'Updated RPM repo IDs to arch-specific format in {} file(s)'.format(len(changed_files)),
        attempted_by=attempted_by,
    )


def parse_untrusted_image_refs(violation_details):
    # type: (Any) -> List[str]
    """Extract old image refs from policy_untrusted_image violation details.

    Returns a list of full refs (quay.io/repo:tag@sha256:digest) that were
    flagged as untrusted. The caller then re-resolves them to fresh digests.
    """
    if not violation_details:
        return []

    if isinstance(violation_details, str):
        try:
            violation_details = json.loads(violation_details)
        except Exception:
            return []

    refs = []
    seen = set()

    components = violation_details.get('components', [])
    if not components:
        components = [violation_details]

    for component in components:
        for violation in component.get('violations', []):
            # Accept any rule name — the category was already classified upstream
            for field in ('msg', 'solution', 'description'):
                text = violation.get(field, '') or ''
                for m in re.finditer(
                    r'(?:oci://)?quay\.io/[^\s`\'"]+@sha256:[a-f0-9]+',
                    text,
                ):
                    ref = m.group(0)
                    if ref not in seen:
                        seen.add(ref)
                        refs.append(ref)

    return refs


def _refresh_pinned_ref(old_ref):
    # type: (str) -> Optional[str]
    """Re-resolve a pinned ref to its tag's current digest.

    Given quay.io/repo:tag@sha256:OLD (or oci://quay.io/...), resolve the
    tag via quay.io API and return the ref with the new digest.
    Returns None if resolution fails or ref is already current.
    """
    m = re.match(
        r'(oci://)?quay\.io/([^:@\s]+):([^@\s]+)@sha256:([a-f0-9]+)',
        old_ref,
    )
    if not m:
        return None
    prefix = m.group(1) or ''
    repo_path, tag, old_digest_hex = m.group(2), m.group(3), m.group(4)

    new_digest = resolve_quay_digest(repo_path, tag)
    if not new_digest:
        return None
    if new_digest == 'sha256:' + old_digest_hex:
        return old_ref  # already current — caller can skip
    return '{}quay.io/{}:{}@{}'.format(prefix, repo_path, tag, new_digest)


def run_conforma_untrusted_image_mode(config, component, conforma_id, application, execute=False, attempted_by='ic-fix'):
    # type: (CollectorConfig, Optional[str], Optional[int], str, bool, str) -> int
    """Fix policy_untrusted_image: update outdated build image refs to fresh digests.

    Parses flagged refs from violation_details, re-resolves each via the quay.io
    v2 API to get the current digest, then applies substitutions in .tekton/*.yaml
    and Containerfile/Dockerfile files, creating a PR with the changes.
    """
    if not config.github_token:
        print("Error: GITHUB_TOKEN not set in .env", file=sys.stderr)
        return 1

    db_conn = DatabaseConnection(config.db)
    conforma, analysis = load_conforma_and_analysis(db_conn, conforma_id, component, application)

    if not conforma:
        print("Error: No conforma violation found for {}".format(component or conforma_id),
              file=sys.stderr)
        return 1

    if not conforma.get('repository_url'):
        print("Error: No repository URL in conforma record", file=sys.stderr)
        return 1

    old_refs = parse_untrusted_image_refs(conforma.get('violation_details'))
    if not old_refs:
        print("Error: Could not extract flagged image refs from violation_details.", file=sys.stderr)
        print("Violation summary: {}".format(conforma.get('violation_summary', 'N/A')))
        print("Use 'ic fix <component>' → Jira for violations that need manual review.")
        return 1

    github = GitHubClient(token=config.github_token)
    parsed = parse_github_repo(conforma['repository_url'])
    if not parsed:
        print("Error: Cannot parse repo URL: {}".format(conforma['repository_url']),
              file=sys.stderr)
        return 1
    owner, repo = parsed
    comp_name = conforma['component_name']

    # Re-resolve each flagged ref to the current digest
    print("Resolving {} flagged ref(s) via quay.io API...".format(len(old_refs)))
    ref_updates = {}   # old_ref -> new_ref or None
    for old_ref in old_refs:
        print("  {} ...".format(old_ref[:60]), end='', flush=True)
        new_ref = _refresh_pinned_ref(old_ref)
        if new_ref and new_ref != old_ref:
            ref_updates[old_ref] = new_ref
            digest_suffix = new_ref.split('@sha256:')[-1][:12] if '@sha256:' in new_ref else ''
            print(" → sha256:{}".format(digest_suffix))
        elif new_ref == old_ref:
            ref_updates[old_ref] = None
            print(" already current")
        else:
            ref_updates[old_ref] = None
            print(" UNRESOLVED")

    updatable = {old: new for old, new in ref_updates.items() if new}
    if not updatable:
        already_current = [o for o, n in ref_updates.items() if n is None and _refresh_pinned_ref(o) == o]
        if already_current:
            print("All flagged refs are already at their current digest. No PR needed.")
        else:
            print("Error: Could not resolve any new digest. Check quay.io connectivity.", file=sys.stderr)
        return 1

    # Search .tekton/*.yaml and Containerfile/Dockerfile for old refs
    candidate_paths = []
    tekton_files = github.get_directory_listing(owner, repo, '.tekton', ref='main') or []
    candidate_paths += ['.tekton/{}'.format(f) for f in tekton_files if f.endswith(('.yaml', '.yml'))]
    candidate_paths += ['Containerfile', 'Dockerfile', 'containers/Containerfile',
                        'containers/Dockerfile', 'docker/Dockerfile']

    file_contents = {}
    for path in candidate_paths:
        content = github.get_file_content(owner, repo, path, ref='main')
        if content:
            file_contents[path] = content

    changed_files = {}
    for path, content in file_contents.items():
        new_content = content
        for old_ref, new_ref in updatable.items():
            new_content = new_content.replace(old_ref, new_ref)
        if new_content != content:
            changed_files[path] = (content, new_content)

    if not changed_files:
        print("No candidate files contained the flagged refs.")
        print("Refs searched for:")
        for old_ref in updatable:
            print("  {}".format(old_ref[:80]))
        return 1

    comp_id = conforma['id']
    branch_name = conforma_branch_name(comp_name, comp_id)

    pr_title = 'fix({}): update outdated build image refs'.format(comp_name)
    pr_body = (
        'Fixes policy_untrusted_image conforma violation on `{}`.\n\n'
        'Updated {} outdated image ref(s) to their current digest in {} file(s).\n\n'
        '---\n_Automated fix by ci-autohealing (conforma id: {})_'
    ).format(comp_name, len(updatable), len(changed_files), comp_id)

    header = ("  UNTRUSTED IMAGE DRY-RUN — no changes pushed"
              if not execute else "  UNTRUSTED IMAGE EXECUTE — will push to GitHub")
    print()
    print("=" * 70)
    print(header)
    print("=" * 70)
    print("Component    : {}".format(comp_name))
    print("Target repo  : {}/{}".format(owner, repo))
    print("Branch       : {}".format(branch_name))
    print("PR title     : {}".format(pr_title))
    print("Refs updated : {}".format(len(updatable)))
    print("Files changed: {}".format(len(changed_files)))
    print()

    for path, (old_content, new_content) in changed_files.items():
        print("-" * 70)
        print("File: {}".format(path))
        print()
        diff = generate_unified_diff(path, old_content, new_content)
        if diff:
            print(diff)
        print()

    print("=" * 70)

    if not execute:
        print("  To create this PR: add --execute flag")
        print("=" * 70)
        return 0

    return _push_pr_and_record(
        github, db_conn, conforma, owner, repo,
        branch_name, changed_files,
        lambda path: 'fix({}): update outdated build image ref in {}'.format(comp_name, path),
        pr_title, pr_body,
        'Updated {} outdated image ref(s) in {} file(s)'.format(
            len(updatable), len(changed_files)),
        attempted_by=attempted_by,
    )


def run_conforma_unpinned_task_mode(config, component, conforma_id, application, execute=False, attempted_by='ic-fix'):
    # type: (CollectorConfig, Optional[str], Optional[int], str, bool, str) -> int
    """Fix policy_unpinned_task: pin floating quay.io task bundle refs to sha256 digests.

    Fetches .tekton/*.yaml from the component repo, calls the quay.io v2 API to
    resolve each floating tag to its current digest, and creates a PR with pinned refs.
    Partially succeeds: creates the PR even if some refs are unresolvable.
    """
    if not config.github_token:
        print("Error: GITHUB_TOKEN not set in .env", file=sys.stderr)
        return 1

    db_conn = DatabaseConnection(config.db)
    conforma, analysis = load_conforma_and_analysis(db_conn, conforma_id, component, application)

    if not conforma:
        print("Error: No conforma violation found for {}".format(component or conforma_id),
              file=sys.stderr)
        return 1

    if not conforma.get('repository_url'):
        print("Error: No repository URL in conforma record", file=sys.stderr)
        return 1

    github = GitHubClient(token=config.github_token)
    parsed = parse_github_repo(conforma['repository_url'])
    if not parsed:
        print("Error: Cannot parse repo URL: {}".format(conforma['repository_url']),
              file=sys.stderr)
        return 1
    owner, repo = parsed
    comp_name = conforma['component_name']

    tekton_files = github.get_directory_listing(owner, repo, '.tekton', ref='main') or []
    yaml_files = [f for f in tekton_files if f.endswith(('.yaml', '.yml'))]

    if not yaml_files:
        print("Error: No .tekton/*.yaml files found in {}/{}".format(owner, repo),
              file=sys.stderr)
        return 1

    file_contents = {}
    for fname in yaml_files:
        path = '.tekton/{}'.format(fname)
        content = github.get_file_content(owner, repo, path, ref='main')
        if content:
            file_contents[path] = content

    # Collect all unique floating refs across all files
    all_refs = {}  # (repo_path, tag) -> full_ref for display
    for content in file_contents.values():
        for full_ref, repo_path, tag in find_floating_bundle_refs(content):
            all_refs[(repo_path, tag)] = full_ref

    if not all_refs:
        print("No floating quay.io bundle refs found in .tekton/*.yaml for {}.".format(comp_name))
        print("All refs may already be pinned with @sha256: digests.")
        return 1

    # Resolve each unique ref to its current digest
    print("Resolving {} unique floating ref(s) via quay.io API...".format(len(all_refs)))
    digest_map = {}   # (repo_path, tag) -> digest string or None
    unresolved = []
    for (repo_path, tag), full_ref in sorted(all_refs.items(), key=lambda x: x[1]):
        print("  {} ...".format(full_ref), end='', flush=True)
        digest = resolve_quay_digest(repo_path, tag)
        if digest:
            digest_map[(repo_path, tag)] = digest
            print(" {}".format(digest[:19]))
        else:
            digest_map[(repo_path, tag)] = None
            unresolved.append(full_ref)
            print(" UNRESOLVED")

    resolved_count = sum(1 for v in digest_map.values() if v)
    if resolved_count == 0:
        print("Error: Could not resolve any digest. Check quay.io connectivity.", file=sys.stderr)
        return 1

    # Pin floating refs in each file using re.sub to avoid double-pinning
    def _pin_ref(m):
        # type: (...) -> str
        digest = digest_map.get((m.group(1), m.group(2)))
        return '{}@{}'.format(m.group(0), digest) if digest else m.group(0)

    changed_files = {}
    for path, content in file_contents.items():
        new_content = _FLOATING_REF_RE.sub(_pin_ref, content)
        if new_content != content:
            changed_files[path] = (content, new_content)

    if not changed_files:
        print("No files were modified (refs may already be pinned).")
        return 1

    comp_id = conforma['id']
    branch_name = conforma_branch_name(comp_name, comp_id)

    unresolved_note = ''
    if unresolved:
        unresolved_note = (
            '\n\n> [!WARNING]\n'
            '> **{} ref(s) could not be resolved** via quay.io API and were left unpinned:\n'
            '{}'
        ).format(
            len(unresolved),
            ''.join('> - `{}`\n'.format(r) for r in unresolved),
        )

    pr_title = 'fix({}): pin task bundle refs to sha256 digests'.format(comp_name)
    pr_body = (
        'Fixes policy_unpinned_task conforma violation on `{}`.\n\n'
        'Pinned {} of {} floating quay.io task bundle ref(s) to their current '
        'content-addressable digest in {} .tekton file(s).{}\n\n'
        '---\n_Automated fix by ci-autohealing (conforma id: {})_'
    ).format(
        comp_name, resolved_count, len(all_refs),
        len(changed_files), unresolved_note, comp_id,
    )

    header = ("  UNPINNED TASK DRY-RUN — no changes pushed"
              if not execute else "  UNPINNED TASK EXECUTE — will push to GitHub")
    print()
    print("=" * 70)
    print(header)
    print("=" * 70)
    print("Component    : {}".format(comp_name))
    print("Target repo  : {}/{}".format(owner, repo))
    print("Branch       : {}".format(branch_name))
    print("PR title     : {}".format(pr_title))
    print("Refs pinned  : {}/{}".format(resolved_count, len(all_refs)))
    print("Files changed: {}".format(len(changed_files)))
    if unresolved:
        print()
        print("  WARNING: {} ref(s) left unresolved (quay.io API unavailable):".format(
            len(unresolved)))
        for r in unresolved:
            print("    {}".format(r))
    print()

    for path, (old_content, new_content) in changed_files.items():
        print("-" * 70)
        print("File: {}".format(path))
        print()
        diff = generate_unified_diff(path, old_content, new_content)
        if diff:
            print(diff)
        print()

    print("=" * 70)

    if not execute:
        print("  To create this PR: add --execute flag")
        print("=" * 70)
        return 0

    return _push_pr_and_record(
        github, db_conn, conforma, owner, repo,
        branch_name, changed_files,
        lambda path: 'fix({}): pin task bundle refs in {}'.format(comp_name, path),
        pr_title, pr_body,
        'Pinned {}/{} floating task bundle refs in {} file(s)'.format(
            resolved_count, len(all_refs), len(changed_files)),
        attempted_by=attempted_by,
    )


def run_conforma_pr_mode(config, component, conforma_id, application, execute=False, attempted_by='ic-fix'):
    # type: (CollectorConfig, Optional[str], Optional[int], str, bool, str) -> int
    """Generate a PR that fixes policy_deprecated_task conforma violations.

    Deterministic fix: parse old→new bundle refs from violation_details,
    fetch .tekton/*.yaml from GitHub, substitute refs, create PR.
    No LLM call — the fix data is self-contained in the violation record.

    With execute=True: creates branch, pushes files, opens PR.
    Default (dry-run): prints diff only, no writes.
    """
    if not config.github_token:
        print("Error: GITHUB_TOKEN not set in .env", file=sys.stderr)
        return 1

    db_conn = DatabaseConnection(config.db)
    conforma, analysis = load_conforma_and_analysis(db_conn, conforma_id, component, application)

    if not conforma:
        print("Error: No conforma violation found for {}".format(component or conforma_id),
              file=sys.stderr)
        return 1

    if not conforma.get('repository_url'):
        print("Error: No repository URL in conforma record", file=sys.stderr)
        return 1

    fixes = parse_deprecated_task_fixes(conforma.get('violation_details'))
    if not fixes:
        print("Error: No policy_deprecated_task violations with parseable bundle refs found.",
              file=sys.stderr)
        print("Violation summary: {}".format(conforma.get('violation_summary', 'N/A')))
        print("Use 'ic fix <component>' choice [1] → Jira for violations that need human review.")
        return 1

    github = GitHubClient(token=config.github_token)
    parsed = parse_github_repo(conforma['repository_url'])
    if not parsed:
        print("Error: Cannot parse repo URL: {}".format(conforma['repository_url']),
              file=sys.stderr)
        return 1
    owner, repo = parsed

    # Fetch all .tekton/*.yaml files
    tekton_files = github.get_directory_listing(owner, repo, '.tekton', ref='main') or []
    yaml_files = [f for f in tekton_files if f.endswith(('.yaml', '.yml'))]

    if not yaml_files:
        print("Error: No .tekton/*.yaml files found in {}/{}".format(owner, repo),
              file=sys.stderr)
        return 1

    file_contents = {}
    for fname in yaml_files:
        path = '.tekton/{}'.format(fname)
        content = github.get_file_content(owner, repo, path, ref='main')
        if content:
            file_contents[path] = content

    # Apply substitutions
    changed_files = {}
    for path, content in file_contents.items():
        new_content = content
        for fix in fixes:
            new_content = new_content.replace(fix['old_ref'], fix['new_ref'])
        if new_content != content:
            changed_files[path] = (content, new_content)

    if not changed_files:
        print("No .tekton files needed updating — bundle refs not found in YAML.")
        print("Refs to replace:")
        for fix in fixes:
            print("  {} → {}".format(fix['old_ref'][-20:], fix['new_ref'][-20:]))
        return 1

    comp_name = conforma['component_name']
    branch_name = conforma_branch_name(comp_name, conforma['id'])

    old_digests = [f['old_ref'].split('@sha256:')[1][:12] for f in fixes]
    new_digests = [f['new_ref'].split('@sha256:')[1][:12] for f in fixes]
    pr_title = 'fix({}): update deprecated task bundles'.format(comp_name)
    pr_body = (
        'Fixes policy_deprecated_task conforma violation on `{}`.\n\n'
        'Updated {} bundle ref(s):\n{}\n\n'
        '---\n_Automated fix by ci-autohealing (conforma id: {})_'
    ).format(
        comp_name,
        len(fixes),
        '\n'.join('- `...{}` → `...{}`'.format(o, n)
                  for o, n in zip(old_digests, new_digests)),
        conforma['id'],
    )

    header = "  CONFORMA DRY-RUN — no changes pushed" if not execute else "  CONFORMA EXECUTE — will push to GitHub"
    print("=" * 70)
    print(header)
    print("=" * 70)
    print("Component   : {}".format(comp_name))
    print("Target repo : {}/{}".format(owner, repo))
    print("Branch      : {}".format(branch_name))
    print("PR title    : {}".format(pr_title))
    print("Bundle fixes: {}".format(len(fixes)))
    print("Files changed: {}".format(len(changed_files)))
    print()

    for path, (old_content, new_content) in changed_files.items():
        print("-" * 70)
        print("File: {}".format(path))
        print()
        diff = generate_unified_diff(path, old_content, new_content)
        if diff:
            print(diff)
        print()

    print("=" * 70)

    if not execute:
        print("  To create this PR: add --execute flag")
        print("=" * 70)
        return 0

    return _push_pr_and_record(
        github, db_conn, conforma, owner, repo,
        branch_name, changed_files,
        lambda path: 'fix({}): update deprecated task bundle in {}'.format(comp_name, path),
        pr_title, pr_body,
        'Updated deprecated task bundle refs in {} file(s)'.format(len(changed_files)),
        attempted_by=attempted_by,
    )


# ---------------------------------------------------------------------------
# Jira mode
# ---------------------------------------------------------------------------

def _open_tty():
    # type: () -> Any
    """Return a readable file for interactive prompts.

    When stdin is a pipe (ticket text), input() reads from stdin which
    is already exhausted after sys.stdin.read(). Open /dev/tty directly
    so interactive prompts still reach the user's keyboard.
    """
    if sys.stdin.isatty():
        return sys.stdin
    try:
        return open('/dev/tty', 'r')
    except OSError:
        return sys.stdin


def _prompt(tty, msg):
    # type: (Any, str) -> str
    """Print msg and read one line from tty (not stdin)."""
    print(msg, end='', flush=True)
    try:
        return tty.readline().rstrip('\n')
    except (EOFError, KeyboardInterrupt):
        raise KeyboardInterrupt


def run_jira_mode(config, component, summary=None):
    # type: (CollectorConfig, str, Optional[str]) -> int
    """Read ticket from stdin, interactive edit loop, POST to Jira."""
    if not config.llm:
        print("Error: LLM not configured — set ANTHROPIC_API_KEY in .env", file=sys.stderr)
        return 1

    # Read initial ticket text from stdin (piped from ic export jira)
    initial_ticket = sys.stdin.read().strip()
    if not initial_ticket:
        print("Error: No ticket text received on stdin", file=sys.stderr)
        return 1

    llm = create_llm_provider(config.llm)
    current_ticket = initial_ticket

    # stdin is now exhausted (pipe); use /dev/tty for interactive prompts
    tty = _open_tty()

    print("\n" + "=" * 70)
    print(current_ticket)
    print("=" * 70)

    # Interactive edit loop
    while True:
        try:
            user_input = _prompt(tty, "\nChange anything? (Enter to post, or describe changes): ").strip()
        except KeyboardInterrupt:
            print("\nAborted.")
            return 0

        if not user_input:
            break

        print("\nApplying changes...")
        edit_prompt = (
            "Here is a Jira ticket:\n\n{}\n\n"
            "The user wants to change: {}\n\n"
            "Return the updated ticket text only — no explanation, no markdown fences."
        ).format(current_ticket, user_input)

        response = llm.create_message(
            system=load_prompt('fix_generator_jira'),
            user_content=edit_prompt,
            max_tokens=2048,
        )

        current_ticket = (response.content if isinstance(response.content, str) else '').strip()
        print("\n" + "=" * 70)
        print(current_ticket)
        print("=" * 70)

    # Confirm before posting
    if not config.jira:
        print("\nJira not configured (set JIRA_EMAIL and JIRA_TOKEN in .env)")
        print("Ticket text saved above — create manually.")
        return 0

    try:
        confirm = _prompt(tty, "\nPost to Jira? [y/N]: ").strip().lower()
    except KeyboardInterrupt:
        print("\nAborted.")
        return 0

    if confirm != 'y':
        print("Not posted. Ticket text saved above.")
        return 0

    # Summary: use passed value, or fall back to a generic title
    if not summary:
        summary = 'Build failure: {}'.format(component)
    body = current_ticket

    jira = JiraClient(
        base_url=config.jira.base_url,
        email=config.jira.email,
        token=config.jira.token,
        project=config.jira.project,
    )

    result = jira.create_issue(
        summary=summary,
        description_text=body,
        issue_type='Bug',
    )

    if result:
        print("\nCreated: {}".format(result['url']))
        return 0
    else:
        print("\nFailed to create Jira issue — check logs above.", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # type: () -> int
    parser = argparse.ArgumentParser(description='CI failure fix generator')
    parser.add_argument('--mode', choices=['pr', 'jira'], required=True)
    parser.add_argument('--component', help='Component name')
    parser.add_argument('--failure-id', type=int, help='build_failures.id (build PR mode)')
    parser.add_argument('--conforma-id', type=int, help='conforma_results.id (conforma PR mode)')
    parser.add_argument('--application', default=None,
                        help='Application name (e.g. acme-v2-0). Auto-derived from DB if omitted.')
    parser.add_argument('--summary', help='Jira issue title (mode=jira). Auto-generated if omitted.')
    parser.add_argument('--execute', action='store_true',
                        help='mode=pr: actually create branch and open PR (default: dry-run)')
    args = parser.parse_args()

    config = CollectorConfig.from_env()

    if args.mode == 'pr':
        if args.conforma_id:
            # Peek at category to pick the right deterministic fixer.
            # Also derive application from the DB row when not passed by caller.
            _db = DatabaseConnection(config.db)
            _conforma, _analysis = load_conforma_and_analysis(
                _db, args.conforma_id, args.component, args.application
            )
            _application = args.application or (_conforma or {}).get('application', 'acme-v2-0')
            _category = (_analysis or {}).get('failure_category', '')
            if _category == 'policy_hermetic_build':
                return run_conforma_hermetic_mode(
                    config, args.component, args.conforma_id,
                    _application, execute=args.execute,
                )
            if _category == 'policy_sbom_vendor_label':
                return run_conforma_sbom_vendor_label_mode(
                    config, args.component, args.conforma_id,
                    _application, execute=args.execute,
                )
            if _category == 'policy_rpm_repository':
                return run_conforma_rpm_repo_id_mode(
                    config, args.component, args.conforma_id,
                    _application, execute=args.execute,
                )
            if _category == 'policy_unpinned_task':
                return run_conforma_unpinned_task_mode(
                    config, args.component, args.conforma_id,
                    _application, execute=args.execute,
                )
            if _category == 'policy_untrusted_image':
                return run_conforma_untrusted_image_mode(
                    config, args.component, args.conforma_id,
                    _application, execute=args.execute,
                )
            return run_conforma_pr_mode(config, args.component, args.conforma_id,
                                        _application, execute=args.execute)

        # Build failure: derive application from DB row when not passed
        _application = args.application
        if not _application and args.failure_id:
            _db = DatabaseConnection(config.db)
            _failure, _ = load_failure_and_analysis(_db, failure_id=args.failure_id)
            _application = (_failure or {}).get('application', 'acme-v2-0')
        _application = _application or 'acme-v2-0'
        return run_pr_mode(config, args.component, args.failure_id, _application,
                           execute=args.execute)
    else:
        return run_jira_mode(config, args.component or '', args.summary)


if __name__ == '__main__':
    sys.exit(main())
