#!/usr/bin/env python3
"""Parse Jira issue JSON into key=value pairs for bash consumption.

Reads Jira API JSON from stdin, outputs structured fields.
"""

import json
import sys
from datetime import datetime


def fmt_date(s):
    if not s:
        return '?'
    return datetime.strptime(s[:10], '%Y-%m-%d').strftime('%d %b %Y')


def fmt_date_short(s):
    if not s:
        return '?'
    try:
        return datetime.strptime(s[:16], '%Y-%m-%dT%H:%M').strftime('%d %b %H:%M')
    except Exception:
        return datetime.strptime(s[:10], '%Y-%m-%d').strftime('%d %b')


d = json.load(sys.stdin)
f = d['fields']

summary = f.get('summary', '')
status = f.get('status', {}).get('name', '?')
priority = f.get('priority', {}).get('name', '?') if f.get('priority') else '?'
assignee = f.get('assignee', {}).get('displayName', 'Unassigned') if f.get('assignee') else 'Unassigned'
labels = ', '.join(f.get('labels', [])) or '-'
components = ', '.join(c['name'] for c in f.get('components', [])) or '-'
created = fmt_date(f.get('created', ''))
updated = fmt_date(f.get('updated', ''))
description = f.get('description', '') or ''

print(f'SUMMARY={summary}')
print(f'STATUS={status}')
print(f'PRIORITY={priority}')
print(f'ASSIGNEE={assignee}')
print(f'LABELS={labels}')
print(f'COMPONENTS={components}')
print(f'CREATED={created}')
print(f'UPDATED={updated}')

comments = f.get('comment', {}).get('comments', [])
print(f'COMMENT_COUNT={len(comments)}')

for c in comments:
    author = c.get('author', {}).get('displayName', '?')
    date = fmt_date_short(c.get('created', ''))
    body = c.get('body', '')
    print(f'COMMENT_AUTHOR={author}')
    print(f'COMMENT_DATE={date}')
    print(f'COMMENT_BODY={body}')
    print('COMMENT_END')

print(f'DESCRIPTION_BODY={description}')
