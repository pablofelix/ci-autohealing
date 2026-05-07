#!/usr/bin/env python3
"""Parse conforma CSV violations for fetch_csv_violations (compact output)."""

import csv
import sys
import collections

reader = csv.DictReader(sys.stdin)
comps = collections.defaultdict(lambda: {'count': 0, 'codes': set()})
for row in reader:
    if row['type'] == 'violation':
        c = comps[row['component_name']]
        c['count'] += 1
        c['codes'].add(row['code'])
for comp in sorted(comps):
    d = comps[comp]
    codes = ','.join(sorted(d['codes']))
    print(f'{comp}|{d["count"]}|{codes}')
