#!/usr/bin/env bash
# STYLE.md compliance checks for pre-commit
# Catches common violations of the style guide

set -euo pipefail

ERRORS=0
STAGED=$(git diff --cached --name-only --diff-filter=ACM -- '*.py' | grep -v __pycache__ || true)

if [ -z "$STAGED" ]; then
    exit 0
fi

# 1. No mutable dataclass configs (STYLE.md: use frozen=True for config)
for f in $STAGED; do
    if grep -n '@dataclass$' "$f" 2>/dev/null | grep -v 'frozen' | grep -i 'config\|settings' > /dev/null 2>&1; then
        echo "STYLE: $f — Config dataclass should use @dataclass(frozen=True)"
        ERRORS=$((ERRORS + 1))
    fi
done

# 2. No bare except (STYLE.md: always catch specific exceptions)
for f in $STAGED; do
    if grep -nP '^\s+except:\s*$' "$f" > /dev/null 2>&1; then
        lines=$(grep -nP '^\s+except:\s*$' "$f" | head -3)
        echo "STYLE: $f — Bare 'except:' found (catch specific exceptions):"
        echo "  $lines"
        ERRORS=$((ERRORS + 1))
    fi
done

# 3. No print() in library code (STYLE.md: use logging, print only in CLI)
for f in $STAGED; do
    if echo "$f" | grep -qE '(repositories|services|collectors|clients|proactive)/' ; then
        if grep -nP '^\s+print\(' "$f" > /dev/null 2>&1; then
            echo "STYLE: $f — print() in library code (use logging instead)"
            ERRORS=$((ERRORS + 1))
        fi
    fi
done

# 4. No import * (always explicit imports)
for f in $STAGED; do
    if grep -nP 'from .+ import \*' "$f" > /dev/null 2>&1; then
        echo "STYLE: $f — 'import *' found (use explicit imports)"
        ERRORS=$((ERRORS + 1))
    fi
done

# 5. Duplicated env var defaults (DRY violation)
for f in $STAGED; do
    if grep -oP "os\.environ\.get\('[^']+',\s*'[^']+'\)" "$f" 2>/dev/null | sort | uniq -d | head -1 | grep -q .; then
        dups=$(grep -oP "os\.environ\.get\('[^']+',\s*'[^']+'\)" "$f" | sort | uniq -d | head -3)
        echo "STYLE: $f — Duplicated env var defaults (DRY): $dups"
        ERRORS=$((ERRORS + 1))
    fi
done

if [ $ERRORS -gt 0 ]; then
    echo ""
    echo "Found $ERRORS style violation(s). See STYLE.md for details."
    echo "Fix violations or use --no-verify to skip (not recommended)."
    exit 1
fi

echo "All style checks passed!"
