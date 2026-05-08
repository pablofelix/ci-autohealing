#!/usr/bin/env python3.11
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
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add parent directory to path so we can import project modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import CollectorConfig
from clients.github_client import GitHubClient, parse_github_repo
from clients.jira_client import JiraClient
from clients.llm_provider import create_llm_provider
from repositories.connection import DatabaseConnection
from logger import setup_logger

logger = setup_logger(__name__)

KONFLUX_CENTRAL_REPO = 'https://github.com/acme-org/konflux-central'

# Claude prompt for PR fix generation
FIX_PROMPT_SYSTEM = """\
You are an expert in Konflux CI/CD build failures for RHOAI components.
You analyze build failures and generate specific, minimal code fixes.

When asked to fix a build failure:
1. Determine the TARGET REPO: the component's own repo, or konflux-central for shared pipeline configs
2. Identify the exact files to change and what to change
3. Generate the new full content for each file (not just a snippet)
4. Explain clearly what changed and why it will fix the failure

Respond with a JSON object:
{
  "target_repo": "component" | "konflux-central",
  "target_repo_reason": "why this repo",
  "files": [
    {
      "path": "relative/path/to/file",
      "new_content": "full new file content",
      "change_summary": "one-line description of what changed",
      "change_reason": "why this change fixes the failure"
    }
  ],
  "pr_title": "fix(<component>): <short description>",
  "pr_body": "PR description explaining the fix and linking to the failure",
  "confidence": 0.0-1.0,
  "caveat": "any known limitations or things to verify manually"
}
"""


def load_failure_and_analysis(db_conn, failure_id=None, component=None, application=None):
    # type: (DatabaseConnection, Optional[int], Optional[str], Optional[str]) -> Tuple[Optional[Dict], Optional[Dict]]
    """Load build failure and AI analysis from DB.

    Returns (failure_row, analysis_row). Either can be None.
    """
    with db_conn.connection() as conn:
        with conn.cursor() as cur:
            if failure_id:
                cur.execute("""
                    SELECT id, component_name, repository_url, branch, commit_sha,
                           error_type, error_message, failed_step_name,
                           LEFT(build_logs, 50000) as build_logs,
                           application
                    FROM build_failures
                    WHERE id = %s
                """, (failure_id,))
            else:
                cur.execute("""
                    SELECT id, component_name, repository_url, branch, commit_sha,
                           error_type, error_message, failed_step_name,
                           LEFT(build_logs, 50000) as build_logs,
                           application
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

            # Load AI analysis if it exists
            cur.execute("""
                SELECT failure_category, confidence_score, root_cause,
                       recommended_fix, recommended_files, can_auto_fix,
                       requires_human_review
                FROM ai_analysis
                WHERE build_failure_id = %s
                ORDER BY created_at DESC
                LIMIT 1
            """, (failure['id'],))

            analysis_row = cur.fetchone()
            analysis = None
            if analysis_row:
                analysis_cols = [d[0] for d in cur.description]
                analysis = dict(zip(analysis_cols, analysis_row))

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


def build_fix_prompt(failure, analysis, file_contents):
    # type: (Dict, Optional[Dict], Dict[str, Optional[str]]) -> str
    """Build the user message for Claude's fix generation."""
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

def run_pr_mode(config, component, failure_id, application):
    # type: (CollectorConfig, Optional[str], Optional[int], str) -> int
    """Fetch files, call Claude, print unified diff. No writes."""
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

    # Display results
    target_repo = determine_target_repo(failure, analysis, fix)
    branch_name = 'ci-autohealing/{}/{}'.format(
        failure['component_name'], failure['id']
    )

    print("=" * 70)
    print("  PR DRY-RUN — no changes pushed")
    print("=" * 70)
    print("Target repo : {}".format(target_repo))
    print("Branch      : {}".format(branch_name))
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
    print("  To create this PR: --execute flag (not yet implemented)")
    print("=" * 70)

    return 0


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


def run_jira_mode(config, component):
    # type: (CollectorConfig, str) -> int
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
            system='You are editing a Jira ticket. Apply the requested change and return only the updated ticket text.',
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

    # Extract summary (first non-empty line)
    lines = [l for l in current_ticket.splitlines() if l.strip()]
    summary = lines[0].lstrip('#').strip() if lines else 'Build failure: {}'.format(component)
    body = '\n'.join(lines[1:]).strip()

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
    parser = argparse.ArgumentParser(description='CI build failure fix generator')
    parser.add_argument('--mode', choices=['pr', 'jira'], required=True)
    parser.add_argument('--component', help='Component name')
    parser.add_argument('--failure-id', type=int, help='build_failures.id')
    parser.add_argument('--application', default='acme-v2-0')
    args = parser.parse_args()

    config = CollectorConfig.from_env()

    if args.mode == 'pr':
        return run_pr_mode(config, args.component, args.failure_id, args.application)
    else:
        return run_jira_mode(config, args.component or '')


if __name__ == '__main__':
    sys.exit(main())
