#!/usr/bin/env bash
# STYLE.md compliance checks for pre-commit
# Catches common violations of the style guide:
#   - Style rules (frozen dataclasses, bare except, print in libs, import *)
#   - Security (shell=True, SQL injection patterns)
#   - Performance (missing timeouts on HTTP/subprocess calls)
#   - Code smells (helpers/utils/common modules, god classes)
#   - Anti-patterns (mutable globals, deep inheritance, singleton __new__)

set -euo pipefail

ERRORS=0
WARNINGS=0
STAGED=$(git diff --cached --name-only --diff-filter=ACM -- '*.py' | grep -v __pycache__ || true)

if [ -z "$STAGED" ]; then
    exit 0
fi

err() {
    echo "STYLE ERROR: $1"
    ERRORS=$((ERRORS + 1))
}

warn() {
    echo "STYLE WARN:  $1"
    WARNINGS=$((WARNINGS + 1))
}

# ─── STYLE RULES ───────────────────────────────────────────────

# 1. No mutable dataclass configs (STYLE.md: use frozen=True for config)
for f in $STAGED; do
    if grep -n '@dataclass$' "$f" 2>/dev/null | grep -v 'frozen' | grep -i 'config\|settings' > /dev/null 2>&1; then
        err "$f — Config dataclass should use @dataclass(frozen=True)"
    fi
done

# 2. No bare except (STYLE.md: always catch specific exceptions)
for f in $STAGED; do
    if grep -nP '^\s+except:\s*$' "$f" > /dev/null 2>&1; then
        lines=$(grep -nP '^\s+except:\s*$' "$f" | head -3)
        err "$f — Bare 'except:' found (catch specific exceptions): $lines"
    fi
done

# 3. No print() in library code (STYLE.md: use logging, print only in CLI)
for f in $STAGED; do
    if echo "$f" | grep -qE '(repositories|services|collectors|clients|proactive|analyzers)/' ; then
        if grep -nP '^\s+print\(' "$f" > /dev/null 2>&1; then
            err "$f — print() in library code (use logging instead)"
        fi
    fi
done

# 4. No import * (always explicit imports)
for f in $STAGED; do
    if grep -nP 'from .+ import \*' "$f" > /dev/null 2>&1; then
        err "$f — 'import *' found (use explicit imports)"
    fi
done

# 5. Duplicated env var defaults (DRY violation)
for f in $STAGED; do
    if grep -oP "os\.environ\.get\('[^']+',\s*'[^']+'\)" "$f" 2>/dev/null | sort | uniq -d | head -1 | grep -q .; then
        dups=$(grep -oP "os\.environ\.get\('[^']+',\s*'[^']+'\)" "$f" | sort | uniq -d | head -3)
        err "$f — Duplicated env var defaults (DRY): $dups"
    fi
done

# ─── SECURITY ──────────────────────────────────────────────────

# 6. No shell=True in subprocess calls
for f in $STAGED; do
    if grep -nP 'subprocess\.(run|call|Popen|check_output|check_call)\(.*shell\s*=\s*True' "$f" > /dev/null 2>&1; then
        lines=$(grep -nP 'subprocess\.(run|call|Popen|check_output|check_call)\(.*shell\s*=\s*True' "$f" | head -3)
        err "$f — shell=True is a security risk (STYLE.md: pass args as list): $lines"
    fi
done

# 7. No f-string/format SQL (SQL injection risk)
for f in $STAGED; do
    if echo "$f" | grep -qE '(repositories|cli/db)'; then
        if grep -nP '\.execute\(f["\x27]' "$f" > /dev/null 2>&1; then
            lines=$(grep -nP '\.execute\(f["\x27]' "$f" | head -3)
            err "$f — f-string in SQL execute() (use parameterized queries): $lines"
        fi
        if grep -nP '\.execute\(["\x27].*\.format\(' "$f" > /dev/null 2>&1; then
            lines=$(grep -nP '\.execute\(["\x27].*\.format\(' "$f" | head -3)
            err "$f — .format() in SQL execute() (use parameterized queries): $lines"
        fi
    fi
done

# ─── PERFORMANCE ───────────────────────────────────────────────

# 8. HTTP requests without timeout
for f in $STAGED; do
    if echo "$f" | grep -qE '(clients|collectors|proactive)/'; then
        if grep -nP 'requests\.(get|post|put|patch|delete)\(' "$f" 2>/dev/null | grep -v 'timeout' > /dev/null 2>&1; then
            lines=$(grep -nP 'requests\.(get|post|put|patch|delete)\(' "$f" | grep -v 'timeout' | head -3)
            warn "$f — HTTP request without timeout= (STYLE.md: set timeouts on all network calls): $lines"
        fi
    fi
done

# 9. subprocess without timeout
for f in $STAGED; do
    if grep -nP 'subprocess\.(run|call|check_output|check_call)\(' "$f" 2>/dev/null | grep -v 'timeout' > /dev/null 2>&1; then
        lines=$(grep -nP 'subprocess\.(run|call|check_output|check_call)\(' "$f" | grep -v 'timeout' | head -3)
        warn "$f — subprocess call without timeout= (STYLE.md: always set timeout): $lines"
    fi
done

# ─── CODE SMELLS ───────────────────────────────────────────────

