Triage CI failures: choose build or conforma focus, with full tracking.

Usage: /triage [component-name]

- No argument: show triage dashboard, then ask what type to triage
- With component: auto-detect type and investigate

---

## Instructions

The working directory is: .

### Always start by showing the triage dashboard:

```bash
./ic triage show 2>/dev/null
```

This shows tracked items, untracked failures, and the overall triage state. Present this to the user first.

### If a specific component was provided as `$ARGUMENTS`:

Auto-detect the failure type by running:
```bash
./ic get alerts 2>/dev/null
```

Check whether the component appears in the build failures or conforma violations section, then follow the corresponding workflow:
- Build failure → follow the /triage-build workflow for that component
- Conforma violation → follow the /triage-conforma workflow for that component
- Both → handle build first, then conforma

### If no component was provided:

Ask the user what type of failures to triage using AskUserQuestion:

Options:
- **Build failures** — follow the /triage-build workflow
- **Conforma violations** — follow the /triage-conforma workflow
- **Both** — run /triage-build first, then /triage-conforma

### Workflow references

- Build triage: follow the exact steps in /triage-build
- Conforma triage: follow the exact steps in /triage-conforma

Both workflows integrate `ic triage` tracking to maintain state across sessions.
