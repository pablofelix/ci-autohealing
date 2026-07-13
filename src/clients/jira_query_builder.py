"""Jira blocker query builder.

Builds JQL queries that match the team's actual Jira blocker queries.
Converts IC application names (rhoai-v3-5) to Jira version strings
(rhoai-3.5, 3.5 GA RHOAI RELEASE) and applies standard filters.
"""

import re


def app_to_jira_versions(application):
    """Convert IC application name to Jira version strings.

    rhoai-v3-5      -> ['rhoai-3.5', '3.5 GA RHOAI RELEASE']
    rhoai-v3-5-ea-2 -> ['rhoai-3.5.EA2', '3.5 EA2 RHOAI RELEASE']
    """
    if not application:
        return []
    match = re.match(r'rhoai-v(\d+)-(\d+)(?:-([a-z]+)-(\d+))?$', application)
    if not match:
        return []
    major, minor = match.group(1), match.group(2)
    milestone_type, milestone_num = match.group(3), match.group(4)
    version = '{}.{}'.format(major, minor)
    if milestone_type and milestone_num:
        milestone = '{}{}'.format(milestone_type.upper(), milestone_num)
        return [
            'rhoai-{}.{}'.format(version, milestone),
            '{} {} RHOAI RELEASE'.format(version, milestone),
        ]
    return [
        'rhoai-{}'.format(version),
        '{} GA RHOAI RELEASE'.format(version),
    ]