# 10. Forbidden directory names (STYLE.md: no utils/, helpers/, common/, misc/)
for f in $STAGED; do
    if echo "$f" | grep -qE '/(utils|helpers|common|misc)/'; then
        err "$f — File inside forbidden directory (STYLE.md line 217: no utils/, helpers/, common/, misc/)"
    fi
    basename=$(basename "$f" .py)
    if echo "$basename" | grep -qxE 'helpers|common|misc'; then
        err "$f — Forbidden module name '$basename' (STYLE.md: name after domain, not purpose)"
    fi
done

# 10b. No unittest.TestCase in new code (STYLE.md: use pytest)
for f in $STAGED; do
    if echo "$f" | grep -qE 'tests/'; then
        if grep -nP 'class\s+\w+\(unittest\.TestCase\)' "$f" > /dev/null 2>&1; then
            lines=$(grep -nP 'class\s+\w+\(unittest\.TestCase\)' "$f" | head -3)
            warn "$f — unittest.TestCase in new test (STYLE.md: use pytest, not unittest): $lines"
        fi
    fi
done

# 11. %-style string formatting in logging (STYLE.md says use %s lazy formatting,
#     but .format() and f-strings in log calls defeat lazy evaluation)
for f in $STAGED; do
    if echo "$f" | grep -qE '(repositories|clients|collectors|proactive|analyzers)/'; then
        if grep -nP 'logger\.(debug|info|warning|error|critical)\(f["\x27]' "$f" > /dev/null 2>&1; then
            lines=$(grep -nP 'logger\.(debug|info|warning|error|critical)\(f["\x27]' "$f" | head -3)
            warn "$f — f-string in logger call (STYLE.md: use %%s lazy formatting): $lines"
        fi
    fi
done

# 12. Functions over 100 lines (code smell: function doing too much)
for f in $STAGED; do
    python3.11 -c "
import ast, sys
try:
    tree = ast.parse(open('$f').read())
except SyntaxError:
    sys.exit(0)
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        size = node.end_lineno - node.lineno + 1
        if size > 100:
            print(f'STYLE WARN:  $f:{node.lineno} — Function {node.name}() is {size} lines (max ~100)')
" 2>/dev/null || true
done

# ─── ANTI-PATTERNS ─────────────────────────────────────────────

# 13. Singleton __new__ (STYLE.md: use module-level instances or factory functions)
for f in $STAGED; do
    if grep -nP 'def __new__\(cls' "$f" > /dev/null 2>&1; then
        if grep -nP '_instance' "$f" > /dev/null 2>&1; then
            warn "$f — Singleton __new__ pattern detected (STYLE.md: use module-level instance or factory)"
        fi
    fi
done

# 14. Deep inheritance (more than 2 levels of class inheritance)
for f in $STAGED; do
    python3.11 -c "
import ast, sys
try:
    tree = ast.parse(open('$f').read())
except SyntaxError:
    sys.exit(0)
classes = {}
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef):
        bases = [b.id if isinstance(b, ast.Name) else '' for b in node.bases]
        classes[node.name] = bases
for name, bases in classes.items():
    for base in bases:
        if base in classes and classes[base]:
            for grandbase in classes[base]:
                if grandbase in classes:
                    print(f'STYLE WARN:  $f — Deep inheritance: {name} → {base} → {grandbase} (prefer composition)')
" 2>/dev/null || true
done

# 15. Mutable module-level state (lists/dicts that get mutated)
for f in $STAGED; do
    if echo "$f" | grep -qE '(repositories|clients|collectors|analyzers)/'; then
        if grep -nP '^[A-Z_]+\s*=\s*\[\]' "$f" > /dev/null 2>&1; then
            lines=$(grep -nP '^[A-Z_]+\s*=\s*\[\]' "$f" | head -3)
            warn "$f — Mutable module-level list (consider frozen tuple or function): $lines"
        fi
        if grep -nP '^[A-Z_]+\s*=\s*\{\}' "$f" > /dev/null 2>&1; then
            if ! grep -nP '^[A-Z_]+\s*=\s*\{.+\}' "$f" > /dev/null 2>&1; then
                lines=$(grep -nP '^[A-Z_]+\s*=\s*\{\}' "$f" | head -3)
                warn "$f — Mutable module-level dict (consider frozen or function): $lines"
            fi
        fi
    fi
done

# 16. None-as-error-signal in pure functions (STYLE.md: raise, don't return None for errors)
for f in $STAGED; do
    if echo "$f" | grep -qE '(parsers|extractors|validators)'; then
        if grep -nP 'except.*:\s*$' "$f" 2>/dev/null | head -1 | grep -q .; then
            # Check if the except block returns None
            python3.11 -c "
import ast, sys
try:
    tree = ast.parse(open('$f').read())
except SyntaxError:
    sys.exit(0)
for node in ast.walk(tree):
    if isinstance(node, ast.ExceptHandler):
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and child.value is None:
                print(f'STYLE WARN:  $f:{child.lineno} — return None in except block of pure function (STYLE.md: raise, don\\'t return None for errors)')
" 2>/dev/null || true
        fi
    fi
done

# ─── SUMMARY ──────────────────────────────────────────────────

if [ $ERRORS -gt 0 ]; then
    echo ""
    echo "Found $ERRORS error(s) and $WARNINGS warning(s)."
    echo "Fix errors before committing. See STYLE.md for details."
    exit 1
fi

if [ $WARNINGS -gt 0 ]; then
    echo "All checks passed with $WARNINGS warning(s)."
else
    echo "All style checks passed!"
fi
