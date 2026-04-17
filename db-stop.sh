#!/bin/bash

# Stop PostgreSQL Docker container

cd "$(dirname "${BASH_SOURCE[0]}")"

echo "Stopping CI Auto-Healing PostgreSQL..."

# Check if docker/podman is available
if command -v docker &> /dev/null; then
    DOCKER_CMD="docker"
elif command -v podman &> /dev/null; then
    DOCKER_CMD="podman"
else
    echo "Error: Neither docker nor podman found."
    exit 1
fi

$DOCKER_CMD stop ci-autohealing-db

echo "✓ PostgreSQL stopped"
echo ""
echo "Data preserved in: ./data/postgres"
echo "To start again: ./db-start.sh"
echo ""
echo "To remove container completely:"
echo "  $DOCKER_CMD rm ci-autohealing-db"
