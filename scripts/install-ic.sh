#!/usr/bin/env bash
set -euo pipefail

# Install ic CLI tool (cluster mode only — lightweight, no DB dependencies)
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/pablofelix/ci-autohealing/develop/scripts/install-ic.sh | bash
#
# Or download and run:
#   ./install-ic.sh

IC_VERSION="${IC_VERSION:-develop}"
IC_REPO="https://github.com/pablofelix/ci-autohealing"
IC_INSTALL_DIR="${IC_INSTALL_DIR:-$HOME/.local}"
IC_VENV_DIR="$IC_INSTALL_DIR/share/ic-tool"

echo "Installing ic CLI tool..."
echo "  Version: $IC_VERSION"
echo "  Install dir: $IC_INSTALL_DIR"
echo

# Check Python
PYTHON=""
for py in python3.11 python3.10 python3.9 python3; do
    if command -v "$py" &>/dev/null; then
        PYTHON="$py"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "Error: Python 3.9+ is required"
    exit 1
fi

PY_VERSION=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  Python: $PYTHON ($PY_VERSION)"

# Create venv
echo "  Creating virtual environment..."
$PYTHON -m venv "$IC_VENV_DIR" 2>/dev/null || {
    echo "Error: python3-venv not installed. Run: sudo dnf install python3-virtualenv"
    exit 1
}

# Install ic-tool from GitHub
echo "  Installing ic-tool..."
"$IC_VENV_DIR/bin/pip" install --quiet --upgrade pip
"$IC_VENV_DIR/bin/pip" install --quiet "git+${IC_REPO}.git@${IC_VERSION}"

# Create wrapper script
mkdir -p "$IC_INSTALL_DIR/bin"
cat > "$IC_INSTALL_DIR/bin/ic" <<WRAPPER
#!/usr/bin/env bash
exec "$IC_VENV_DIR/bin/ic" "\$@"
WRAPPER
chmod +x "$IC_INSTALL_DIR/bin/ic"

echo
echo "Done! ic installed to $IC_INSTALL_DIR/bin/ic"
echo
echo "Quick start:"
echo "  ic config use-cluster https://YOUR-API-URL --api-key YOUR_KEY"
echo "  ic get apps"
echo "  ic get alerts"
echo
if ! echo "$PATH" | grep -q "$IC_INSTALL_DIR/bin"; then
    echo "Add to PATH (if not already):"
    echo "  export PATH=\"$IC_INSTALL_DIR/bin:\$PATH\""
fi
