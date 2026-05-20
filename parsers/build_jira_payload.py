#!/usr/bin/env python3
"""Build Jira API payload JSON for ticket creation.

Reads description from stdin. Required env vars:
  JIRA_PROJECT, JIRA_COMPONENT, JIRA_SUMMARY

Optional env vars:
  JIRA_PRIORITY (default: Blocker)
  JIRA_LABELS (comma-separated, default: conforma-violation)
"""

import json
import os
import sys

desc = sys.stdin.read()

# Parse optional env vars
priority = os.environ.get('JIRA_PRIORITY', 'Blocker')
labels_str = os.environ.get('JIRA_LABELS', 'conforma-violation')
labels = [l.strip() for l in labels_str.split(',') if l.strip()]

print(json.dumps({
    'fields': {
        'project': {'key': os.environ['JIRA_PROJECT']},
        'issuetype': {'name': 'Bug'},
        'summary': os.environ['JIRA_SUMMARY'],
        'description': desc,
        'labels': labels,
        'components': [{'name': os.environ['JIRA_COMPONENT']}],
        'priority': {'name': priority}
    }
}))
