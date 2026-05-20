# Conforma Future Scenarios

## Overview

Starting with migration 011, `conforma_results` tracks whether a violation comes from a **current policy** (gate-blocking) or a **future policy** (informational preview).

## Background

The RHOAI project runs 3 Conforma scenarios for each release:

| Scenario Type | Example | Policy | Purpose | Blocks Release? |
|---------------|---------|--------|---------|-----------------|
| **Normal** | `conforma-registry-acme-prod-v3-4` | `releng-tenant/registry-acme-prod` | Current EC rules | ✅ YES |
| **Single-component** | `conforma-registry-acme-prod-v3-4-single-component` | Same as normal | Faster testing (1 component only) | ✅ YES |
| **Future** | `conforma-registry-acme-prod-v3-4-future` | `NAMESPACE_PLACEHOLDER/registry-acme-prod-future` | Preview of stricter rules | ❌ NO (informational) |

**Future scenarios** show what would fail if stricter EC policies were enforced today, giving teams advance warning to fix issues before they become blocking.

## Database Schema

```sql
-- conforma_results table now has:
is_future BOOLEAN DEFAULT FALSE NOT NULL
```

- `is_future = TRUE` → Violation from future policy (informational)
- `is_future = FALSE` → Violation from current policy (blocks release)

## Detection Logic

The collector automatically detects future scenarios by the `-future` suffix in the scenario name:

```python
# In ConformaViolationCollector.save_to_db()
is_future = '-future' in scenario
```

Examples:
- `conforma-registry-acme-prod-v3-4` → `is_future = FALSE`
- `conforma-registry-acme-prod-v3-4-future` → `is_future = TRUE`

## Querying

### Show only gate-blocking violations

```sql
SELECT component_name, scenario, violations_count
FROM conforma_results
WHERE application = 'rhoai' 
  AND is_resolved = FALSE
  AND is_future = FALSE  -- Only current-policy violations
ORDER BY violations_count DESC;
```

### Show only future/informational violations

```sql
SELECT component_name, scenario, violations_count
FROM conforma_results
WHERE application = 'rhoai'
  AND is_resolved = FALSE
  AND is_future = TRUE  -- Only future-policy violations
ORDER BY violations_count DESC;
```

### Compare current vs future violations

```sql
SELECT 
    component_name,
    SUM(CASE WHEN is_future = FALSE THEN violations_count ELSE 0 END) as current_violations,
    SUM(CASE WHEN is_future = TRUE THEN violations_count ELSE 0 END) as future_violations
FROM conforma_results
WHERE application = 'rhoai' AND is_resolved = FALSE
GROUP BY component_name
HAVING SUM(violations_count) > 0
ORDER BY current_violations DESC, future_violations DESC;
```

## Python API

### Repository: Filter blocking violations only

```python
from repositories import ConformaRepository

# Get only blocking violations (current policy)
blocking_components = repo.find_unresolved_component_names(
    application='rhoai',
    include_future=False  # Exclude future scenarios
)

# Get all violations (default)
all_components = repo.find_unresolved_component_names(
    application='rhoai',
    include_future=True
)
```

### Collector: Automatic detection

```python
# ConformaViolationCollector automatically detects and saves is_future
collector.save_to_db(
    component='odh-dashboard',
    scenario='conforma-registry-acme-prod-v3-4-future',  # Will set is_future=True
    pr_name='pr-123',
    pr_uid='uid-456',
    violations=violation_data,
    comp_info=comp_info
)
```

## ic Script Commands

### View violations by policy type

```bash
# Show only blocking violations (current policy) - DEFAULT
ic conforma

# Show only future/informational violations
ic conforma --future

# Show both current and future
ic conforma --all

# Refresh from cluster and show
ic conforma --refresh
```

### Example output

