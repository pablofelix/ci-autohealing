#!/usr/bin/env python3
"""Build Jira API payload JSON for ticket creation.

Reads description from stdin. Required env vars:
  JIRA_PROJECT, JIRA_COMPONENT, JIRA_SUMMARY
"""

import json
import os
import sys

desc = sys.stdin.read()
print(json.dumps({
    'fields': {
        'project': {'key': os.environ['JIRA_PROJECT']},
        'issuetype': {'name': 'Bug'},
        'summary': os.environ['JIRA_SUMMARY'],
        'description': desc,
        'labels': ['conforma-violation'],
        'components': [{'name': os.environ['JIRA_COMPONENT']}],
        'priority': {'name': 'Blocker'}
    }
}))
