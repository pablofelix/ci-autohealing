# source_image.exists failures from PipelineRunTimeout builds

**Date**: 2026-07-03
**Jira**: RHOAIENG-73431
**Status**: Resolved (epoch 1783073621 succeeded)

## Summary

Release epoch 1783018561 failed verify-conforma with 5 `source_image.exists` violations on `odh-workbench-jupyter-minimal-cpu-py312-rhel9`. All violations pointed to per-arch images from build `qjhfc` (status: PipelineRunTimeout).

## Root Cause

Build `qjhfc` timed out but partially pushed container images to Quay. The source-build task never completed, so no source images were generated. operator-processor picked up this SHA as tag-latest from Quay and nudged it into operator-nudging.yaml. The release snapshot then contained a SHA with no source image, causing guaranteed `source_image.exists` failures.

## Slack Thread Investigation (2026-07-01)

A Slack discussion raised that 107/110 entries in `bundle-patch.yaml` had stale digests vs `operator-nudging.yaml`. This was investigated and found to be a **red herring**:

- **bundle-patch.yaml staleness**: Normal — these digests are frozen from branch creation. The operator-processor.py (GitHub Actions workflow) updates operator-nudging.yaml from Quay, NOT bundle-patch.yaml. The staleness is cosmetic.
- **operator-nudging.yaml**: This is what matters. The nightly build reads from here into the CSV and FBC fragment.
- **Konflux snapshot**: verify-conforma checks the snapshot SHAs, which come from the nudging chain. bundle-patch.yaml is not in this path.

The Slack thread conflated bundle-patch.yaml staleness with the actual failure mechanism. The real issue was simpler: PipelineRunTimeout → no source image → guaranteed verify-conforma failure.

## Resolution

Build `r9sfr` succeeded with SHA `sha256:cb4f5b20...`, replacing the stale SHA in the snapshot. Epoch 1783073621 passed all verify-conforma checks.

## Lessons for AI Analyzer

1. PipelineRunTimeout + image pushed to Quay = guaranteed source_image.exists failure
2. bundle-patch.yaml staleness is normal and should NOT be cited as a root cause
3. Focus on: which SHA is in the Konflux snapshot → which build produced it → did that build complete successfully
4. The fix is always: trigger a fresh build so tag-latest points to a completed build with source images
