# AI Analysis Review Prompt

You are reviewing an AI-generated CI/CD failure analysis against the actual resolution (ground truth). Your job is to find gaps in the AI's diagnosis and recommend improvements.

## Context

- **Analysis type**: {analysis_type} (build failure / conforma violation / release failure)
- **Component**: {component}
- **AI model used**: {model_used}
- **Analyzed at**: {analyzed_at}
- **AI confidence**: {confidence_score}%

## AI Analysis Output

### Root Cause (AI)
{root_cause}

### Failure Category (AI)
{failure_category}

### Recommended Fix (AI)
{recommended_fix}

### Analysis Details (AI)
{analysis_json_summary}

## Ground Truth (Actual Resolution)

{ground_truth}

## Your Task

Compare the AI analysis with the ground truth and produce a structured review. Be specific and actionable.

### 1. Verdict
Rate the AI analysis: **correct**, **partial**, or **incorrect**.
- **correct**: Root cause and fix are essentially right, even if wording differs.
- **partial**: Got the general area right but missed important details, or fix was incomplete.
- **incorrect**: Fundamentally wrong root cause or fix would not resolve the issue.

### 2. Gap Analysis
For each gap found:
- What the AI said vs. what actually happened
- Why the AI missed it (missing data? wrong inference? prompt gap?)
- Severity: critical (wrong diagnosis), moderate (missed detail), minor (presentation)

### 3. Prompt Improvements
Concrete suggestions to improve the analyzer prompt so it catches this pattern next time:
- New rules or heuristics to add
- Data the prompt should request
- Patterns it should recognize
- Edge cases to handle

### 4. Data Improvements
Information that was NOT available to the AI but would have helped:
- Missing context from the CI system
- Logs or metadata not collected
- External data sources needed

## Output Format

Respond with a structured JSON tool call using the review_result tool.
