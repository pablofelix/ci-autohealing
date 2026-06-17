# AI Quality Metrics — Design Document

## Problem

The AI analyzer produces diagnoses but we don't measure if they're correct. Without quality metrics, we can't:
- Know if the AI is getting better or worse over time
- Justify the LLM cost
- Decide when to invest in a custom model
- Trust autonomous actions (auto-fix PRs)

## Metrics

### Classification Metrics

| Metric | Formula | Source |
|--------|---------|--------|
| `classification_accuracy` | correct / total | Human feedback on triage resolve |
| `category_accuracy` | correct category / total | Same |
| `confidence_calibration` | actual accuracy at each confidence level | Binned analysis |

### Auto-Fix Metrics

| Metric | Formula | Source |
|--------|---------|--------|
| `auto_fix_precision` | successful auto-fix PRs / total auto-fix PRs | Build status after merge |
| `auto_fix_recall` | auto-fixed / (auto-fixed + could-have-been-fixed) | Manual review |
| `unsafe_auto_fix_rate` | PRs that made things worse / total auto-fix PRs | Build status + revert detection |
| `fix_acceptance_rate` | merged PRs / created PRs | GitHub PR status |

### Cost Metrics

| Metric | Formula | Source |
|--------|---------|--------|
| `avg_cost_per_analysis` | total cost / total analyses | ai_analysis.cost_usd |
| `avg_tokens_per_analysis` | total tokens / total analyses | ai_analysis.tokens_used |
| `cost_per_correct_diagnosis` | total cost / correct diagnoses | cost / accuracy |
| `time_to_diagnosis` | analysis_duration avg | ai_analysis.analysis_duration |

### Trend Metrics

| Metric | Window | Purpose |
|--------|--------|---------|
| `accuracy_7d` | 7 days | Short-term quality tracking |
| `accuracy_30d` | 30 days | Stable quality baseline |
| `cost_trend` | Weekly | Cost management |
| `pattern_coverage` | All time | What % of failures match known patterns |

## Feedback Loop

### How accuracy is measured

When a human resolves a triage item, the system asks:

```
Was the AI diagnosis correct?
  [y] Yes, matched the actual root cause
  [p] Partially — right direction but incomplete
  [n] No, diagnosis was wrong
  [s] Skip — can't determine
```

This gets stored in `ai_analysis` as `human_verdict` (correct/partial/incorrect/unknown).

### Schema changes

```sql
ALTER TABLE ai_analysis
    ADD COLUMN human_verdict VARCHAR(20),        -- correct, partial, incorrect, unknown
    ADD COLUMN human_verdict_at TIMESTAMP,
    ADD COLUMN human_notes TEXT,
    ADD COLUMN actual_root_cause TEXT,
    ADD COLUMN actual_fix_description TEXT;
```

### Integration points

- `ic triage resolve` — prompts for AI verdict
- MCP `resolve_triage_item()` — accepts verdict field
- API `POST /triage/{id}/resolve` — accepts verdict in body
- Worker: auto-measure `auto_fix_precision` by checking build status after PR merge

## Custom Model — Readiness Criteria

A custom fine-tuned model makes sense when:

1. **>500 analyzed failures with human verdicts** — enough labeled data
2. **>60% pattern match rate** — most failures are variants of known patterns
3. **>$500/month LLM cost** — financial incentive
4. **<85% accuracy** — generic model hitting ceiling for this domain

Data pipeline for future fine-tuning:
```
ai_analysis (prompt + response + verdict) → training pairs
error_patterns (category + typical_fix) → few-shot examples
resolution_attempts (what actually worked) → ground truth
```

The current architecture supports this: `build_analysis_prompt()` is a pure function that generates prompts. Swapping the LLM backend (Claude → fine-tuned model) only requires changing `LLMProvider`.

## Not Now, But Prepare

- [ ] Add `human_verdict` column to ai_analysis
- [ ] Prompt for verdict on triage resolve
- [ ] Dashboard endpoint: `/api/v1/metrics/ai-quality`
- [ ] Weekly accuracy report
- [ ] Export training data: `ic ai export-training-data`
