"""Jira REST API client for creating tickets and reading comments.

Write operations: create_issue.
Read operations: get_comments, get_comment (for comment polling).
Uses Basic auth (email:token) and API v2.

Note: interactive reads (ticket detail, full describe) go via the MCP Jira
tools or the jira_api bash function in ic. This client is for cron-driven
polling where Python needs direct access.
"""

import requests

from logger import setup_logger

logger = setup_logger(__name__)

JIRA_API_VERSION = '2'


class JiraClient:
    """Jira REST API client.

    Creates issues in a configured Jira project. Uses Basic auth with
    email:API token pair (not Bearer — Jira Cloud requires this format).
    """

    def __init__(self, base_url, email, token, project):
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
        return '{}/rest/api/{}/{}'.format(self._base_url, JIRA_API_VERSION, path)

    def create_issue(self, summary, description_text, issue_type='Bug',
                     priority=None, labels=None, components=None):
        """Create a Jira issue and return the created issue dict (key, id, url).

        Args:
            summary: Issue title (one line).
            description_text: Plain text / wiki markup body (API v2 format).
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
            'description': description_text,
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

    def _get(self, path):
        """GET request returning parsed JSON, or None on error."""
        try:
            resp = self._session.get(self._api(path), timeout=30)
        except requests.RequestException as e:
            logger.warning("Jira GET %s failed: %s", path, str(e)[:100])
            return None
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            logger.warning("Jira GET %s: not found (404)", path)
        else:
            logger.warning("Jira GET %s: HTTP %d", path, resp.status_code)
        return None

    def add_comment(self, jira_key, body):
        """Add a comment to a Jira issue. Returns comment dict or None."""
        try:
            resp = self._session.post(
                self._api('issue/{}/comment'.format(jira_key)),
                json={'body': body},
                timeout=30,
            )
        except requests.RequestException as e:
            logger.error("Jira add comment failed: %s", str(e)[:100])
            return None
        if resp.status_code == 201:
            logger.info("Added comment to %s", jira_key)
            return resp.json()
        logger.error("Jira add comment %s: HTTP %d", jira_key, resp.status_code)
        return None

    def get_transitions(self, jira_key):
        """Get available transitions for a Jira issue."""
        data = self._get('issue/{}/transitions'.format(jira_key))
        if data is None:
            return []
        return data.get('transitions', [])

    def transition_issue(self, jira_key, transition_id):
        """Transition a Jira issue to a new status. Returns True on success."""
        try:
            resp = self._session.post(
                self._api('issue/{}/transitions'.format(jira_key)),
                json={'transition': {'id': str(transition_id)}},
                timeout=30,
            )
        except requests.RequestException as e:
            logger.error("Jira transition failed: %s", str(e)[:100])
            return False
        if resp.status_code == 204:
            logger.info("Transitioned %s (transition_id=%s)", jira_key, transition_id)
            return True
        logger.error("Jira transition %s: HTTP %d", jira_key, resp.status_code)
        return False

    def get_comments(self, jira_key):
        """Return all comments for a Jira issue, newest-last.

        Each dict contains: id, author (displayName, emailAddress), body, created.
        Returns empty list on error or missing issue.
        """
        data = self._get('issue/{}/comment'.format(jira_key))
        if data is None:
            return []
        return data.get('comments', [])

    def get_comment(self, jira_key, comment_id):
        """Return a single comment dict by ID, or None if not found."""
        return self._get('issue/{}/comment/{}'.format(jira_key, comment_id))

    def get_issue_status(self, jira_key):
        """Return the status category key for a Jira issue.

        Returns 'done', 'indeterminate', 'new', or None on error.
        """
        data = self._get('issue/{}?fields=status'.format(jira_key))
        if not data:
            return None
        try:
            return data['fields']['status']['statusCategory']['key']
        except (KeyError, TypeError):
            return None
