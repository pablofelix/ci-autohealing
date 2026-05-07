#!/usr/bin/env python3
"""Compare conforma CSV violations against ic DB state.

Reads two inputs via environment variables:
  CSV_SUMMARY: pipe-delimited lines from parse_csv_violations.py (component|count|codes)
  DB_COUNTS:   pipe-delimited lines from SQL query (component_name|violations_count|scenario)
"""

import os
import sys

csv_summary = os.environ.get('CSV_SUMMARY', '')
db_raw = os.environ.get('DB_COUNTS', '')

csv_data = {}
for line in csv_summary.strip().split('\n'):
    if not line:
        continue
    parts = line.split('|')
    if parts[0] != 'TOTAL':
        csv_data[parts[0]] = int(parts[1])

db_data = {}
db_types = {}
if db_raw.strip():
    for line in db_raw.strip().split('\n'):
        parts = line.split('|')
        if len(parts) >= 3:
            comp = parts[0].strip()
            count = int(parts[1].strip())
            scenario = parts[2].strip()
            if not comp:
                continue
            key = (comp, scenario)
            db_data[key] = count
            s = scenario
            if 'single-component' in s:
                label = 'single'
            elif s.startswith('conforma-fbc'):
                label = 'fbc'
            elif s.startswith('conforma-custom'):
                label = 'custom'
            elif 'chart' in s:
                label = 'chart'
            else:
                label = '-'
            db_types[key] = label

all_keys = set()
for key in db_data:
    all_keys.add(key)
for comp in csv_data:
    found = False
    for key in db_data:
        if key[0] == comp:
            found = True
            break
    if not found:
        all_keys.add((comp, ''))

for key in sorted(all_keys):
    comp, scenario = key
    label = db_types.get(key, 'csv')
    csv_n = csv_data.get(comp, 0)
    db_n = db_data.get(key, 0)

    if label == 'single' or label == 'csv':
        delta = csv_n - db_n
    else:
        delta = 0
        csv_n = '-'

    if label == 'csv':
        status = 'NEW in CSV'
        delta_str = f'+{csv_n}'
    elif csv_n == '-':
        status = 'DB only'
        delta_str = '-'
    elif csv_n == 0 and db_n > 0:
        status = 'RESOLVED'
        delta_str = str(-db_n)
    elif delta == 0:
        status = 'Match'
        delta_str = '0'
    elif delta > 0:
        status = f'+{delta} in CSV'
        delta_str = f'+{delta}'
    else:
        status = f'{delta} in CSV'
        delta_str = str(delta)

    print(f'{comp}|{label}|{csv_n}|{db_n}|{delta_str}|{status}')
