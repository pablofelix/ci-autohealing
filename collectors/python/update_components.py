#!/usr/bin/env python3
"""
Sync components.txt with Konflux UI failing components.

Since we don't have direct Kubernetes access, this script helps you
manually sync the component list by showing what's in the UI vs what's
in components.txt.
"""

import sys
from pathlib import Path
from typing import Set

COMPONENTS_FILE = Path(__file__).parent / "components.txt"


def get_current_components() -> Set[str]:
    """Read current components from components.txt."""
    if not COMPONENTS_FILE.exists():
        return set()

    with open(COMPONENTS_FILE, 'r') as f:
        return {line.strip() for line in f if line.strip()}


def get_ui_components() -> Set[str]:
    """Get components from user input (paste from Konflux UI)."""
    print("=" * 70)
    print("Paste the failing component names from Konflux UI")
    print("(one per line, or space-separated)")
    print("Press Ctrl+D (or Ctrl+Z on Windows) when done")
    print("=" * 70)
    print()

    components = set()
    try:
        while True:
            line = input().strip()
            if line:
                # Handle both space-separated and newline-separated
                parts = line.split()
                for part in parts:
                    # Clean up component name (remove any trailing punctuation)
                    clean = part.strip().rstrip(',;.')
                    if clean:
                        components.add(clean)
    except EOFError:
        pass

    return components


def update_components_file(ui_components: Set[str], current: Set[str]) -> bool:
    """Update components.txt with components from UI."""
    new_components = ui_components - current
    removed_components = current - ui_components

    if not new_components and not removed_components:
        print("\n✓ No changes needed - components.txt is up to date")
        return False

    # Write to file
    all_components = sorted(ui_components)
    with open(COMPONENTS_FILE, 'w') as f:
        for component in all_components:
            f.write(f"{component}\n")

    # Report changes
    print()
    print("=" * 70)
    print("Components Updated")
    print("=" * 70)

    if new_components:
        print(f"\n✓ Added {len(new_components)} new component(s):")
        for comp in sorted(new_components):
            print(f"  + {comp}")

    if removed_components:
        print(f"\n✓ Removed {len(removed_components)} resolved component(s):")
        for comp in sorted(removed_components):
            print(f"  - {comp}")

    print(f"\nTotal components: {len(all_components)}")
    print(f"File: {COMPONENTS_FILE}")
    print()

    return True


def auto_mode(components_to_add: list):
    """Non-interactive mode - add components from command line."""
    current = get_current_components()
    ui_components = current | set(components_to_add)

    updated = update_components_file(ui_components, current)

    if updated:
        print("✓ components.txt has been updated")
        print("  Run ./cron/collect-comprehensive.sh to collect new failures")
    else:
        print("✓ No new components added")


def main():
    # Check for command-line arguments (non-interactive mode)
    if len(sys.argv) > 1:
        # Auto mode: add components from arguments
        components_to_add = sys.argv[1:]
        auto_mode(components_to_add)
        return

    # Interactive mode
    print("=" * 70)
    print("Component List Synchronization")
    print("=" * 70)
    print()

    # Show current components
    current = get_current_components()
    print(f"Current components in components.txt: {len(current)}")
    if current:
        print("\nCurrent components:")
        for comp in sorted(current):
            print(f"  • {comp}")
    print()

    # Get UI components
    ui_components = get_ui_components()

    if not ui_components:
        print("\n⚠ No components entered")
        sys.exit(1)

    print(f"\nComponents from UI: {len(ui_components)}")
    print()

    # Update file
    updated = update_components_file(ui_components, current)

    if updated:
        print("✓ components.txt has been updated")
        print("\nNext steps:")
        print("  1. Run: ./cron/collect-comprehensive.sh")
        print("  2. Check: ./ic get components")
    else:
        print("✓ components.txt is already synchronized")


if __name__ == '__main__':
    main()
