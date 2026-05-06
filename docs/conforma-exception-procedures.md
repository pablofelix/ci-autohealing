# Conforma Exception Procedures

How to create a new Conforma policy exception and what to do when one expires.

Source: `comforma.pdf` (RHOAI Conforma Documentation), sections 5 and 6.

## Policy files

There are two GitLab policy files where exceptions are managed. Which one to use depends on what is being excepted:

- **FBC (acme-fbc-fragment)**: [fbc-acme-prod.yaml](https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_SHORT/product/EnterpriseContractPolicy/fbc-acme-prod.yaml)
- **Registry (all other components)**: [registry-acme-prod.yaml](https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_SHORT/product/EnterpriseContractPolicy/registry-acme-prod.yaml)

There are also STAGE equivalents (less strict, ignore `test.no_skipped_tests` among other things):

- [fbc-acme-stage.yaml](https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_SHORT/product/EnterpriseContractPolicy/fbc-acme-stage.yaml)
- [registry-acme-stage.yaml](https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/config/CLUSTER_SHORT/product/EnterpriseContractPolicy/registry-acme-stage.yaml)

---

## How to create a new exception

### 1. Find the exact exception format

- Go to the Slack notification with the Conforma report, or find the PipelineRun in Konflux UI
- Open the **Security** tab
- Each "Failed" violation shows the exact exclusion string to use, e.g.:
  `rpm_signature.allowed:05b555b38483c65d` or
  `sbom_spdx.allowed_package_sources:pkg:generic/filename?checksum=sha256:...&download_url=...`

### 2. Create the PSRD exception JIRA

- Follow the [PSRD Exception Submission Quick Guide](https://JIRA_HOST/wiki/spaces/PRODSEC/pages/289226815/PSRD+Exception+Submission+Quick+Guide)
- The JIRA is created under the **PSX** project using [this link](https://JIRA_CREATE_ISSUE_URL)
- Fill in:
  - Why the exception is required
  - Examples of the violations
  - Links to agreements if third-party packages are involved (see the [3rd party spreadsheet](https://docs.google.com/spreadsheets/d/1o2j87H-k33eBsDcxR4oeqpNJJZe_TqHarnEBw-PqepM/edit?gid=1354667519#gid=1354667519))
- Assign **Jay Koehler** as Authorized Party
- Move the JIRA from "Pending Approval" to "Review"
- Notify Jay Koehler and ProdSec engineers

### 3. Add the exception to the policy file

Add the exclusion string under `volatileConfig.exclude` in the appropriate policy file via a **Merge Request**.

Exception entry format:

```yaml
- value: <exclusion-string-from-security-tab>
  effectiveUntil: "YYYY-MM-DDT00:00:00Z"
  imageUrl: quay.io/acme/<component-image>   # optional, scopes to one image
  reference: https://JIRA_HOST/browse/PSX-XXXX
```

### 4. Get ProdSec approval

- Ask RH ProdSec on Slack (e.g. `#wg-3_0-openshift-ai-release`) to review the JIRA and Merge Request
- The ProdSec engineer who reviews RHOAI policies is **@owatkins**
- Once ProdSec approves, the MR can be merged
- The PSX JIRA stays in "Waiting" status for the duration of the exception

---

## How to handle an expired exception

Exceptions have an `effectiveUntil` date. When that date passes, the exception silently stops working and the violation starts firing again in Conforma reports.

### 1. Check if the exception is still needed

- Look at the `reference` field in the exception entry -- it points to a JIRA
- If that JIRA is **closed**, the underlying issue was fixed and the exception can be safely removed from the GitLab file

### 2. Verify in Conforma reports

- Check `#rhoai-konflux-poc-notifications` Slack channel or Konflux UI
- Look at recent reports and confirm the violation no longer appears for that component
- If the violation is gone, the fix landed -- clean up the expired entry from the policy file

### 3. If the violation still appears

- Contact the **Component Team** or **Konflux team** (depending on who the JIRA is assigned to)
- Check the JIRA for discussion and follow up on the plan to fix

### 4. If fixing is not immediately possible

This is the case when a push to Prod/GA is happening soon and the fix won't land in time.

- **Extend the exception**: raise a new Merge Request updating the `effectiveUntil` date
- Set the new date to something reasonable based on when the Component/Konflux team estimates they can fix it
- The MR needs **ProdSec approval** (@owatkins)
- Ask for review on the appropriate Slack channel for the affected RHOAI release

### 5. Check all active RHOAI releases

- The violation may affect multiple active releases (e.g. 3.3 and 3.4)
- If it shows up on any of them, either extend the exception or confirm the fix is landing soon

---

## Testing upcoming expirations (conforma-custom)

If exceptions are expiring soon and you want to preview what will happen:

- Create a **custom Conforma policy** with those exceptions already removed
- Run it against a recent Snapshot in Konflux to see which violations would fire
- This lets you proactively identify problems before the expiration date hits

See comforma.pdf section 6 (FAQ item 6) for details on running conforma-custom.

---

## Weekend releases

When releasing a Konflux application on a weekend, you may need a `schedule.weekday_restriction` exception:

```yaml
- value: schedule.weekday_restriction
  effectiveUntil: "YYYY-MM-DDT00:00:00Z"
  reference: https://JIRA_HOST/browse/RHOAIENG-XXXXX
```

This goes in both FBC and registry policy files.

---

## Key contacts

- **ProdSec reviewer for RHOAI policies**: @owatkins
- **Exception request handler**: mmilev
- **Authorized Party (Senior Manager)**: Jay Koehler
- **Approval also from**: Lindani
