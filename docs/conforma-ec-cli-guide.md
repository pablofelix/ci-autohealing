# Conforma EC CLI Guide

Local validation of component images against EnterpriseContractPolicy using the `ec` CLI.
Useful for verifying exceptions work without waiting for the conforma pipeline to run.

## Prerequisites

- `ec` CLI installed (`ec version` to verify)
- `oc` logged into the Konflux cluster (`oc whoami`)
- Pull secret for the private Quay registry (see Setup below)

## Setup

### Install ec CLI

```bash
# Download latest release
curl -sL https://github.com/enterprise-contract/ec-cli/releases/latest/download/ec_linux_amd64 -o /usr/local/bin/ec
chmod +x /usr/local/bin/ec

# Verify
ec version
```

### Extract pull secret

The `automation-pull` secret in the tenant namespace contains credentials for private Quay images.
Extract it to a local file:

```bash
oc get secret automation-pull -n NAMESPACE_PLACEHOLDER \
  -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d > ~/.docker/rhoai-pull-secret.json
```

Keep this file private — do not commit it to git.

## Usage

### Validate a component image

**Step 1 — Get the component's latest image SHA from the latest snapshot:**

```bash
oc get snapshot -n NAMESPACE_PLACEHOLDER \
  --sort-by=.metadata.creationTimestamp \
  -l appstudio.openshift.io/component=<COMPONENT> \
  -o json | python3 -c "
import json, sys
items = json.load(sys.stdin).get('items', [])
for comp in items[-1]['spec']['components']:
    if comp['name'] == '<COMPONENT>':
        print(comp['containerImage'])
        break
"
```

Replace `<COMPONENT>` with the component name (e.g., `acme-autorag-v3-4`).

**Step 2 — Run ec validate:**

```bash
ec validate image \
  --image "<IMAGE>@sha256:<DIGEST>" \
  --policy NAMESPACE_PLACEHOLDER/registry-acme-prod \
  --ignore-rekor \
  --strict false \
  --docker-config ~/.docker/rhoai-pull-secret.json \
  --output text
```

### Output formats

- `--output text` — quick pass/fail summary, good for a first check
- `--output json` — structured output, useful for parsing violations
- `--output yaml` — same as JSON but in YAML format

### Parse JSON output for violation details

```bash
ec validate image \
  --image "<IMAGE>@sha256:<DIGEST>" \
  --policy NAMESPACE_PLACEHOLDER/registry-acme-prod \
  --ignore-rekor \
  --strict false \
  --docker-config ~/.docker/rhoai-pull-secret.json \
  --output json 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
for c in data.get('components', []):
    v = c.get('violations', [])
    w = c.get('warnings', [])
    s = c.get('successes', [])
    print(f'Success: {c[\"success\"]}')
    print(f'Violations: {len(v)}, Warnings: {len(w)}, Successes: {len(s)}')
    for x in v:
        code = x.get('metadata', {}).get('code', '')
        print(f'  FAIL  {code}: {x[\"msg\"][:100]}')
    for x in w:
        code = x.get('metadata', {}).get('code', '')
        print(f'  WARN  {code}: {x[\"msg\"][:100]}')
"
```

## Available Policies

List all policies in the namespace:

```bash
oc get enterprisecontractpolicy -n NAMESPACE_PLACEHOLDER -o custom-columns='NAME:.metadata.name' --no-headers
```

Key policies for RHOAI:

| Policy | Description | Use case |
|--------|-------------|----------|
| `registry-acme-prod` | Current production policy | Verify what ships to registry.redhat.io |
| `registry-acme-prod-future` | Future policy with upcoming exceptions | Verify new exceptions before they go live |
| `registry-acme-stage` | Stage policy | Check stage compliance |
| `fbc-acme-prod` | FBC fragment production policy | Validate FBC components |
| `fbc-acme-stage` | FBC fragment stage policy | Check FBC stage compliance |

## Common Tasks

### Verify an exception is working

After merging an exception MR in GitLab, validate that the component now passes:

```bash
# Should fail (old policy without exception)
ec validate image \
  --image "<IMAGE>" \
  --policy NAMESPACE_PLACEHOLDER/registry-acme-prod \
  --ignore-rekor --strict false \
  --docker-config ~/.docker/rhoai-pull-secret.json \
  --output text

# Should pass (future policy with exception)
ec validate image \
  --image "<IMAGE>" \
  --policy NAMESPACE_PLACEHOLDER/registry-acme-prod-future \
  --ignore-rekor --strict false \
  --docker-config ~/.docker/rhoai-pull-secret.json \
  --output text
```

### Compare prod vs stage

```bash
for policy in registry-acme-prod registry-acme-stage; do
    echo "=== $policy ==="
    ec validate image \
      --image "<IMAGE>" \
      --policy "NAMESPACE_PLACEHOLDER/$policy" \
      --ignore-rekor --strict false \
      --docker-config ~/.docker/rhoai-pull-secret.json \
      --output json 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
for c in data.get('components', []):
    v = len(c.get('violations', []))
    w = len(c.get('warnings', []))
    s = len(c.get('successes', []))
    print(f'  Success: {c[\"success\"]}  Violations: {v}  Warnings: {w}  Successes: {s}')
"
done
```

### Inspect policy rules and exceptions

```bash
ec inspect policy \
  --policy NAMESPACE_PLACEHOLDER/registry-acme-prod \
  --output json 2>/dev/null | python3 -c "
import json, sys
# The output is keyed by policy source OCI reference
data = json.load(sys.stdin)
for source_ref, rules in data.items():
    print(f'Source: {source_ref[:80]}...')
    print(f'Rules: {len(rules)}')
" 2>/dev/null
```

To see the exception (exclude) list for a policy:

```bash
oc get enterprisecontractpolicy registry-acme-prod -n NAMESPACE_PLACEHOLDER \
  -o jsonpath='{.spec.sources[0].volatileConfig.exclude}' | python3 -m json.tool
```

## Key Flags Reference

| Flag | Description |
|------|-------------|
| `--image` | Container image with digest (`repo@sha256:...`) |
| `--policy` | EC policy as `namespace/name` (reads CR from cluster) or inline YAML/JSON |
| `--docker-config` | Path to docker config JSON with registry credentials |
| `--ignore-rekor` | Skip Rekor transparency log verification |
| `--strict false` | Don't fail on warnings, only on violations |
| `--output` | Output format: `text`, `json`, `yaml` |
| `--show-successes` | Include passing rules in the output (verbose) |
| `--info` | Show rule descriptions and metadata |

## Troubleshooting

**401 Unauthorized on image pull:**
The pull secret is missing or doesn't have access to the Quay org.
Re-extract from the cluster: `oc get secret automation-pull ...`

**Policy not found:**
Verify the policy exists: `oc get ecp <name> -n NAMESPACE_PLACEHOLDER`

**Timeout on validate:**
`ec` downloads policy bundles from OCI registries on first run.
Subsequent runs are faster (cached). Use `--timeout` to increase.

**Stale results:**
`ec` caches policy bundles. Clear cache: `rm -rf ~/.ec/cache`
