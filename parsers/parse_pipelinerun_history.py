#!/usr/bin/env python3
"""Format PipelineRun history from KubeArchive JSON files.

Usage: parse_pipelinerun_history.py <limit> <build.json> <test.json>
"""

import json
import sys
from datetime import datetime

limit = int(sys.argv[1])
build_file = sys.argv[2]
test_file = sys.argv[3]

try:
    with open(build_file) as f:
        build_data = json.load(f)
except Exception:
    build_data = {'items': []}
try:
    with open(test_file) as f:
        test_data = json.load(f)
except Exception:
    test_data = {'items': []}

runs = []

for pr in build_data.get('items', []):
    meta = pr.get('metadata', {})
    labels = meta.get('labels', {})
    conditions = pr.get('status', {}).get('conditions', [])
    if not conditions:
        continue
    reason = conditions[-1].get('reason', '?')
    status_val = conditions[-1].get('status', '?')
    if status_val == 'True':
        result = 'Succeeded'
    elif status_val == 'False':
        result = 'Failed'
    else:
        result = 'Running'
    event = labels.get('pipelinesascode.tekton.dev/event-type', 'push')
    runs.append({
        'ts': meta.get('creationTimestamp', ''),
        'type': 'build/' + event,
        'result': result,
        'name': meta.get('name', '?'),
    })

for pr in test_data.get('items', []):
    meta = pr.get('metadata', {})
    labels = meta.get('labels', {})
    scenario = labels.get('test.appstudio.openshift.io/scenario', '')
    if not scenario.startswith('conforma'):
        continue
    conditions = pr.get('status', {}).get('conditions', [])
    if not conditions:
        continue
    status_val = conditions[-1].get('status', '?')
    if status_val == 'True':
        result = 'Succeeded'
    elif status_val == 'False':
        result = 'Failed'
    elif status_val == 'Unknown':
        result = 'Running'
    else:
        result = status_val
    short = scenario.replace('conforma-registry-acme-prod-v3-4-', '').replace('conforma-', '')
    runs.append({
        'ts': meta.get('creationTimestamp', ''),
        'type': 'test/' + short,
        'result': result,
        'name': meta.get('name', '?'),
    })

runs.sort(key=lambda r: r['ts'])
runs = runs[-limit:]

if not runs:
    print('No PipelineRuns found for this component')
    sys.exit(0)

GREEN = '\033[0;32m'
RED = '\033[0;31m'
YELLOW = '\033[1;33m'
NC = '\033[0m'

print(' %-20s  %-22s  %-10s  %s' % ('Timestamp (UTC)', 'Type', 'Result', 'PipelineRun'))
print(' %-20s  %-22s  %-10s  %s' % ('-' * 20, '-' * 22, '-' * 10, '-' * 30))
for r in runs:
    ts = r['ts'][:16].replace('T', ' ') if r['ts'] else '?'
    try:
        dt = datetime.strptime(ts, '%Y-%m-%d %H:%M')
        ts_fmt = dt.strftime('%d %b %H:%M')
    except Exception:
        ts_fmt = ts
    res = r['result']
    color = GREEN if res == 'Succeeded' else RED if res == 'Failed' else YELLOW
    print(' %-20s  %-22s  %s%-10s%s  %s' % (ts_fmt, r['type'], color, res, NC, r['name']))
