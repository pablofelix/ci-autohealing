"""Detect skill execution type from SKILL.md content.

Classifies skills into four types:
- routing: Pure prompt, no code blocks (e.g., triage-conforma)
- script: Standalone executable code blocks (e.g., check-fips)
- workflow: Multi-step instructions with contextual code (e.g., conforma-analyze)
- hybrid: Documentation with embedded examples

The detected type determines how `ic skills run` executes the skill:
- script → SkillExecutor (extract + run code blocks)
- workflow → AgentExecutor (AI follows instructions step by step)
- routing/hybrid → not directly executable
"""

import os
import re


def detect_skill_type(skill_path):
    """Detect skill type from SKILL.md content analysis.

    Args:
        skill_path: Path to the skill directory (containing SKILL.md)

    Returns:
        str: One of 'routing', 'script', 'workflow', 'hybrid'
    """
    skill_md = os.path.join(skill_path, 'SKILL.md')
    if not os.path.isfile(skill_md):
        return 'routing'

    try:
        with open(skill_md) as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return 'routing'

    blocks = _count_code_blocks(content)
    has_executable_blocks = blocks['executable'] > 0
    has_step_instructions = _has_step_pattern(content)
    has_variables = _has_cross_step_variables(content)
    has_conditional_logic = _has_conditional_flow(content)
    total_lines = len(content.split('\n'))

    if not has_executable_blocks and total_lines < 50:
        return 'routing'

    if not has_executable_blocks:
        return 'routing'

    if has_step_instructions and (has_variables or has_conditional_logic):
        return 'workflow'

    if blocks['example'] > 0 and blocks['executable'] == 0:
        return 'hybrid'

    if has_executable_blocks and not has_step_instructions:
        return 'script'

    if has_step_instructions and blocks['executable'] >= 3:
        return 'workflow'

    if has_executable_blocks:
        return 'script'

    return 'hybrid'


def should_use_agent(skill_path, execution_mode=None):
    """Determine if a skill should run via AgentExecutor.

    Args:
        skill_path: Path to skill directory
        execution_mode: Explicit mode from metadata (overrides detection)

    Returns:
        bool: True if agent execution is recommended
    """
    if execution_mode == 'agent':
        return True
    if execution_mode == 'script':
        return False
    return detect_skill_type(skill_path) == 'workflow'


def _strip_frontmatter(content):
    """Remove YAML frontmatter from the start of content."""
    if not content.startswith('---'):
        return content
    lines = content.split('\n')
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            return '\n'.join(lines[i + 1:])
    return content


def _count_code_blocks(content):
    """Count executable vs example code blocks."""
    counts = {'executable': 0, 'example': 0, 'skipped': 0}
    in_block = False
    block_lines = []

    body = _strip_frontmatter(content)

    for line in body.split('\n'):
        stripped = line.strip()

        if stripped.startswith('```') and not in_block:
            lang = stripped[3:].strip().split()[0] if stripped[3:].strip() else ''
            if lang in ('bash', 'sh', 'shell', 'python', 'python3', ''):
                in_block = True
                block_lines = []
            continue

        if stripped.startswith('```') and in_block:
            in_block = False
            code = '\n'.join(block_lines).strip()
            if not code:
                continue
            if code.startswith('# ic:skip') or code.startswith('# example'):
                counts['example'] += 1
            else:
                counts['executable'] += 1
            continue

        if in_block:
            block_lines.append(line)

    return counts


_STEP_PATTERNS = [
    re.compile(r'^#+\s*(step|phase|stage)\s+\d', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\d+\.\s+\*\*', re.MULTILINE),
    re.compile(r'^#+\s*\d+[\.\)]\s', re.MULTILINE),
]


def _has_step_pattern(content):
    """Check if content has numbered step/phase patterns."""
    return any(p.search(content) for p in _STEP_PATTERNS)


_VARIABLE_PATTERNS = [
    re.compile(r'\$[A-Z_]{3,}'),
    re.compile(r'\$\{[A-Z_]{3,}\}'),
]


def _has_cross_step_variables(content):
    """Check for shell variables used across code blocks."""
    all_vars = set()
    for pattern in _VARIABLE_PATTERNS:
        all_vars.update(pattern.findall(content))
    return len(all_vars) >= 2


_CONDITIONAL_PATTERNS = [
    re.compile(r'if\s+.*(?:fail|error|success|pass)', re.IGNORECASE),
    re.compile(r'(?:skip|optional|only if|when)', re.IGNORECASE),
    re.compile(r'depending on|based on the', re.IGNORECASE),
]


def _has_conditional_flow(content):
    """Check for conditional/branching instructions."""
    return any(p.search(content) for p in _CONDITIONAL_PATTERNS)
