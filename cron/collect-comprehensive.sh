#!/bin/bash

# Cron job to collect comprehensive failure data
# Uses Python comprehensive collector for last failure per component

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON_DIR="$PROJECT_DIR/collectors/python"
LOG_DIR="$PROJECT_DIR/logs/cron"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Create log directory
mkdir -p "$LOG_DIR"

# Log file for this run
LOG_FILE="$LOG_DIR/collect-comprehensive-${TIMESTAMP}.log"

{
    echo "========================================================================"
    echo "Comprehensive CI Failure Collection - $(date)"
    echo "========================================================================"
    echo ""

    # Step 1: Run comprehensive Python collector
    echo "[1/2] Running comprehensive collector..."
    echo ""

    cd "$PYTHON_DIR"
    python3 collect_comprehensive.py 2>&1

    echo ""
    echo "========================================================================"
    echo "[2/2] Synchronizing component status..."
    echo "========================================================================"
    echo ""

    # Step 2: Sync component status (mark resolved, record successes)
    python3 sync_component_status.py 2>&1

    echo ""
    echo "========================================================================"
    echo "[3/5] Updating sync status cache..."
    echo "========================================================================"
    echo ""

    python3 check_sync_status.py 2>&1

    echo ""
    echo "========================================================================"
    echo "[4/5] Checking Conforma test status..."
    echo "========================================================================"
    echo ""

    python3 check_conforma_status.py 2>&1

    echo ""
    echo "========================================================================"
    echo "[5/5] Collecting Conforma failure details..."
    echo "========================================================================"
    echo ""

    python3 collect_conforma.py 2>&1

    echo ""
    echo "========================================================================"
    echo "Summary from Database"
    echo "========================================================================"

    # Get statistics from database
    "$PROJECT_DIR/ic" stats 2>&1 || true

    echo ""
    echo "========================================================================"
    echo "Collection complete - $(date)"
    echo "========================================================================"

} | tee "$LOG_FILE"

# Keep only last 50 log files
cd "$LOG_DIR"
ls -t collect-comprehensive-*.log 2>/dev/null | tail -n +51 | xargs -r rm -f

echo ""
echo "Log saved to: $LOG_FILE"
