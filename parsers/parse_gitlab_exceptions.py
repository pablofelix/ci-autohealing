#!/usr/bin/env python3
"""Parse GitLab EC policy YAML and output non-expired exception rule names."""

import yaml
import sys
from datetime import datetime, timezone

docs = list(yaml.safe_load_all(sys.stdin))
now = datetime.now(timezone.utc)
rules = set()
for data in docs:
    if not data or not isinstance(data, dict):
        continue
    for src in data.get('spec', {}).get('sources', []):
        for e in src.get('volatileConfig', {}).get('exclude', []):
            eu = e.get('effectiveUntil')
            if eu:
                exp = datetime.strptime(str(eu)[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
                if exp < now:
                    continue
            val = e.get('value', '')
            if val:
                rules.add(val.split(':')[0])
for r in sorted(rules):
    print(r)
