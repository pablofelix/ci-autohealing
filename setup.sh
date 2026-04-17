#!/bin/bash

# Setup script for CI Auto-Healing System
# Run this first time to initialize everything

set -e  # Exit on error

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "========================================"
echo -e "${BLUE}CI Auto-Healing System Setup${NC}"
echo "========================================"
echo ""

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Step 1: Check prerequisites
echo -e "${YELLOW}[1/7] Checking prerequisites...${NC}"

MISSING_DEPS=()

if ! command -v python3 &> /dev/null; then
    MISSING_DEPS+=("python3")
fi

if ! command -v psql &> /dev/null; then
    MISSING_DEPS+=("postgresql-client")
fi

if ! command -v oc &> /dev/null; then
    MISSING_DEPS+=("oc (OpenShift CLI)")
fi

if [ ${#MISSING_DEPS[@]} -ne 0 ]; then
    echo -e "${RED}Missing required dependencies:${NC}"
    printf '  - %s\n' "${MISSING_DEPS[@]}"
    echo ""
    echo "Please install them and run this script again."
    exit 1
fi

echo -e "${GREEN}✓ All prerequisites found${NC}"
echo ""

# Step 2: Create .env file
echo -e "${YELLOW}[2/7] Creating .env file...${NC}"

if [ -f ".env" ]; then
    echo -e "${YELLOW}⚠ .env already exists, skipping...${NC}"
else
    cp .env.example .env
    echo -e "${GREEN}✓ Created .env file${NC}"
    echo -e "${YELLOW}⚠ Please edit .env and configure:${NC}"
    echo "  - Database credentials (DB_PASSWORD)"
    echo "  - Anthropic API key (ANTHROPIC_API_KEY)"
    echo "  - Langfuse keys (LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY)"
    echo ""
    read -p "Press Enter when .env is configured..."
fi
echo ""

# Step 3: Create Python virtual environment
echo -e "${YELLOW}[3/7] Setting up Python environment...${NC}"

if [ -d "venv" ]; then
    echo -e "${YELLOW}⚠ venv already exists, skipping...${NC}"
else
    python3 -m venv venv
    echo -e "${GREEN}✓ Created virtual environment${NC}"
fi

# Activate venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip > /dev/null

# Install dependencies
echo -e "${YELLOW}Installing Python dependencies...${NC}"
pip install -r requirements.txt
echo -e "${GREEN}✓ Dependencies installed${NC}"
echo ""

# Step 4: Setup database
echo -e "${YELLOW}[4/7] Setting up database...${NC}"
cd db
chmod +x migrate.sh
./migrate.sh
cd ..
echo ""

# Step 5: Create log directory
echo -e "${YELLOW}[5/7] Creating log directory...${NC}"
mkdir -p logs
echo -e "${GREEN}✓ Log directory created${NC}"
echo ""

# Step 6: Install Claude Code skill
echo -e "${YELLOW}[6/7] Installing Claude Code skill...${NC}"

CLAUDE_SKILLS_DIR="$HOME/.claude/skills"
mkdir -p "$CLAUDE_SKILLS_DIR"

# Copy skill
cp skills/ci-build.skill.md "$CLAUDE_SKILLS_DIR/"
echo -e "${GREEN}✓ Installed /ci-build skill${NC}"
echo "  You can now use: /ci-build in Claude Code"
echo ""

# Step 7: Make scripts executable
echo -e "${YELLOW}[7/7] Making scripts executable...${NC}"
chmod +x collectors/scanner.py
chmod +x db/migrate.sh
echo -e "${GREEN}✓ Scripts are executable${NC}"
echo ""

# Done!
echo "========================================"
echo -e "${GREEN}Setup Complete!${NC}"
echo "========================================"
echo ""
echo -e "${BLUE}Next Steps:${NC}"
echo ""
echo "1. Activate virtual environment:"
echo "   ${YELLOW}source venv/bin/activate${NC}"
echo ""
echo "2. Test scanner:"
echo "   ${YELLOW}python3 collectors/scanner.py --mode trigger${NC}"
echo ""
echo "3. Start daemon (optional):"
echo "   ${YELLOW}python3 collectors/scanner.py --mode daemon${NC}"
echo ""
echo "4. Use in Claude Code:"
echo "   ${YELLOW}/ci-build${NC}                    - Show dashboard"
echo "   ${YELLOW}/ci-build scan${NC}               - Scan for failures"
echo "   ${YELLOW}/ci-build <component>${NC}        - Analyze component"
echo ""
echo "5. View dashboard in Grafana:"
echo "   (Setup Grafana connection to PostgreSQL)"
echo ""
echo -e "${GREEN}Happy troubleshooting!${NC}"
echo ""
