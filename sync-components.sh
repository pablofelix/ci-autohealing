#!/bin/bash
#
# Quick component sync script
# Usage:
#   ./sync-components.sh                    # Interactive mode
#   ./sync-components.sh comp1 comp2 comp3  # Add specific components
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/src/update_components.py"

echo "========================================================================"
echo "Component Synchronization Helper"
echo "========================================================================"
echo ""

if [ $# -eq 0 ]; then
    # No arguments - interactive mode
    echo "Copy the failing component names from Konflux UI and paste here."
    echo "You can paste the whole list (from 'Latest push build' section)."
    echo "The script will extract the component names automatically."
    echo ""
    echo "Tip: Component names look like: odh-spark-operator-v3-4"
    echo ""

    python3 "$PYTHON_SCRIPT"
else
    # Arguments provided - add them directly
    echo "Adding components: $@"
    echo ""

    python3 "$PYTHON_SCRIPT" "$@"
fi

echo ""
echo "Next: Run ./cron/collect-comprehensive.sh to collect the new failures"
