# Skill Issue: analyze-mr-component-coverage

## Date: 2026-06-30

## Error

The `conforma_mr_ops.py` script fails with Python 3.6 (system default):

```
  File "scripts/conforma_mr_ops.py", line 3
    from __future__ import annotations
    ^
SyntaxError: future feature annotations is not defined
```

`from __future__ import annotations` requires Python 3.7+.

## Workaround

Use `python3.11` explicitly instead of `python3`:

```bash
python3.11 scripts/conforma_mr_ops.py analyze-coverage \
  --mr-iid 19385 \
  --rule hermetic_task.hermetic \
  --components comp1,comp2
```

## Additional Issue

Even with Python 3.11, the script returned `"coverage_error": "Failed to fetch Merge Request diff"`
when `GITLAB_TOKEN` was set but `glab` was not authenticated. Had to install `glab` and
run `glab auth login --hostname gitlab.cee.redhat.com` first.

The script also failed to parse component names from the diff when run with empty components list
(returned `"missing": [""]` instead of empty list).

## Environment

- System Python: 3.6.8
- Available Python: 3.11.5
- glab: installed to ~/.local/bin/glab (v1.52.0)
- RHEL 8
