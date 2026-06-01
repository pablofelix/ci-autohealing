#!/usr/bin/env python3
"""Interactive Jira reply refinement with session-level meta-learning.

Called by `ic jira inbox refine <N>` after ic resolves the index to
(jira_key, comment_id).

Usage:
    python3 jira_refine.py <jira_key> <comment_id>

Flow:
  1. Load current draft from DB + original comment from Jira API
  2. Interactive loop: show draft → user feedback → LLM revises → repeat
  3. On 'ok': persist final draft to DB
  4. Meta-learning: LLM analyzes session for systematic patterns
  5. If pattern found: propose prompt addition, ask confirmation, update file
"""

import json
import sys
import textwrap
from pathlib import Path

from config import CollectorConfig
from clients.jira_client import JiraClient
from clients.llm_provider import create_llm_provider
from logger import setup_logger
from prompt_loader import load_prompt
from repositories import DatabaseConnection, JiraCommentDraftRepository

logger = setup_logger(__name__)

REFINER_PROMPT  = load_prompt('jira_reply_refiner')
ANALYZER_PROMPT = load_prompt('jira_prompt_analyzer')

PROMPTS_DIR = Path(__file__).parent.parent / 'prompts'
DRAFTER_FILE = PROMPTS_DIR / 'jira_reply_drafter.md'

WIDTH = 72


def wrap(text, indent='    '):
    lines = []
    for paragraph in text.splitlines():
        if paragraph.strip():
            lines.extend(textwrap.wrap(paragraph, WIDTH - len(indent)))
        else:
            lines.append('')
    return '\n'.join(indent + line for line in lines)


def display_draft(draft, label='Current draft:'):
    print()
    print('  \033[1m{}\033[0m'.format(label))
    print(wrap(draft))
    print()


def refine_draft(llm, original_comment, current_draft, feedback, history):
    """Call LLM to revise the draft based on user feedback."""
    history_section = ''
    if history:
        history_section = '\n\n## Previous revisions\n' + '\n'.join(
            '  Feedback: {}\n  Draft: {}'.format(h['feedback'], h['draft'][:300])
            for h in history
        )

    user_prompt = (
        '## Original Jira comment\n{comment}'
        '\n\n## Current draft\n{draft}'
        '{history}'
        '\n\n## User feedback\n{feedback}'
        '\n\nProduce the improved draft only — no preamble or explanation.'
    ).format(
        comment=original_comment,
        draft=current_draft,
        history=history_section,
        feedback=feedback,
    )

    resp = llm.create_message(
        system=REFINER_PROMPT,
        user_content=user_prompt,
        tools=None,
        max_tokens=512,
    )
    return resp.content.strip()


def analyze_session(llm, original_draft, history, final_draft):
    """Analyze refinement session for systematic prompt improvements."""
    if not history:
        return None

    drafter_content = DRAFTER_FILE.read_text(encoding='utf-8') if DRAFTER_FILE.exists() else ''

    revision_log = '\n'.join(
        '[{}] Feedback: "{}"\n    Draft after: {}'.format(
            i + 1, h['feedback'], h['draft'][:400]
        )
        for i, h in enumerate(history)
    )

    user_prompt = (
        '## Current reply drafter system prompt\n{drafter}'
        '\n\n## Original draft (before refinement)\n{original}'
        '\n\n## Refinement session\n{log}'
        '\n\n## Final accepted draft\n{final}'
    ).format(
        drafter=drafter_content,
        original=original_draft,
        log=revision_log,
        final=final_draft,
    )

    resp = llm.create_message(
        system=ANALYZER_PROMPT,
        user_content=user_prompt,
        tools=None,
        max_tokens=256,
    )

    try:
        return json.loads(resp.content.strip())
    except (json.JSONDecodeError, ValueError):
        logger.debug("Meta-learning response was not valid JSON: %s", resp.content[:200])
        return None


def apply_prompt_change(proposed_text):
    """Append the proposed text to jira_reply_drafter.md after user confirms."""
    if not DRAFTER_FILE.exists():
        print('\033[31mError: {} not found\033[0m'.format(DRAFTER_FILE))
        return False

    content = DRAFTER_FILE.read_text(encoding='utf-8')
    updated = content.rstrip('\n') + '\n- ' + proposed_text.lstrip('- ').rstrip() + '\n'
    DRAFTER_FILE.write_text(updated, encoding='utf-8')
    return True


