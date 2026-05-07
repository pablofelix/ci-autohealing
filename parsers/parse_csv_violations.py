#!/usr/bin/env python3
"""Summarize conforma CSV violations by component."""

import csv
import sys
import collections

reader = csv.DictReader(sys.stdin)
counts = collections.Counter()
codes = collections.defaultdict(set)
for row in reader:
    if row['type'] == 'violation':
        counts[row['component_name']] += 1
        codes[row['component_name']].add(row['code'])
for comp in sorted(counts):
    code_list = ', '.join(sorted(codes[comp]))
    print(f'{comp}|{counts[comp]}|{code_list}')
print(f'TOTAL|{sum(counts.values())}|{len(counts)}')
