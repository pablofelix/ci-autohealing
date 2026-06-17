"""Secret redaction for logs, AI output, and displayed content.

Scans text for known secret patterns and replaces them with [REDACTED].
Used before storing logs to DB, before LLM prompts, and before display.
"""

import os
import re

_SECRET_PATTERNS = [
    (re.compile(r'sk-ant-[a-zA-Z0-9_-]{20,}'), 'Anthropic API key'),
    (re.compile(r'ghp_[a-zA-Z0-9]{36,}'), 'GitHub PAT'),
    (re.compile(r'ghs_[a-zA-Z0-9]{36,}'), 'GitHub App token'),
    (re.compile(r'github_pat_[a-zA-Z0-9_]{20,}'), 'GitHub fine-grained PAT'),
    (re.compile(r'glpat-[a-zA-Z0-9_-]{20,}'), 'GitLab PAT'),
    (re.compile(r'xoxb-[0-9]{10,}-[a-zA-Z0-9]+'), 'Slack bot token'),
    (re.compile(r'xoxp-[0-9]{10,}-[a-zA-Z0-9]+'), 'Slack user token'),
    (re.compile(r'AKIA[0-9A-Z]{16}'), 'AWS access key'),
    (re.compile(r'eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}'), 'JWT token'),
    (re.compile(r'(?:password|passwd|pwd|secret|token|api_key|apikey|auth)\s*[=:]\s*["\'][^"\']{8,}["\']',
                re.IGNORECASE), 'Hardcoded credential'),
    (re.compile(r'Bearer\s+[a-zA-Z0-9._\-]{20,}'), 'Bearer token'),
    (re.compile(r'Basic\s+[a-zA-Z0-9+/=]{20,}'), 'Basic auth'),
]

_ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')

_REDACTED = '[REDACTED]'


def redact_secrets(text):
    """Remove known secret patterns from text."""
    if not text:
        return text
    for pattern, _ in _SECRET_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


def strip_ansi(text):
    """Remove ANSI escape codes from text."""
    if not text:
        return text
    return _ANSI_ESCAPE.sub('', text)


def sanitize_for_storage(text):
    """Full sanitization pipeline: strip ANSI + redact secrets."""
    if not text:
        return text
    return redact_secrets(strip_ansi(text))


def sanitize_for_llm(text):
    """Sanitize text before including in LLM prompts."""
    if not text:
        return text
    text = strip_ansi(text)
    text = redact_secrets(text)
    return text


def get_safe_env(declared_vars=None):
    """Build a subprocess environment with only safe variables.

    Only passes env vars that are in the declared list (from skill metadata).
    Always includes PATH, HOME, LANG, TERM, USER for basic functionality.
    Never passes tokens/secrets unless explicitly declared.
    """
    safe_keys = {'PATH', 'HOME', 'LANG', 'TERM', 'USER', 'SHELL', 'LC_ALL',
                 'LC_CTYPE', 'PYTHONPATH', 'VIRTUAL_ENV', 'TMPDIR', 'TMP'}

    if declared_vars:
        safe_keys.update(declared_vars)

    env = {}
    for key in safe_keys:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val

    return env
