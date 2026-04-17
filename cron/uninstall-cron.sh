#!/bin/bash

# Uninstall cron job for log collection

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRON_SCRIPT="$SCRIPT_DIR/collect-logs.sh"

echo "Uninstalling cron job for log collection..."
echo ""

# Check if exists
if ! crontab -l 2>/dev/null | grep -q "$CRON_SCRIPT"; then
    echo "Cron job not found"
    exit 0
fi

# Show what will be removed
echo "Current cron job:"
crontab -l 2>/dev/null | grep -A 1 "CI Auto-Healing"
echo ""

read -p "Remove this cron job? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled"
    exit 0
fi

# Remove cron job and comment
crontab -l 2>/dev/null | grep -v "CI Auto-Healing" | grep -v "$CRON_SCRIPT" | crontab -

echo "✓ Cron job removed successfully"
