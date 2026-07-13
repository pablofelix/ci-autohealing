"""Jira blocker query builder.

Builds JQL queries that match the team's actual Jira blocker queries.
Converts IC application names (rhoai-v3-5) to Jira version strings
(rhoai-3.5, 3.5 GA RHOAI RELEASE) and applies standard filters.
"""

import os
import re


def app_to_jira_versions(application):
    """Convert IC application name to Jira version strings.

    rhoai-v3-5      -> ['rhoai-3.5', '3.5 GA RHOAI RELEASE']
    rhoai-v3-5-ea-2 -> ['rhoai-3.5.EA2', '3.5 EA2 RHOAI RELEASE']
    """
    if not application:
        return []
    match = re.match(r"rhoai-v(\d+)-(\d+)(?:-([a-z]+)-(\d+))?$", application)
    if not match:
        return []
    major, minor = match.group(1), match.group(2)
    milestone_type, milestone_num = match.group(3), match.group(4)
    version = "{}.{}".format(major, minor)
    if milestone_type and milestone_num:
        milestone = "{}{}".format(milestone_type.upper(), milestone_num)
        return [
            "rhoai-{}.{}".format(version, milestone),
            "{} {} RHOAI RELEASE".format(version, milestone),
        ]
    return [
        "rhoai-{}".format(version),
        "{} GA RHOAI RELEASE".format(version),
    ]


_DEFAULT_PROJECTS = ["RHAIENG", "RHOAIENG"]

_EXCLUDED_LABELS = [
    "RHOAI-releases",
    "RHOAI-internal",
    "devtestops-service",
    "test-failed",
    "test-skipped",
]

_GA_EXCLUDED_COMPONENTS = ["Documentation", "PXE"]


class JiraBlockerQuery:
    """Builds JQL that matches the team's actual Jira blocker queries."""

    def __init__(self, projects=None):
        env_projects = os.environ.get("JIRA_BLOCKER_PROJECTS", "")
        if env_projects:
            self._projects = [p.strip() for p in env_projects.split(",") if p.strip()]
        elif projects:
            self._projects = projects
        else:
            self._projects = list(_DEFAULT_PROJECTS)
        self._versions = []
        self._is_ga = True

    def for_application(self, application):
        self._versions = app_to_jira_versions(application)
        self._is_ga = "-ea-" not in (application or "") and "-rc-" not in (application or "")
        return self

    def build(self):
        parts = []

        projects_str = ", ".join(self._projects)
        parts.append("project in ({})".format(projects_str))

        parts.append("priority = Blocker")

        if self._versions:
            version_list = ", ".join("'{}'".format(v) for v in self._versions)
            parts.append(
                "(affectedVersion IN ({vs}) OR 'Target Version' IN ({vs}))".format(vs=version_list)
            )

        label_list = ", ".join(self._excluded_labels)
        parts.append("(labels not in ({}) OR labels IS EMPTY)".format(label_list))

        if self._is_ga:
            comp_list = ", ".join(self._ga_excluded_components)
            parts.append("(component not in ({}) OR component IS EMPTY)".format(comp_list))

        if self._is_ga:
            parts.append("status not in (Closed, Resolved)")
        else:
            parts.append("statusCategory != Done")

        parts.append("('Release Blocker' != Rejected OR 'Release Blocker' is EMPTY)")

        return " AND ".join(parts) + " ORDER BY updated DESC"

    @property
    def _excluded_labels(self):
        return _EXCLUDED_LABELS

    @property
    def _ga_excluded_components(self):
        return _GA_EXCLUDED_COMPONENTS


def categorize_blocker(summary):
    """Categorize a blocker by its summary text.

    Returns 'signoff' for Product Sign Off tickets, 'tfa' for test failure
    analysis, 'infra' for infrastructure issues, 'product' for everything else.
    """
    lower = summary.lower()
    if 'product sign off' in lower:
        return 'signoff'
    tfa_indicators = ['tfa', 'test-failure', 'test failure analysis',
                      'testfailure', 'automation']
    if any(ind in lower for ind in tfa_indicators):
        return 'tfa'
    if any(ind in lower for ind in ['infra', 'cluster', 'jenkins', 'ci ']):
        return 'infra'
    return 'product'
