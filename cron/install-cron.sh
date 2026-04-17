#!/bin/bash

# Install cron job for automatic log collection

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRON_SCRIPT="$SCRIPT_DIR/collect-logs.sh"

# Check if cron is available
if ! command -v crontab &> /dev/null; then
    echo "Error: crontab command not found"
    exit 1
fi

# Cron schedule: Every 15 minutes
CRON_SCHEDULE="*/15 * * * *"

# Cron job entry
CRON_JOB="$CRON_SCHEDULE $CRON_SCRIPT"
CRON_COMMENT="# CI Auto-Healing: Collect logs from failed PipelineRuns"

echo "Installing cron job for log collection..."
echo ""
echo "Schedule: Every 15 minutes"
echo "Script: $CRON_SCRIPT"
echo ""

# Check if already installed
if crontab -l 2>/dev/null | grep -q "$CRON_SCRIPT"; then
    echo "Warning: Cron job already exists"
    echo ""
    echo "Current crontab:"
    crontab -l 2>/dev/null | grep "$CRON_SCRIPT"
    echo ""
    read -p "Remove and reinstall? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Cancelled"
        exit 0
    fi

    # Remove existing
    crontab -l 2>/dev/null | grep -v "$CRON_SCRIPT" | crontab -
fi

# Add new cron job
(crontab -l 2>/dev/null; echo "$CRON_COMMENT"; echo "$CRON_JOB") | crontab -

echo "✓ Cron job installed successfully"
echo ""
echo "Current crontab:"
crontab -l 2>/dev/null | grep -A 1 "CI Auto-Healing"
echo ""
echo "To remove: ./cron/uninstall-cron.sh"
echo "To test: ./cron/collect-logs.sh"
