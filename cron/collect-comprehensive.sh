#!/bin/bash

# Cron job to collect comprehensive failure data
# Uses Python comprehensive collector for last failure per component

set -e

# Ensure oc and other tools are in PATH for cron environment
export PATH="$HOME/.local/bin:$HOME/bin:/usr/local/bin:$PATH"

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

    # Step 6: Commit context collection (optional - only if GITHUB_TOKEN is set)
    if [ -n "$GITHUB_TOKEN" ]; then
        echo ""
        echo "========================================================================"
        echo "[6/7] Collecting commit context from GitHub..."
        echo "========================================================================"
        echo ""
        python3.11 collect_commit_context.py 2>&1 || {
            echo "Commit context collection failed (non-critical - continuing)"
        }
    else
        echo ""
        echo "[6/7] Skipping commit context collection (GITHUB_TOKEN not configured)"
    fi

    # Step 7: AI analysis (optional - only if LLM_PROVIDER is set)
    # Note: Uses python3.11 for anthropic SDK compatibility (requires Python 3.7+)
    if [ -n "$LLM_PROVIDER" ]; then
        echo ""
        echo "========================================================================"
        echo "[7/7] Running AI analysis on new failures..."
        echo "========================================================================"
        echo ""
        python3.11 analyze_failures.py 2>&1 || {
            echo "AI analysis failed (non-critical - continuing)"
        }
    else
        echo ""
        echo "[7/7] Skipping AI analysis (LLM_PROVIDER not configured)"
    fi

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
