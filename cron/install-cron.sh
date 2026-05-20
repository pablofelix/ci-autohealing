#!/bin/bash

# Install cron job for comprehensive CI failure collection

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRON_SCRIPT="python3.11 $SCRIPT_DIR/collect_comprehensive.py"

if ! command -v crontab &> /dev/null; then
    echo "Error: crontab command not found"
    exit 1
fi

# Cron schedule: Every 20 minutes
CRON_SCHEDULE="*/20 * * * *"

CRON_JOB="$CRON_SCHEDULE $CRON_SCRIPT"
CRON_COMMENT="# CI Auto-Healing: Comprehensive collection of last failure per component"

echo "Installing cron job for CI failure collection..."
echo ""
echo "Schedule: Every 20 minutes"
echo "Script: $CRON_SCRIPT"
echo ""

# Check if already installed
if crontab -l 2>/dev/null | grep -q "collect-comprehensive"; then
    echo "Warning: Cron job already exists"
    echo ""
    echo "Current crontab:"
    crontab -l 2>/dev/null | grep "collect-comprehensive"
    echo ""
    read -p "Remove and reinstall? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Cancelled"
        exit 0
    fi

    crontab -l 2>/dev/null | grep -v "collect-comprehensive" | crontab -
fi

(crontab -l 2>/dev/null; echo "$CRON_COMMENT"; echo "$CRON_JOB") | crontab -

echo "Cron job installed successfully"
echo ""
echo "Current crontab:"
crontab -l 2>/dev/null | grep -A 1 "CI Auto-Healing"
echo ""
echo "To remove: ./cron/uninstall-cron.sh"
echo "To test: python3.11 ./cron/collect_comprehensive.py"
