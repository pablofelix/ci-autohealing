"""GitLab REST API client for fetching release configuration files.

Fetches RPA (ReleasePlanAdmission) mappings and EnterpriseContractPolicy
files from the konflux-release-data repository on GitLab.
"""

import base64
import os
from urllib.parse import quote

import requests

from logger import setup_logger

logger = setup_logger(__name__)

MAX_FILE_CONTENT_CHARS = 50000


class GitLabClient:
    """Read-only GitLab REST API client.

    Fetches file contents and directory listings from GitLab repositories.
    """

    DEFAULT_API_BASE = os.environ.get('GITLAB_API_BASE', '')

    def __init__(self, token=None, api_base=None):
        self._api_base = api_base or self.DEFAULT_API_BASE
        self._session = requests.Session()
        self._session.headers.update({
            'User-Agent': 'ci-autohealing/1.0',
        })
        token = token or os.environ.get('GITLAB_TOKEN')
        if token:
            self._session.headers['PRIVATE-TOKEN'] = token

    def _get(self, path, params=None):
        url = '{}{}'.format(self._api_base, path)
        try:
            response = self._session.get(url, params=params, timeout=30)
            if response.status_code == 200:
                return response
            if response.status_code == 404:
                logger.debug("GitLab API %s: Not Found (404)", path)
            elif response.status_code in (401, 403):
                logger.error("GitLab API %s: Auth error (%d)", path, response.status_code)
            else:
                logger.warning("GitLab API %s returned %d", path, response.status_code)
            return None
        except requests.Timeout:
            logger.warning("GitLab API %s: timeout", path)
            return None
        except requests.RequestException as e:
            logger.warning("GitLab API %s: %s", path, str(e)[:100])
            return None

    def _encode_project(self, project):
        return quote(project, safe='')

    def get_file_content(self, project, file_path, ref='main'):
        """Fetch a single file's decoded content from a GitLab repository.

        Args:
            project: Project path (e.g., 'releng/konflux-release-data')
            file_path: Path within the repo (e.g., 'config/cluster/product/rpa.yaml')
            ref: Branch or commit ref (default: 'main')

        Returns:
            Decoded file content as string, or None if not found
        """
        encoded_path = quote(file_path, safe='')
        resp = self._get(
            '/projects/{}/repository/files/{}'.format(
                self._encode_project(project), encoded_path
            ),
            params={'ref': ref},
        )
        if not resp:
            return None

        data = resp.json()
        content = data.get('content', '')
        encoding = data.get('encoding', '')

        if encoding == 'base64' and content:
            try:
                decoded = base64.b64decode(content).decode('utf-8', errors='replace')
                if len(decoded) > MAX_FILE_CONTENT_CHARS:
                    return decoded[:MAX_FILE_CONTENT_CHARS] + '\n... (truncated)'
                return decoded
            except Exception:
                logger.warning("Failed to decode file %s from %s", file_path, project)
                return None

        return content or None

    def list_directory(self, project, path, ref='main'):
        """List files in a directory within a GitLab repository.

        Args:
            project: Project path
            path: Directory path within the repo
            ref: Branch or commit ref

        Returns:
            List of dicts with 'name', 'type', 'path' keys, or None on error
        """
        resp = self._get(
            '/projects/{}/repository/tree'.format(self._encode_project(project)),
            params={'path': path, 'ref': ref, 'per_page': 100},
        )
        if not resp:
            return None

        return [
            {'name': item['name'], 'type': item['type'], 'path': item['path']}
            for item in resp.json()
        ]
