# Cron Job Setup

Automated batch processing for CI Auto-Healing system.

## Jobs

### 1. Batch Analysis (Hourly)
Analyzes build failures and conforma violations in batches.

**Script:** `batch_analysis.sh`  
**Schedule:** Every hour at minute 0  
**What it does:**
- Analyzes up to 20 failures per run (configurable)
- Splits: 75% build failures, 25% conforma violations
- Tracks queue depth and ETA
- Logs to `/tmp/ci-autohealing/batch_analysis_YYYYMMDD_HHMMSS.log`

### 2. Context Enrichment (Every 30 minutes)
Enriches failures with dependency changes and related failures.

**Script:** `enrich_context.sh`  
**Schedule:** Every 30 minutes  
**What it does:**
- Enriches up to 50 failures per run
- Extracts dependency file changes
- Finds related failures from last 7 days
- Logs to `/tmp/ci-autohealing/enrichment_YYYYMMDD_HHMMSS.log`

## Installation

### Manual Crontab Setup

```bash
# Edit crontab
crontab -e

# Add these lines (adjust paths):
0 * * * * PROJECT_DIR/src/cron/batch_analysis.sh
*/30 * * * * PROJECT_DIR/src/cron/enrich_context.sh
```

### Environment Variables

Cron jobs load from `.env` file in project root. Required variables:

```bash
# Database
DB_HOST=localhost
DB_PORT=5433
DB_USER=postgres
DB_PASSWORD=admin
DB_NAME=konflux_monitoring

# Kubernetes
NAMESPACE=NAMESPACE_PLACEHOLDER
APPLICATION_NAME=acme-v2-0

# LLM (Vertex AI or Anthropic)
ANTHROPIC_VERTEX_PROJECT_ID=your-project-id
CLOUD_ML_REGION=us-east5
# OR
ANTHROPIC_API_KEY=sk-ant-...

# Batch Analysis Config (optional)
BATCH_ANALYSIS_ENABLED=true
BATCH_ANALYSIS_MAX_PER_RUN=20
BATCH_ANALYSIS_AUTO_JIRA=false
```

## Log Management

- Logs stored in `/tmp/ci-autohealing/` (configurable via `LOG_DIR` env var)
- Automatic cleanup: logs older than 7 days are deleted
- Each run creates timestamped log file

## Monitoring

### Check Queue Depth

```bash
cd PROJECT_DIR/src
python3 analyze_batch.py --estimate
```

### View Recent Logs

```bash
# Latest batch analysis log
ls -lt /tmp/ci-autohealing/batch_analysis_*.log | head -1 | xargs tail -f

# Latest enrichment log
ls -lt /tmp/ci-autohealing/enrichment_*.log | head -1 | xargs tail -f
```

### Verify Cron Jobs Running

```bash
crontab -l  # List installed jobs
grep CRON /var/log/syslog  # View cron execution (Ubuntu/Debian)
```

## Troubleshooting

### Jobs Not Running

1. Check crontab: `crontab -l`
2. Verify scripts are executable: `ls -l cron/*.sh`
3. Check system cron service: `systemctl status cron`
4. Review syslog: `grep CRON /var/log/syslog`

### LLM Authentication Errors

Vertex AI requires Application Default Credentials:

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

### Database Connection Errors

1. Verify PostgreSQL container running:
   ```bash
   podman ps | grep ci-autohealing-db
   ```

2. Test connection:
   ```bash
   PGPASSWORD=admin psql -h localhost -p 5433 -U postgres -d konflux_monitoring -c "SELECT 1"
   ```

### High Queue Depth

If queue depth exceeds 100:
- Increase `BATCH_ANALYSIS_MAX_PER_RUN` (default: 20)
- Run additional manual batches: `python3 analyze_batch.py --limit 50`
- Check for errors in recent failures blocking analysis

## Performance Tuning

### Increase Batch Size

```bash
# In .env
BATCH_ANALYSIS_MAX_PER_RUN=50  # Analyze more per hour
```

### Adjust Schedule

```bash
# More frequent analysis (every 30 minutes)
*/30 * * * * /path/to/batch_analysis.sh

# Less frequent (every 2 hours)
0 */2 * * * /path/to/batch_analysis.sh
```

## Manual Execution

Run jobs manually for testing:

```bash
# Batch analysis
cd PROJECT_DIR/src
./cron/batch_analysis.sh

# Context enrichment
./cron/enrich_context.sh
```
