# Skill Issues: conforma-analyze

## Date: 2026-07-01

## Error

The `conforma-analyze` skill fails at Step 7 (violations_coverage.py) when the
installed `ec` CLI is too old. The script uses `--skip-att-sig-check` which was
added in ec v0.9.50, but the prerequisites check only verifies that `ec` exists,
not that it meets a minimum version.

With ec v0.9.14:
```
error: unknown flag: --skip-att-sig-check
```

The agent then wasted ~20 of its 30 turns attempting workarounds (downloading a
newer binary, shadowing PATH, writing helper scripts) instead of stopping and
reporting the version mismatch.

## Root Cause

`verify_conforma_prerequisites.py` checks:
- `ec` binary exists in PATH ✅
- `ec` can run `--help` ✅
- `ec` version meets minimum required ❌ (not checked)

The `ensure_ec_binary()` in `conforma_ec_validate.py` also prefers the PATH
binary over the `.work/bin/ec` download, so even when a newer version is
downloaded it gets ignored.

## Fix Needed (in aiops-infra skill repo)

1. **Prerequisites**: Add minimum version check for `ec` (>= v0.9.50) in
   `verify_conforma_prerequisites.py`. Fail with a clear message:
   ```
   ❌ ec — v0.9.14 installed, v0.9.50+ required
   Fix: curl -sL https://github.com/enterprise-contract/ec-cli/releases/latest/download/ec_linux_amd64 -o ~/.local/bin/ec && chmod +x ~/.local/bin/ec
   ```

2. **Binary preference**: `ensure_ec_binary()` should prefer `.work/bin/ec` over
   PATH if the PATH version is too old, or at least verify the PATH version
   supports the required flags before returning it.

3. **Agent guardrail**: Add to SKILL.md workflow rules:
   "If a tool fails with an unknown flag/option error, STOP and report the
   version mismatch — do not attempt workarounds."

## Workaround

Update ec manually:
```bash
curl -sL https://github.com/enterprise-contract/ec-cli/releases/latest/download/ec_linux_amd64 -o ~/.local/bin/ec && chmod +x ~/.local/bin/ec
```

## Environment

- ec installed: v0.9.14 (too old)
- ec required: v0.9.50+
- ec latest: v0.9.50 (2026-06-26)
- RHEL 8

---

# Skill Issue: conforma-analyze — wrong working directory

## Date: 2026-07-01

## Error

The agent starts every run with `cd /workspace` which doesn't exist, then
wastes 2-3 turns searching for where scripts are located:

```
[Tool] bash_execute(cd /workspace && python3 scripts/verify_conforma_prerequisites.py ...)
  stderr: bash: line 0: cd: /workspace: No such file or directory
[Tool] bash_execute(pwd && ls -la 2>&1)
[Tool] bash_execute(find / -name "verify_conforma_prerequisites.py" 2>/dev/null)
```

## Root Cause

`agent_executor.py` set `working_dir=skill.path` which pointed to the skill
subdirectory (`skills/conforma-analyze/`) instead of the repo root
(`conforma-99f9de972661/`). The scripts live at `scripts/` in the repo root,
not inside the skill subdirectory.

The SKILL.md references paths like `python3 scripts/...` which are relative to
the repo root.

## Fix Applied (in ic)

Added `working-dir` field to `SkillMetadata` (SKILL.md frontmatter). The skill
author declares where to run: `skill` (default), `repo-root`, or a relative
path. Resolution logic in `models.resolve_working_dir()`.

Both `AgentExecutor` and `SkillExecutor` use it.

## Fix Needed (in aiops-infra skill repo)

Add `working-dir: repo-root` to the SKILL.md frontmatter of skills that
reference scripts at the repo root:

```yaml
---
name: conforma-analyze
description: ...
working-dir: repo-root
---
```

After updating the SKILL.md, run `ic skills update` to re-sync.

---

# Skill Issue: conforma-analyze — sandbox timeout too short

## Date: 2026-07-01

## Error

Step 7 (`violations_coverage.py`) runs `ec validate image` against 3 policy
files. This takes 2-5 minutes. The sandbox default timeout was 60 seconds,
causing the command to be killed:

```
[Tool] bash_execute(python3 skills/conforma-analyze/scripts/conforma_ec_validate.py ...)
  stderr: Command timed out after 60 seconds
  exit_code: -1
```

## Fix Applied (in ic)

- `agent_tools.py`: `bash_execute` and `python_execute` now accept a `timeout`
  parameter (default 300s, max 1800s)
- Default raised from 60s to 300s — enough for most long-running commands
- The agent can request longer timeouts when needed
