"""Build log filtering — extract error context from large logs.

Replicates the bash extract_error_context() logic as a pure function.
"""

import re

_ERROR_PATTERN = re.compile(
    r'\[.*\].*([Ee]rror|[Ff]ailed|[Ff]atal|[Ww]arning|denied|timeout|killed)'
    r'|^(Error|ERROR|ERRO|FATAL|WARNING|WARN|Traceback|Exception|panic|STEP [0-9])'
    r'|(segmentation fault|segfault|sigsegv|sigkill|sigterm|core dump|out of memory|oom'
    r'|exit code [0-9]|exited with|subprocess exited|Skipping step because'
    r'|cannot find|not found|permission denied|command not found|no space'
    r'|disk full|quota exceeded|import error|module.*not found)',
    re.IGNORECASE,
)

CONTEXT_LINES = 5


def extract_failed_step_section(logs, failed_step):
    """Extract the TaskRun section matching the failed step."""
    if not failed_step:
        return logs

    lines = logs.split('\n')
    section = []
    in_section = False

    for line in lines:
        if line.startswith('===== TaskRun:'):
            in_section = failed_step in line
        if in_section:
            section.append(line)

    return '\n'.join(section) if len(section) > 10 else logs


def filter_error_context(logs, failed_step=None, context=CONTEXT_LINES):
    """Filter logs to show only error/warning lines with context.

    Returns (filtered_text, stats_dict).
    For short logs (<80 lines), returns the full log.
    """
    if not logs:
        return '', {'total_lines': 0, 'match_count': 0, 'filtered': False}

    if failed_step:
        logs = extract_failed_step_section(logs, failed_step)

    lines = logs.split('\n')
    total = len(lines)

    if total <= 80:
        return logs, {'total_lines': total, 'match_count': 0, 'filtered': False}

    match_nums = set()
    for i, line in enumerate(lines):
        if _ERROR_PATTERN.search(line):
            match_nums.add(i)

    if not match_nums:
        last_lines = lines[-30:]
        return '\n'.join(last_lines), {
            'total_lines': total, 'match_count': 0, 'filtered': True,
            'note': 'No error patterns found — showing last 30 lines',
        }

    ranges = []
    for num in sorted(match_nums):
        start = max(0, num - context)
        end = min(total - 1, num + context)
        if ranges and start <= ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], end)
        else:
            ranges.append((start, end))

    output = []
    for start, end in ranges:
        if output:
            output.append('  ...')
        for i in range(start, end + 1):
            output.append(lines[i])

    filtered = '\n'.join(output)
    return filtered, {
        'total_lines': total,
        'match_count': len(match_nums),
        'filtered': True,
    }
