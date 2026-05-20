#!/usr/bin/env python3.11
"""Cron orchestrator — runs the collection pipeline.

Replaces collect-comprehensive.sh. Each step runs as a subprocess so
failures are isolated. Steps gated by env vars are skipped when the
required variable is absent.

COST CONSTRAINT: Steps that call LLM APIs (analyze_failures, poll_jira)
are gated by LLM_PROVIDER. Do NOT set LLM_PROVIDER in the cron
environment — run those steps manually with `ic ai analyze` and
`ic jira inbox` to avoid uncontrolled API spend.
"""

import io
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
PYTHON_DIR = PROJECT_DIR / 'collectors' / 'python'
LOG_DIR = PROJECT_DIR / 'logs' / 'cron'
PYTHON = sys.executable

STEPS = [
    {
        'name': 'Comprehensive collector',
        'script': 'collect_comprehensive.py',
        'condition': lambda: True,
    },
    {
        'name': 'Sync component status',
        'script': 'sync_component_status.py',
        'condition': lambda: True,
    },
    {
        'name': 'Verify fix resolutions',
        'script': 'fixers/verify_fixes.py',
        'condition': lambda: bool(os.environ.get('GITHUB_TOKEN')),
        'skip_msg': 'GITHUB_TOKEN not configured',
    },
    {
        'name': 'Update sync status cache',
        'script': 'check_sync_status.py',
        'condition': lambda: True,
    },
    {
        'name': 'Check Conforma test status',
        'script': 'check_conforma_status.py',
        'condition': lambda: True,
    },
    {
        'name': 'Collect Conforma failure details',
        'script': 'collect_conforma.py',
        'condition': lambda: True,
    },
    {
        'name': 'Collect commit context from GitHub',
        'script': 'collect_commit_context.py',
        'condition': lambda: bool(os.environ.get('GITHUB_TOKEN')),
        'skip_msg': 'GITHUB_TOKEN not configured',
    },
    {
        'name': 'AI analysis on new failures',
        'script': 'analyze_failures.py',
        'condition': lambda: bool(os.environ.get('LLM_PROVIDER')),
        'skip_msg': 'LLM_PROVIDER not configured (run manually: ic ai analyze)',
    },
    {
        'name': 'Autonomous conforma fix',
        'script': 'fixers/auto_fix.py',
        'condition': lambda: (
            bool(os.environ.get('GITHUB_TOKEN'))
            and os.environ.get('AUTONOMOUS_MODE') == 'true'
        ),
        'skip_msg': 'requires GITHUB_TOKEN + AUTONOMOUS_MODE=true',
        'silent_skip': True,
    },
    {
        'name': 'Refresh doc context for error patterns',
        'script': 'collect_doc_context.py',
        'condition': lambda: True,
    },
    {
        'name': 'Poll Jira tickets for new comments',
        'script': 'poll_jira_comments.py',
        'condition': lambda: all(
            os.environ.get(v) for v in ('JIRA_EMAIL', 'JIRA_TOKEN', 'LLM_PROVIDER')
        ),
        'skip_msg': 'JIRA_EMAIL, JIRA_TOKEN, or LLM_PROVIDER not configured',
    },
]

MAX_LOG_FILES = 50


class _Tee(io.TextIOBase):
    """Write to both a file and the original stream (like bash `tee`)."""

    def __init__(self, file_handle, stream):
        self._file = file_handle
        self._stream = stream

    def write(self, data):
        self._stream.write(data)
        self._file.write(data)
        return len(data)

    def flush(self):
        self._stream.flush()
        self._file.flush()


def _banner(text):
    print('=' * 72)
    print(text)
    print('=' * 72)
    print()


def _rotate_logs():
    logs = sorted(LOG_DIR.glob('collect-comprehensive-*.log'), reverse=True)
    for old in logs[MAX_LOG_FILES:]:
        old.unlink(missing_ok=True)


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = LOG_DIR / 'collect-comprehensive-{}.log'.format(timestamp)

    with open(log_file, 'w') as f:
        tee = _Tee(f, sys.__stdout__)
        sys.stdout = tee
        sys.stderr = tee

        try:
            _run_pipeline()
        finally:
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__

    _rotate_logs()
    print('Log saved to: {}'.format(log_file))


def _run_pipeline():
    _banner('Comprehensive CI Failure Collection — {}'.format(
        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ))

    total = len(STEPS)
    failed_steps = []

    for i, step in enumerate(STEPS, 1):
        if not step['condition']():
            if not step.get('silent_skip'):
                print('[{}/{}] Skipping {} ({})'.format(
                    i, total, step['name'], step.get('skip_msg', 'disabled')
                ))
                print()
            continue

        _banner('[{}/{}] {}'.format(i, total, step['name']))

        try:
            result = subprocess.run(
                [PYTHON, step['script']],
                cwd=str(PYTHON_DIR),
                timeout=600,
            )
            if result.returncode != 0:
                print('{} failed (non-critical — continuing)'.format(step['name']))
                failed_steps.append(step['name'])
        except subprocess.TimeoutExpired:
            print('{} timed out after 10 minutes — skipping'.format(step['name']))
            failed_steps.append(step['name'])
        print()

    _banner('Summary')

    ic_path = str(PROJECT_DIR / 'ic')
    try:
        subprocess.run([ic_path, 'stats'], timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    print()
    if failed_steps:
        print('Steps that failed: {}'.format(', '.join(failed_steps)))
        print()
    _banner('Collection complete — {}'.format(
        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ))


if __name__ == '__main__':
    main()
