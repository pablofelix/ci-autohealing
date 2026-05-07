#!/usr/bin/env python3
"""Extract individual fields from Jira issue JSON.

Usage: parse_jira_fields.py <field> [<field> ...]
Fields: summary, status, assignee, updated, comments
"""

import json
import sys
from datetime import datetime

d = json.load(sys.stdin)
f = d['fields']

for field in sys.argv[1:]:
    if field == 'summary':
        print(f.get('summary', ''))
    elif field == 'status':
        print(f.get('status', {}).get('name', '?'))
    elif field == 'assignee':
        a = f.get('assignee')
        print(a['displayName'][:20] if a else 'Unassigned')
    elif field == 'updated':
        u = f.get('updated', '')
        if u:
            print(datetime.strptime(u[:10], '%Y-%m-%d').strftime('%d %b'))
        else:
            print('?')
    elif field == 'comments':
        print(f.get('comment', {}).get('total', 0))
