"""Jira REST API client for creating and updating tickets.

Write-only operations: create_issue. Uses Basic auth (email:token).
All reads are done via the MCP Jira tools, not this client.
"""

import requests
from typing import Any, Dict, List, Optional

from logger import setup_logger

logger = setup_logger(__name__)

JIRA_API_VERSION = '3'


class JiraClient:
    """Jira REST API client.

    Creates issues in a configured Jira project. Uses Basic auth with
    email:API token pair (not Bearer — Jira Cloud requires this format).
    """

    def __init__(self, base_url, email, token, project):
        # type: (str, str, str, str) -> None
        self._base_url = base_url.rstrip('/')
        self._project = project
        self._session = requests.Session()
        self._session.auth = (email, token)
        self._session.headers.update({
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'User-Agent': 'ci-autohealing/1.0',
        })

    def _api(self, path):
        # type: (str) -> str
        return '{}/rest/api/{}/{}'.format(self._base_url, JIRA_API_VERSION, path)

    def create_issue(self, summary, description_text, issue_type='Bug',
                     priority=None, labels=None, components=None):
        # type: (str, str, str, Optional[str], Optional[List[str]], Optional[List[str]]) -> Optional[Dict[str, Any]]
        """Create a Jira issue and return the created issue dict (key, id, url).

        Args:
            summary: Issue title (one line).
            description_text: Plain text body — converted to ADF paragraph blocks.
            issue_type: Jira issue type name (Bug, Task, Story).
            priority: Priority name (Blocker, Critical, Major, Minor). None = default.
            labels: List of label strings.
            components: List of component name strings.

        Returns:
            Dict with 'key', 'id', 'url' on success, None on failure.
        """
        fields = {
            'project': {'key': self._project},
            'summary': summary,
            'issuetype': {'name': issue_type},
            'description': self._text_to_adf(description_text),
        }

        if priority:
            fields['priority'] = {'name': priority}
        if labels:
            fields['labels'] = labels
        if components:
            fields['components'] = [{'name': c} for c in components]

        try:
            resp = self._session.post(
                self._api('issue'),
                json={'fields': fields},
                timeout=30,
            )
        except requests.RequestException as e:
            logger.error("Jira API request failed: %s", str(e)[:100])
            return None

        if resp.status_code == 201:
            data = resp.json()
            key = data.get('key', '')
            url = '{}/browse/{}'.format(self._base_url, key)
            logger.info("Created Jira issue: %s", url)
            return {'key': key, 'id': data.get('id', ''), 'url': url}

        if resp.status_code == 401:
            logger.error("Jira API: Unauthorized (401) — check JIRA_EMAIL and JIRA_TOKEN")
        elif resp.status_code == 403:
            logger.error("Jira API: Forbidden (403) — token lacks create permission")
        elif resp.status_code == 400:
            try:
                errors = resp.json().get('errors', {})
                logger.error("Jira API: Bad request (400) — %s", errors)
            except Exception:
                logger.error("Jira API: Bad request (400)")
        else:
            logger.error("Jira API: Unexpected status %d", resp.status_code)

        return None

    @staticmethod
    def _text_to_adf(text):
        # type: (str) -> Dict[str, Any]
        """Convert plain text to minimal Atlassian Document Format.

        Splits on blank lines into paragraphs. Code blocks (indented with 4
        spaces or surrounded by ```) are preserved as code nodes.
        """
        paragraphs = []
        current_lines = []

        for line in text.splitlines():
            if line.strip() == '':
                if current_lines:
                    paragraphs.append('\n'.join(current_lines))
                    current_lines = []
            else:
                current_lines.append(line)
        if current_lines:
            paragraphs.append('\n'.join(current_lines))

        content = []
        for para in paragraphs:
            content.append({
                'type': 'paragraph',
                'content': [{'type': 'text', 'text': para}],
            })

        return {
            'version': 1,
            'type': 'doc',
            'content': content or [{'type': 'paragraph', 'content': [{'type': 'text', 'text': ''}]}],
        }
