#!/bin/bash

# Start PostgreSQL Docker container for CI Auto-Healing

set -e

cd "$(dirname "${BASH_SOURCE[0]}")"

echo "========================================"
echo "Starting CI Auto-Healing PostgreSQL"
echo "========================================"
echo ""

# Check if docker/podman is available
if command -v docker &> /dev/null; then
    DOCKER_CMD="docker"
elif command -v podman &> /dev/null; then
    DOCKER_CMD="podman"
else
    echo "Error: Neither docker nor podman found. Please install docker."
    exit 1
fi

echo "Using: $DOCKER_CMD"
echo ""

# Create named volume if it doesn't exist
if ! $DOCKER_CMD volume ls | grep -q ci-autohealing-pgdata; then
    echo "Creating persistent volume..."
    $DOCKER_CMD volume create ci-autohealing-pgdata
fi

# Check if container already exists
if $DOCKER_CMD ps -a --format '{{.Names}}' | grep -q '^ci-autohealing-db$'; then
    echo "Container already exists. Starting..."
    $DOCKER_CMD start ci-autohealing-db
else
    echo "Creating and starting PostgreSQL container..."
    $DOCKER_CMD run -d \
        --name ci-autohealing-db \
        -e POSTGRES_USER=postgres \
        -e POSTGRES_PASSWORD=admin \
        -e POSTGRES_DB=konflux_monitoring \
        -p 5433:5432 \
        -v ci-autohealing-pgdata:/var/lib/postgresql/data \
        --restart unless-stopped \
        postgres:14-alpine
fi

echo ""
echo "Waiting for PostgreSQL to be ready..."
sleep 5

# Check health
for i in {1..15}; do
    if $DOCKER_CMD exec ci-autohealing-db pg_isready -U postgres &> /dev/null; then
        echo "✓ PostgreSQL is ready!"

        # Check if schema needs to be applied
        TABLE_COUNT=$($DOCKER_CMD exec ci-autohealing-db psql -U postgres -d konflux_monitoring -tAc "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'" 2>/dev/null || echo "0")

        if [ "$TABLE_COUNT" = "0" ]; then
            echo ""
            echo "Applying database schema..."
            $DOCKER_CMD exec -i ci-autohealing-db psql -U postgres -d konflux_monitoring < db/schema.sql
            echo "✓ Schema applied"
        else
            echo "✓ Database already initialized ($TABLE_COUNT tables)"
        fi

        break
    fi
    echo "  Waiting... ($i/15)"
    sleep 2
done

echo ""
echo "========================================"
echo "PostgreSQL Started Successfully"
echo "========================================"
echo ""
echo "Connection details:"
echo "  Host: localhost"
echo "  Port: 5433"
echo "  Database: konflux_monitoring"
echo "  User: postgres"
echo "  Password: admin"
echo ""
echo "Commands:"
echo "  Stop:   ./db-stop.sh"
echo "  Logs:   $DOCKER_CMD logs -f ci-autohealing-db"
echo "  Shell:  $DOCKER_CMD exec -it ci-autohealing-db psql -U postgres -d konflux_monitoring"
echo ""
echo "Data volume: ci-autohealing-pgdata"
echo "  View: $DOCKER_CMD volume inspect ci-autohealing-pgdata"
echo ""
