"""Log filtering — extract error-relevant lines from build logs."""

import re

ERROR_KEYWORDS = re.compile(
    r'error[:\s]|fatal|failed|failure|exit code|exit \d|traceback|exception|cannot\s|'
    r'warning[:\s]|warn[:\s]|deprecated|'
    r'denied|timeout|killed|oom|no such|not found|permission denied|command not found|'
    r'segmentation fault|segfault|sigsegv|sigkill|sigterm|signal \d|abort|core dump|'
    r'panic|refused|rejected|conflict|broken|missing|undefined|unresolved|'
    r'out of memory|no space|disk full|quota exceeded|import error|module.*not found|'
    r'skipping step because',
    re.IGNORECASE
)


def filter_error_lines(logs, context_lines=20):
    """Keep only lines matching error keywords plus surrounding context.

    Returns filtered log text with gap markers, or the original logs
    if filtering wouldn't remove much (>80% kept).
    """
    if not logs:
        return logs

    lines = logs.split('\n')
    total = len(lines)

    if total <= 100:
        return logs

    keep = set()

    for i in range(min(10, total)):
        keep.add(i)
    for i in range(max(0, total - 30), total):
        keep.add(i)

    for i, line in enumerate(lines):
        if ERROR_KEYWORDS.search(line):
            for j in range(max(0, i - context_lines), min(total, i + context_lines + 1)):
                keep.add(j)

    if len(keep) >= total * 0.8:
        return logs

    result = []
    prev_kept = -1
    for i in sorted(keep):
        if prev_kept >= 0 and i > prev_kept + 1:
            skipped = i - prev_kept - 1
            result.append('... ({} lines filtered) ...'.format(skipped))
        result.append(lines[i])
        prev_kept = i

    return '\n'.join(result)
