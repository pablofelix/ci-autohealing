#!/bin/bash

# Cron job to collect logs from failed PipelineRuns
# Runs both pod-based and KubeArchive-based collectors

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/logs/cron"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Create log directory
mkdir -p "$LOG_DIR"

# Log file for this run
LOG_FILE="$LOG_DIR/collect-logs-${TIMESTAMP}.log"

echo "========================================" | tee -a "$LOG_FILE"
echo "Cron Log Collection - $(date)" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# Step 1: Collect logs from pods (recent failures, <8 hours)
echo "[1/2] Collecting logs from pods..." | tee -a "$LOG_FILE"
"$PROJECT_DIR/collectors/fetch-logs-from-pods.sh" >> "$LOG_FILE" 2>&1 || true
echo "" | tee -a "$LOG_FILE"

# Step 2: Collect logs from KubeArchive (older failures)
echo "[2/2] Collecting logs from KubeArchive..." | tee -a "$LOG_FILE"
"$PROJECT_DIR/collectors/fetch-logs-kubearchive.sh" >> "$LOG_FILE" 2>&1 || true
echo "" | tee -a "$LOG_FILE"

# Summary
TOTAL=$(docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -tAc \
    "SELECT COUNT(*) FROM build_failures;" 2>/dev/null || echo "0")

WITH_LOGS=$(docker exec ci-autohealing-db psql -U postgres -d konflux_monitoring -tAc \
    "SELECT COUNT(*) FROM build_failures WHERE build_logs IS NOT NULL;" 2>/dev/null || echo "0")

echo "========================================" | tee -a "$LOG_FILE"
echo "Summary:" | tee -a "$LOG_FILE"
echo "  Total failures: $TOTAL" | tee -a "$LOG_FILE"
echo "  With logs: $WITH_LOGS" | tee -a "$LOG_FILE"
echo "  Without logs: $((TOTAL - WITH_LOGS))" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# Keep only last 50 log files
cd "$LOG_DIR"
ls -t collect-logs-*.log | tail -n +51 | xargs -r rm -f

echo "Log saved to: $LOG_FILE"
