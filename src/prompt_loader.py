"""Load system prompts from prompts/*.md at the project root.

Each file has a YAML frontmatter block (---...---) followed by the prompt body.
The frontmatter is stripped before the text is returned; only the body is sent to Claude.

Raises FileNotFoundError if the named prompt file does not exist — silent fallbacks
would mask configuration errors and make prompt regressions hard to detect.
"""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / 'prompts'


def load_prompt(name):
    """Return the body of prompts/<name>.md with frontmatter stripped."""
    path = _PROMPTS_DIR / f'{name}.md'
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {path}\n"
            f"Expected a file at prompts/{name}.md relative to the project root."
        )

    lines = path.read_text(encoding='utf-8').splitlines(keepends=True)

    # Strip YAML frontmatter block (--- ... ---)
    if lines and lines[0].strip() == '---':
        end = 1
        while end < len(lines) and lines[end].strip() != '---':
            end += 1
        # Skip the closing --- line and one blank line if present
        body_start = end + 1
        if body_start < len(lines) and lines[body_start].strip() == '':
            body_start += 1
        lines = lines[body_start:]

    return ''.join(lines).rstrip('\n')