def get_comment_text(jira, jira_key, comment_id):
    """Return (author_display_name, body_text) for a comment."""
    data = jira.get_comment(jira_key, comment_id)
    if not data:
        return 'Unknown', '[comment not available]'
    author = (data.get('author') or {}).get('displayName', 'Unknown')
    body = (data.get('body') or '').strip()
    return author, body


def main():
    if len(sys.argv) != 3:
        print('Usage: jira_refine.py <jira_key> <comment_id>', file=sys.stderr)
        sys.exit(1)

    jira_key   = sys.argv[1]
    comment_id = int(sys.argv[2])

    config = CollectorConfig.from_env()
    if not config.jira:
        print('\033[31mError: JIRA_EMAIL and JIRA_TOKEN not configured.\033[0m', file=sys.stderr)
        sys.exit(1)
    if not config.llm:
        print('\033[31mError: LLM_PROVIDER not configured.\033[0m', file=sys.stderr)
        sys.exit(1)

    db         = DatabaseConnection(config.db)
    draft_repo = JiraCommentDraftRepository(db)
    jira       = JiraClient(
        base_url=config.jira.base_url,
        email=config.jira.email,
        token=config.jira.token,
        project=config.jira.project,
    )
    llm = create_llm_provider(config.llm)

    # Load current draft
    existing = draft_repo.get_unreviewed()
    current_draft = next(
        (r['draft_response'] for r in existing
         if r['jira_key'] == jira_key and r['comment_id'] == comment_id),
        None,
    )
    if current_draft is None:
        print('\033[31mError: No unreviewed draft for {} comment {}.\033[0m'.format(
            jira_key, comment_id), file=sys.stderr)
        sys.exit(1)

    author, comment_body = get_comment_text(jira, jira_key, comment_id)

    print()
    print('\033[1m{}\033[0m  ·  from \033[1m{}\033[0m'.format(jira_key, author))
    print()
    print('  \033[1mOriginal comment:\033[0m')
    print(wrap(comment_body))

    original_draft = current_draft
    history = []

    display_draft(current_draft)

    while True:
        try:
            raw = input("  Feedback (or 'ok' to accept, 'skip' to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            print('Cancelled.')
            sys.exit(0)

        if raw.lower() in ('ok', 'done', 'accept', ''):
            break

        if raw.lower() in ('skip', 'cancel', 'q', 'quit'):
            print('Cancelled — draft unchanged.')
            sys.exit(0)

        print('\n  \033[36mRefining...\033[0m', end='', flush=True)
        try:
            revised = refine_draft(llm, comment_body, current_draft, raw, history)
        except Exception as e:
            print('\r  \033[31mLLM error: {}\033[0m'.format(str(e)[:80]))
            continue

        history.append({'feedback': raw, 'draft': current_draft})
        current_draft = revised
        print('\r' + ' ' * 30 + '\r', end='')
        display_draft(current_draft, label='Revised draft:')

    # Persist final draft if it changed
    if current_draft != original_draft:
        with db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE jira_comment_drafts SET draft_response = %s
                    WHERE jira_key = %s AND comment_id = %s
                """, (current_draft, jira_key, comment_id))
        print('\033[32m✓ Draft updated.\033[0m  Post it, then run: ic jira inbox done')
    else:
        print('\033[32m✓ Draft accepted unchanged.\033[0m  Post it, then run: ic jira inbox done')

    # Meta-learning: only if at least one revision was made
    if not history:
        sys.exit(0)

    print()
    print('  \033[36mAnalysing session for prompt improvements...\033[0m', end='', flush=True)
    try:
        result = analyze_session(llm, original_draft, history, current_draft)
    except Exception as e:
        print('\r  \033[33mMeta-analysis skipped ({})\033[0m'.format(str(e)[:60]))
        sys.exit(0)

    print('\r' + ' ' * 55 + '\r', end='')

    if not result or not result.get('has_systematic_pattern'):
        sys.exit(0)

    pattern   = result.get('pattern_summary', '')
    proposed  = result.get('proposed_change', '').strip()

    if not proposed:
        sys.exit(0)

    print()
    print('  \033[1mPattern detected:\033[0m  {}'.format(pattern))
    print('  \033[1mProposed addition to jira_reply_drafter.md:\033[0m')
    print('    \033[33m{}\033[0m'.format(proposed))
    print()
    try:
        confirm = input('  Apply this change? [y/N]: ').strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)

    if confirm == 'y':
        if apply_prompt_change(proposed):
            print('\033[32m✓ Prompt updated.\033[0m')
        else:
            print('\033[31mFailed to update prompt file.\033[0m')
    else:
        print('Skipped.')

    sys.exit(0)


if __name__ == '__main__':
    main()
