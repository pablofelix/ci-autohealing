#!/bin/bash

# Uninstall cron job for CI failure collection

echo "Uninstalling cron job..."
echo ""

if ! crontab -l 2>/dev/null | grep -q "collect-comprehensive"; then
    echo "Cron job not found"
    exit 0
fi

echo "Current cron job:"
crontab -l 2>/dev/null | grep -A 1 "CI Auto-Healing"
echo ""

read -p "Remove this cron job? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled"
    exit 0
fi

crontab -l 2>/dev/null | grep -v "CI Auto-Healing" | grep -v "collect-comprehensive" | crontab -

echo "Cron job removed successfully"
