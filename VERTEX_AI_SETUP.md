# Vertex AI Setup Complete ✓

## Status
- ✓ LLM provider code implemented (provider-independent architecture)
- ✓ Python 3.11 installed with anthropic SDK (`anthropic[vertex]>=0.40.0`)
- ✓ Config auto-detects Vertex AI from environment variables
- ✓ GCP authentication configured
- ✓ 11 pending failures ready for analysis

## Environment Variables (from ~/.bashrc)
```bash
export CLAUDE_CODE_USE_VERTEX=1
export CLOUD_ML_REGION=us-east5
export ANTHROPIC_VERTEX_PROJECT_ID=itpc-gcp-ai-eng-claude
```

## Quick Test
```bash
# List pending failures (no LLM call)
cd PROJECT_DIR/collectors/python
python3.11 -c "
from config import CollectorConfig
from analyzers.build_failure_analyzer import BuildFailureAnalyzer
config = CollectorConfig.from_env()
analyzer = BuildFailureAnalyzer(config)
pending = analyzer.get_pending_failures(limit=5)
print('Pending:', len(pending), 'failures')
for f in pending:
    print(' -', f['component_name'])
"
```

## Run AI Analysis
```bash
# Analyze up to 5 failures (default)
cd PROJECT_DIR/collectors/python
./analyze_failures.py

# Or specify a limit
python3.11 analyze_failures.py  # uses AI_MAX_PER_RUN=5 from env
```

## What It Does
1. Fetches unanalyzed failures from database
2. Constructs analysis prompt (system prompt + failure details + logs)
3. Calls Claude Sonnet 4.5 on Vertex AI with tool_use for structured output
4. Parses response: root_cause, failure_category, confidence, recommended_fix, can_auto_fix
5. Stores results in `ai_analysis` table
6. Updates `build_failures.ai_analyzed = TRUE`
7. Tracks in Langfuse (if configured)

## Output Example
```
======================================================================
Build Failure AI Analysis
Application: acme-v2-0
======================================================================
Found 11 pending failures
[1/5] acme-automl-v3-4
PipelineRun: acme-automl-v3-4-...
Category: build_error (confidence: 0.92)
Can auto-fix: False
Root cause: Docker COPY failed - missing requirements.txt in context

[2/5] odh-maas-api-v3-4
...
```

## Database Tables
- `ai_analysis` - Analysis results with root cause, category, confidence, fix recommendations
- `build_failures.ai_analyzed` - Marked TRUE after analysis
- `build_failures.ai_analysis_id` - Foreign key to ai_analysis

## View Results
```bash
# Via ic CLI (after Phase 1.4 implementation)
ic describe component acme-automl-v3-4

# Via SQL
ic db query "SELECT component_name, failure_category, confidence_score, can_auto_fix FROM ai_analysis a JOIN build_failures b ON a.build_failure_id = b.id WHERE a.analyzed_at > NOW() - INTERVAL '1 day' ORDER BY analyzed_at DESC"
```

## Cron Integration
The cron script automatically runs AI analysis if `LLM_PROVIDER` is set:
```bash
./cron/collect-comprehensive.sh
# Step 6 runs: python3.11 analyze_failures.py
```

## Next Steps (Phase 1 completion)
- [ ] Phase 1.2: Conforma analyzer (analyze Enterprise Contract violations)
- [ ] Phase 1.4: CLI display (`ic describe component` shows AI analysis)
- [ ] Phase 1.6: ADR document (`docs/adr/005-ai-analysis-architecture.md`)

## Troubleshooting
- If "No module named 'anthropic'": `python3.11 -m pip install --user 'anthropic[vertex]'`
- If GCP auth fails: `gcloud auth application-default login`
- If no pending failures: Run collectors first (`./cron/collect-comprehensive.sh`)
