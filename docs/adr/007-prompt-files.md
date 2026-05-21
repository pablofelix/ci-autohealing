# ADR 007: Prompts as Markdown Files

**Date:** 2026-05-08
**Status:** Accepted

## Context

The project uses Claude (via LLM provider abstraction) for two distinct tasks:
1. **Analysis** — understanding what went wrong (build failures, conforma violations)
2. **Fix generation** — producing code changes and Jira ticket edits

Each task is driven by a *system prompt* that defines Claude's persona, output format rules, domain knowledge, and constraints. These prompts are substantial (30–300 lines) and need tuning independently of the Python logic around them.

Previously, prompts were hardcoded Python string constants (`SYSTEM_PROMPT = """..."""`) inside the analyzer and fixer modules. Editing them required opening Python files, being careful about string escaping, and mentally filtering prompt content from surrounding code.

## Decision

Extract all system prompts to standalone Markdown files in `prompts/` at the project root. Load them at runtime via a small utility function.

### File layout

```
prompts/
  build_failure_analyzer.md    # Claude's persona when analyzing build failures
  conforma_analyzer.md         # Claude's persona when analyzing conforma violations
  fix_generator_pr.md          # Claude's persona when generating PR code fixes
  fix_generator_jira.md        # Claude's persona when editing Jira ticket text
```

### File format

Each file has a YAML frontmatter block followed by the raw prompt text:

```markdown
---
name: build-failure-analyzer
description: System prompt for Claude analysis of Konflux build failures
version: 1
---

You are a CI/CD troubleshooting specialist...
```

The frontmatter is metadata only — it is stripped before the prompt is sent to Claude.

### Loading

`src/prompt_loader.py` provides a single function (available as `prompt_loader` when running from `src/`):

```python
from prompt_loader import load_prompt

SYSTEM_PROMPT = load_prompt('build_failure_analyzer')
```

The loader reads `prompts/<name>.md`, strips the frontmatter block, and returns the body. If the file is missing, it raises a clear `FileNotFoundError` — silent fallbacks would mask configuration errors.

## Consequences

**Benefits:**
- Prompt text is editable without touching Python code or worrying about string escaping
- Diffs on prompts are clean and readable in git history
- An AI reviewing the codebase can find and understand all Claude instructions in one place
- `version:` in frontmatter enables tracking prompt changes across releases

**Trade-offs:**
- One extra file read per process startup (negligible — file is small and read once at module load)
- Prompts must be deployed alongside the Python code (they are in the same repo, so this is automatic)

## What belongs here vs. in Python

- **`prompts/*.md`** — the static system prompt: persona, tone, output format rules, domain knowledge, confidence scoring rules
- **Python code** — the dynamic user prompt: failure data, file contents, logs assembled at runtime

The user prompt (the per-call message containing actual failure data) stays in Python because it is constructed from runtime data. Only the *system* prompt — which changes rarely and is tuned independently — lives in Markdown.
