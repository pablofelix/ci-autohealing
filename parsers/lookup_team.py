#!/usr/bin/env python3
"""Look up the Slack team handle for a Konflux component.

Reads the rhoai-component-data YAML from stdin.
Usage: lookup_team.py <component-name>

Prints the slack_team_handle for the component, or nothing if not found.
The component name is matched with and without the version suffix (e.g. -v3-4).
"""

import sys
import yaml

if len(sys.argv) < 2:
    sys.exit(1)

component = sys.argv[1]
data = yaml.safe_load(sys.stdin)
if not isinstance(data, dict):
    sys.exit(0)

for key in [component, component.rsplit('-v', 1)[0]]:
    entry = data.get(key)
    if isinstance(entry, dict):
        handle = entry.get('slack_team_handle', '')
        if handle:
            print(handle)
            sys.exit(0)

for name, entry in data.items():
    if not isinstance(entry, dict):
        continue
    if component.startswith(name) or name.startswith(component.rsplit('-v', 1)[0]):
        handle = entry.get('slack_team_handle', '')
        if handle:
            print(handle)
            sys.exit(0)
