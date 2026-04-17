#!/bin/bash

# Database migration script for CI Auto-Healing system
# Creates database and applies schema

set -e  # Exit on error

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Database configuration (same as Langfuse)
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-konflux_monitoring}"
LANGFUSE_DB="${LANGFUSE_DB:-langfuse}"

echo "========================================"
echo "CI Auto-Healing Database Migration"
echo "========================================"
echo ""

# Check if psql is available
if ! command -v psql &> /dev/null; then
    echo -e "${RED}Error: psql not found. Please install PostgreSQL client.${NC}"
    exit 1
fi

# Check connection
echo -e "${YELLOW}Testing PostgreSQL connection...${NC}"
if ! psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$LANGFUSE_DB" -c '\q' 2>/dev/null; then
    echo -e "${RED}Error: Cannot connect to PostgreSQL${NC}"
    echo "Please check:"
    echo "  - PostgreSQL is running"
    echo "  - DB_HOST, DB_PORT, DB_USER are correct"
    echo "  - You have proper credentials"
    exit 1
fi
echo -e "${GREEN}✓ Connection successful${NC}"
echo ""

# Check if database exists
echo -e "${YELLOW}Checking if database '$DB_NAME' exists...${NC}"
DB_EXISTS=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$LANGFUSE_DB" -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")

if [ "$DB_EXISTS" = "1" ]; then
    echo -e "${YELLOW}Database '$DB_NAME' already exists.${NC}"
    read -p "Do you want to drop and recreate it? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Dropping database '$DB_NAME'...${NC}"
        psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$LANGFUSE_DB" -c "DROP DATABASE IF EXISTS $DB_NAME;"
        echo -e "${GREEN}✓ Database dropped${NC}"

        echo -e "${YELLOW}Creating database '$DB_NAME'...${NC}"
        psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$LANGFUSE_DB" -c "CREATE DATABASE $DB_NAME;"
        echo -e "${GREEN}✓ Database created${NC}"
    else
        echo -e "${YELLOW}Skipping database creation, will update schema only${NC}"
    fi
else
    echo -e "${YELLOW}Creating database '$DB_NAME'...${NC}"
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$LANGFUSE_DB" -c "CREATE DATABASE $DB_NAME;"
    echo -e "${GREEN}✓ Database created${NC}"
fi

echo ""
echo -e "${YELLOW}Applying schema...${NC}"

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Apply schema
if psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$SCRIPT_DIR/schema.sql"; then
    echo -e "${GREEN}✓ Schema applied successfully${NC}"
else
    echo -e "${RED}Error: Failed to apply schema${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}Verifying tables...${NC}"

# List tables
TABLES=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc "
    SELECT COUNT(*) FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
")

echo -e "${GREEN}✓ Created $TABLES tables${NC}"

# Show table list
echo ""
echo "Tables created:"
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
    SELECT table_name,
           (SELECT COUNT(*) FROM information_schema.columns
            WHERE table_name = t.table_name) as columns
    FROM information_schema.tables t
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    ORDER BY table_name;
"

echo ""
echo "Views created:"
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
    SELECT table_name
    FROM information_schema.views
    WHERE table_schema = 'public'
    ORDER BY table_name;
"

echo ""
echo "========================================"
echo -e "${GREEN}Migration completed successfully!${NC}"
echo "========================================"
echo ""
echo "Connection details:"
echo "  Host: $DB_HOST"
echo "  Port: $DB_PORT"
echo "  Database: $DB_NAME"
echo "  User: $DB_USER"
echo ""
echo "To connect:"
echo "  psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME"
echo ""
echo "Next steps:"
echo "  1. Update .env file with database credentials"
echo "  2. Run initial scan: ./collectors/scanner.py --mode trigger"
echo "  3. Start daemon: ./collectors/scanner.py --mode daemon"
echo ""
