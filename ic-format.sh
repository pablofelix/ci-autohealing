#!/bin/bash
# ic-format.sh — Output formatting for ic CLI
# Sourced by ic; do not execute directly.

DIM='\033[2m'
HEADER_COLOR='\033[1;38;5;110m'

section_header() {
    echo -e "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${HEADER_COLOR}$1${NC}"
    echo -e "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

subsection_header() {
    echo -e "${BOLD}$1${NC}"
}

trim() {
    echo "$1" | xargs 2>/dev/null || echo ""
}

clipboard_or_print() {
    local output="$1" clipboard="${2:-false}"
    if [ "$clipboard" = "true" ]; then
        if command -v pbcopy &>/dev/null; then
            echo -e "$output" | pbcopy
            echo -e "${GREEN}Copied to clipboard${NC}"
        elif command -v xclip &>/dev/null; then
            echo -e "$output" | xclip -selection clipboard
            echo -e "${GREEN}Copied to clipboard${NC}"
        else
            echo -e "$output"
        fi
    else
        echo -e "$output"
    fi
}
