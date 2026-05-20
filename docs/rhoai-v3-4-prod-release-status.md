# RHOAI v3.4 Production Release — Status Update

**Date:** 2026-05-13
**Status:** Blocked — waiting on resolution from the release engineering team.

---

## What happened

We generated the production release artifacts for **RHOAI v3.4** using the standard release helper tooling ([generate-prod-release-artifacts.sh](https://github.com/acme-org/rhods-devops-infra/blob/main/tools/rhoai-release-helper/generate-prod-release-artifacts.sh)) and submitted the release request to Konflux via `oc apply -f` (including the CVEs).

The Release CR submitted:

```yaml
apiVersion: appstudio.redhat.com/v1alpha1
kind: Release
metadata:
  name: acme-v2-0-prod-1778584292
  namespace: NAMESPACE_PLACEHOLDER
  labels:
    konflux-release-data/rbc-release-commit: a4fd3e32e54a082a920038ffa39e0c3e2bf5b29e
    konflux-release-data/artifact-type: components
    konflux-release-data/environment: prod
    appstudio.openshift.io/application: acme-v2-0
spec:
  gracePeriodDays: 30
  releasePlan: rhoai-onprem-v3-4-components-prod
  snapshot: acme-v2-0-1778062796
  data:
    version: 3.4.0
```

The snapshot used in this release (`acme-v2-0-1778062796`) is **the same one that was already validated and released successfully in the stage environment**, so the content being promoted to production has already been tested.

---

## What went wrong

After submitting the release, the pipeline flagged issues:

1. **Configuration error in the release pipeline:** An error was found in the Konflux release configuration at [line 338 of the ReleasePlanAdmission config](https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_SHORT/product/ReleasePlanAdmission/rhoai/rhoai-onprem-v3-4-components-prod.yaml?ref_type=heads#L338).

2. **Conforma policy violations:** The release also triggered Conforma violations (automated compliance/policy checks that must pass before a release can proceed) related to RHAII.

The issue was discussed in [this Slack thread](https://redhat-internal.slack.com/archives/C04NY86M4EM/p1778635654060019) where the team identified the error.

---

## Current status

**We are blocked and waiting on the team to resolve** the configuration error and the Conforma violations before we can retry the production release.
