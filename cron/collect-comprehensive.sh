#!/bin/bash

# Cron job to collect comprehensive failure data
# Uses Python comprehensive collector for last failure per component

set -e

# Ensure oc and other tools are in PATH for cron environment
export PATH="$HOME/.local/bin:$HOME/bin:/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON_DIR="$PROJECT_DIR/src"
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

    # Step 2.5: Verify fix resolution attempts (optional - only if GITHUB_TOKEN is set)
    # Must run after sync_component_status.py so is_resolved is up to date.
    if [ -n "$GITHUB_TOKEN" ]; then
        echo ""
        echo "========================================================================"
        echo "[2.5] Verifying fix resolution attempts..."
        echo "========================================================================"
        echo ""
        python3 fixers/verify_fixes.py 2>&1 || {
            echo "Fix verification failed (non-critical - continuing)"
        }
    else
        echo ""
        echo "[2.5] Skipping fix verification (GITHUB_TOKEN not configured)"
    fi

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
        python3 collect_commit_context.py 2>&1 || {
            echo "Commit context collection failed (non-critical - continuing)"
        }
    else
        echo ""
        echo "[6/7] Skipping commit context collection (GITHUB_TOKEN not configured)"
    fi

    # Step 7: AI analysis (optional - only if LLM_PROVIDER is set)
    if [ -n "$LLM_PROVIDER" ]; then
        echo ""
        echo "========================================================================"
        echo "[7/8] Running AI analysis on new failures..."
        echo "========================================================================"
        echo ""
        python3 analyze_failures.py 2>&1 || {
            echo "AI analysis failed (non-critical - continuing)"
        }
    else
        echo ""
        echo "[7/8] Skipping AI analysis (LLM_PROVIDER not configured)"
    fi

    # Step 7.5: Autonomous conforma fix (optional - requires GITHUB_TOKEN + AUTONOMOUS_MODE=true)
    # Creates PRs for high-confidence auto-fixable violations without human approval.
    # Off by default; enable with AUTONOMOUS_MODE=true in .env after manual validation.
    if [ -n "$GITHUB_TOKEN" ] && [ "${AUTONOMOUS_MODE:-false}" = "true" ]; then
        echo ""
        echo "========================================================================"
        echo "[7.5/8] Running autonomous conforma fix (AUTONOMOUS_MODE=true)..."
        echo "========================================================================"
        echo ""
        python3 fixers/auto_fix.py 2>&1 || {
            echo "Autonomous fix failed (non-critical - continuing)"
        }
    fi

    # Step 8: Doc context collection for error patterns
    # Fetches doc pages for known patterns so next analysis cycle has richer context.
    # Skipped gracefully when VPN / network is unavailable.
    echo ""
    echo "========================================================================"
    echo "[8/8] Refreshing doc context for error patterns..."
    echo "========================================================================"
    echo ""
    python3 collect_doc_context.py 2>&1 || {
        echo "Doc context collection failed (non-critical - continuing)"
    }

    # Step 9: Poll Jira comments (requires JIRA_EMAIL + JIRA_TOKEN + LLM_PROVIDER)
    if [ -n "$JIRA_EMAIL" ] && [ -n "$JIRA_TOKEN" ] && [ -n "$LLM_PROVIDER" ]; then
        echo ""
        echo "========================================================================"
        echo "[9/9] Polling Jira tickets for new comments..."
        echo "========================================================================"
        echo ""
        python3 poll_jira_comments.py 2>&1 || {
            echo "Jira comment polling failed (non-critical - continuing)"
        }
    else
        echo ""
        echo "[9/9] Skipping Jira comment polling (JIRA_EMAIL, JIRA_TOKEN, or LLM_PROVIDER not configured)"
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