```bash
$ ic conforma
════════════════════════════════════════════════════════════════════════════
 Conforma Test Failures (Blocking - Current Policy)
════════════════════════════════════════════════════════════════════════════
Application: rhoai

Summary: 2 components failing Conforma tests

  #  Component                Policy   Violations  Warnings  OK    Since    JIRA
 --- ------------------------ -------- ----------- --------- ----- -------- ----------
  1  odh-dashboard-v3-4       current  5           12        234   15 May   PROJECT-1234
  2  odh-notebook-v3-4        current  2           8         256   16 May   -

Tip: Use 'ic conforma --future' to see future policy violations
Tip: Use 'ic conforma --all' to see both current and future
```

```bash
$ ic conforma --future
════════════════════════════════════════════════════════════════════════════
 Conforma Test Failures (Informational - Future Policy)
════════════════════════════════════════════════════════════════════════════
Application: rhoai

Summary: 3 components failing Conforma tests

  #  Component                Policy   Violations  Warnings  OK    Since    JIRA
 --- ------------------------ -------- ----------- --------- ----- -------- ----------
  1  acme-fbc-fragment-v3-4  future   5           5         220   14 May   -
  2  odh-model-controller     future   3           10        245   15 May   -
  3  odh-trustyai-v3-4        future   1           7         268   16 May   -
```

## Jira Ticket Creation

When creating Jira tickets for Conforma violations, the `ic` script automatically tags them based on the policy type:

### Current Policy (Blocking)

```bash
$ ic jira create conforma odh-dashboard-v3-4 --execute
Creating Jira ticket...
Created: PROJECT-1234
URL: https://JIRA_HOST/browse/PROJECT-1234

Ticket details:
  Summary:   [Conforma/BLOCKER] odh-dashboard-v3-4 - conforma-registry-acme-prod-v3-4 (5 violations)
  Priority:  Blocker
  Labels:    conforma, conforma-blocker
```

### Future Policy (Informational)

```bash
$ ic jira create conforma acme-fbc-fragment-v3-4 --execute
Creating Jira ticket...
Created: PROJECT-1235
URL: https://JIRA_HOST/browse/PROJECT-1235

Ticket details:
  Summary:   [Conforma/FUTURE] acme-fbc-fragment-v3-4 - conforma-fbc-acme-prod-v3-4-future (5 violations)
  Priority:  Low
  Labels:    conforma, conforma-future

Note: This is a FUTURE policy violation (informational, not blocking current releases)
```

### Dry Run

Preview what would be created without actually creating the ticket:

```bash
$ ic jira create conforma odh-dashboard-v3-4
[DRY RUN] Would create Jira ticket:

  Project:   RHOAIENG
  Type:      Bug
  Priority:  Blocker
  Labels:    conforma,conforma-blocker
  Component: DevOps
  Summary:   [Conforma/BLOCKER] odh-dashboard-v3-4 - conforma-registry-acme-prod-v3-4 (5 violations)

→ Add --execute to actually create: ic jira create conforma odh-dashboard-v3-4 --execute
```

## Migration

Run the migration:

```bash
psql -h localhost -U ci_autohealing -d ci_autohealing -f db/migrations/011_add_is_future_to_conforma.sql
```

All existing records will have `is_future = FALSE` (current policy violations).

## Example Scenario Comparison

For `acme-fbc-fragment-v3-4`:

**Current policy** (blocks release):
```yaml
scenario: conforma-fbc-acme-prod-v3-4
violations: 0  ✅
warnings: 15
```

**Future policy** (informational):
```yaml
scenario: conforma-fbc-acme-prod-v3-4-future
violations: 5  ⚠️ Would block if this policy was current
warnings: 5
```

Violation in future scenario:
```
❌ One of "fbc-fips-check", "fbc-fips-check-oci-ta" tasks is missing
   Code: tasks.required_tasks_found
   
This doesn't block today's release, but will block future releases
once the stricter policy is activated.
```

## Benefits

1. **Prioritization**: Separate urgent (current) from future TODOs
2. **Metrics**: Track actual gate-blockers vs informational warnings
3. **Jira**: Different labels/priorities for current vs future violations
4. **Proactive**: Fix future violations before they become blocking
