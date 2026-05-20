#!/bin/bash
# Batch analysis cron job - runs hourly
# Add to crontab: 0 * * * * /path/to/batch_analysis.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="${LOG_DIR:-/tmp/ci-autohealing}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Create log directory
mkdir -p "$LOG_DIR"

# Log file with timestamp
LOG_FILE="$LOG_DIR/batch_analysis_${TIMESTAMP}.log"

echo "=== Batch Analysis Cron Job ===" | tee -a "$LOG_FILE"
echo "Started: $(date)" | tee -a "$LOG_FILE"
echo "Project: $PROJECT_DIR" | tee -a "$LOG_FILE"

# Change to project directory
cd "$PROJECT_DIR"

# Load environment if .env exists
if [ -f .env ]; then
    echo "Loading .env" | tee -a "$LOG_FILE"
    set -a
    source .env
    set +a
fi

# Run batch analysis
echo "Running batch analysis..." | tee -a "$LOG_FILE"
if python3 analyze_batch.py >> "$LOG_FILE" 2>&1; then
    echo "Completed: $(date)" | tee -a "$LOG_FILE"
    EXIT_CODE=0
else
    EXIT_CODE=$?
    echo "Failed with exit code: $EXIT_CODE" | tee -a "$LOG_FILE"
fi

# Keep only last 7 days of logs
find "$LOG_DIR" -name "batch_analysis_*.log" -mtime +7 -delete

echo "Log: $LOG_FILE"
exit $EXIT_CODE
