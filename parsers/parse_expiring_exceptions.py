#!/usr/bin/env python3
"""Parse GitLab EC policy YAML and output expiring exception details with line links."""

import yaml
import sys
import re
from datetime import datetime, timezone

raw = sys.stdin.read()
lines = raw.split('\n')

file_map = []
cur_file = 'unknown.yaml'
cur_line = 0
for line in lines:
    m = re.match(r'^# __FILE__:(.+)$', line)
    if m:
        cur_file = m.group(1)
        cur_line = 0
        file_map.append((cur_file, 0))
    else:
        cur_line += 1
        file_map.append((cur_file, cur_line))


def find_line(fname, val, eu_str, img=''):
    in_file = False
    for i, line in enumerate(lines):
        if re.match(r'^# __FILE__:' + re.escape(fname) + r'$', line):
            in_file = True
            continue
        if line.startswith('# __FILE__:'):
            in_file = False
            continue
        if not in_file:
            continue
        if 'value:' in line and val[:40] in line:
            block = '\n'.join(lines[max(0, i-2):min(len(lines), i+6)])
            if eu_str not in block:
                continue
            if img and img[:40] not in block:
                continue
            return file_map[i][1] if i < len(file_map) else 0
    return 0


base = ('https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/'
        'config/CLUSTER_SHORT/product/EnterpriseContractPolicy')

docs = list(yaml.safe_load_all(raw))
now = datetime.now(timezone.utc)
seen = set()
results = []
for data in docs:
    if not data or not isinstance(data, dict):
        continue
    source = data.get('metadata', {}).get('name', 'unknown')
    fname = source + '.yaml'
    for src in data.get('spec', {}).get('sources', []):
        for e in src.get('volatileConfig', {}).get('exclude', []):
            eu = e.get('effectiveUntil')
            if not eu:
                continue
            val = e.get('value', '?')
            img = e.get('imageUrl', '')
            key = (str(eu), val, img)
            if key in seen:
                continue
            seen.add(key)
            exp = datetime.strptime(str(eu)[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
            days_left = (exp - now).days
            ln = find_line(fname, val, str(eu)[:10], img)
            link = f'{base}/{fname}#L{ln}' if ln > 0 else f'{base}/{fname}'
            results.append((days_left, exp.strftime('%Y-%m-%d'), val, img,
                            e.get('reference', ''), source, link))

results.sort(key=lambda x: x[0])
for r in results:
    print(f'{r[0]}|{r[1]}|{r[2]}|{r[3]}|{r[4]}|{r[5]}|{r[6]}')
