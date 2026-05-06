"""GitHub REST API client for fetching commit context.

Fetches commit diffs, file contents, and PR info via HTTP.
No disk usage — all data returned in memory.
"""

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests

from logger import setup_logger

logger = setup_logger(__name__)

# Max diff size to store (avoid storing huge diffs)
MAX_DIFF_CHARS = 50000
MAX_FILE_CONTENT_CHARS = 30000


def parse_github_repo(repository_url):
    # type: (str) -> Optional[Tuple[str, str]]
    """Extract owner/repo from a GitHub URL.

    Handles:
        https://github.com/acme-org/model-registry
        https://github.com/acme-org/model-registry.git
    """
    if not repository_url:
        return None

    match = re.match(
        r'https?://github\.com/([^/]+)/([^/\s]+)',
        repository_url.rstrip('/')
    )
    if match:
        repo = match.group(2)
        # Remove .git suffix if present (don't use rstrip - it removes characters, not suffix)
        if repo.endswith('.git'):
            repo = repo[:-4]
        return match.group(1), repo
    return None


class GitHubClient:
    """Read-only GitHub REST API client.

    Fetches commit diffs, file contents, directory listings, and PR info.
    All operations are HTTP GETs — no cloning, no disk writes.
    """

    API_BASE = 'https://api.github.com'

    def __init__(self, token=None):
        # type: (Optional[str]) -> None
        self._session = requests.Session()
        self._session.headers.update({
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'ci-autohealing/1.0',
        })
        if token:
            self._session.headers['Authorization'] = 'token {}'.format(token)

    def _get(self, path, params=None, accept=None):
        # type: (str, Optional[Dict], Optional[str]) -> Optional[requests.Response]
        """Execute GET request with detailed error logging.

        Returns response on 200, None on any error (with appropriate logging).
        """
        url = '{}{}'.format(self.API_BASE, path)
        headers = {}
        if accept:
            headers['Accept'] = accept

        try:
            response = self._session.get(url, params=params, headers=headers, timeout=30)

            if response.status_code == 200:
                return response

            # Log different error types with appropriate severity
            if response.status_code == 404:
                # Resource not found - expected in some cases (commit deleted, private repo, etc)
                logger.debug("GitHub API %s: Not Found (404)", path)
                return None

            if response.status_code == 403:
                # Forbidden - likely permissions issue or rate limit
                try:
                    error_msg = response.json().get('message', '')
                    if 'rate limit' in error_msg.lower():
                        logger.error("GitHub API %s: Rate Limit Exceeded (403) - %s", path, error_msg)
                    else:
                        logger.error("GitHub API %s: Forbidden (403) - Token may lack permissions. Message: %s",
                                   path, error_msg or 'No details')
                except Exception:
                    logger.error("GitHub API %s: Forbidden (403) - Token may lack permissions", path)
                return None

            if response.status_code == 401:
                # Unauthorized - token is invalid or expired
                logger.error("GitHub API %s: Unauthorized (401) - Token is invalid or expired", path)
                return None

            if response.status_code == 429:
                # Too Many Requests - rate limited
                logger.error("GitHub API %s: Rate Limited (429) - Too many requests", path)
                return None

            # Other errors (500, 502, etc)
            logger.warning("GitHub API %s returned %d", path, response.status_code)
            return None

        except requests.Timeout:
            logger.warning("GitHub API %s: Request timeout (>30s)", path)
            return None
        except requests.RequestException as e:
            logger.warning("GitHub API %s: Request failed - %s", path, str(e)[:100])
            return None

    def get_commit(self, owner, repo, sha):
        # type: (str, str, str) -> Optional[Dict[str, Any]]
        """Fetch commit metadata and diff.

        Returns dict with: message, author, date, files changed, patch.
        Logs detailed error info if commit cannot be fetched.
        """
        resp = self._get('/repos/{}/{}/commits/{}'.format(owner, repo, sha))
        if not resp:
            # More context already logged in _get(), but add repo/sha for traceability
            logger.debug("Failed to fetch commit %s/%s@%s", owner, repo, sha[:8])
            return None

        data = resp.json()
        commit_info = data.get('commit', {})

        files = []
        total_patch_size = 0
        for f in data.get('files', []):
            patch = f.get('patch', '')
            # Truncate individual patches if very large
            if len(patch) > 10000:
                patch = patch[:10000] + '\n... (truncated)'

            total_patch_size += len(patch)
            if total_patch_size > MAX_DIFF_CHARS:
                files.append({
                    'filename': f['filename'],
                    'status': f.get('status', ''),
                    'additions': f.get('additions', 0),
                    'deletions': f.get('deletions', 0),
                    'patch': '(diff truncated — total diff too large)',
                })
                continue

            files.append({
                'filename': f['filename'],
                'status': f.get('status', ''),
                'additions': f.get('additions', 0),
                'deletions': f.get('deletions', 0),
                'patch': patch,
            })

        return {
            'sha': data.get('sha', ''),
            'message': commit_info.get('message', ''),
            'author': commit_info.get('author', {}).get('name', ''),
            'date': commit_info.get('author', {}).get('date', ''),
            'parents': [p['sha'] for p in data.get('parents', [])],
            'stats': data.get('stats', {}),
            'files': files,
        }

    def get_file_content(self, owner, repo, path, ref=None):
        # type: (str, str, str, Optional[str]) -> Optional[str]
        """Fetch a single file's content at a specific ref (branch/SHA).

        Returns the decoded text content, or None if not found.
        """
        params = {}
        if ref:
            params['ref'] = ref

        resp = self._get(
            '/repos/{}/{}/contents/{}'.format(owner, repo, quote(path, safe='/')),
            params=params,
        )
        if not resp:
            return None

        data = resp.json()

        # Directory listing, not a file
        if isinstance(data, list):
            return None

        # File too large for contents API — use blob API
        if data.get('size', 0) > 1000000:
            return '(file too large: {} bytes)'.format(data['size'])

        import base64
        content = data.get('content', '')
        encoding = data.get('encoding', '')

        if encoding == 'base64' and content:
            try:
                decoded = base64.b64decode(content).decode('utf-8', errors='replace')
                if len(decoded) > MAX_FILE_CONTENT_CHARS:
                    return decoded[:MAX_FILE_CONTENT_CHARS] + '\n... (truncated)'
                return decoded
            except Exception:
                return None

        return None

    def get_directory_listing(self, owner, repo, path, ref=None):
        # type: (str, str, str, Optional[str]) -> Optional[List[str]]
        """List files in a directory at a specific ref.

        Returns list of filenames, or None if directory not found.
        """
        params = {}
        if ref:
            params['ref'] = ref

        resp = self._get(
            '/repos/{}/{}/contents/{}'.format(owner, repo, quote(path, safe='/')),
            params=params,
        )
        if not resp:
            return None

        data = resp.json()
        if not isinstance(data, list):
            return None

        return [item['name'] for item in data]

    def get_pr_for_commit(self, owner, repo, sha):
        # type: (str, str, str) -> Optional[Dict[str, Any]]
        """Find the PR associated with a commit SHA.

        Returns dict with: number, title, body, url, labels.
        """
        resp = self._get(
            '/repos/{}/{}/commits/{}/pulls'.format(owner, repo, sha),
            accept='application/vnd.github.groot-preview+json',
        )
        if not resp:
            return None

        prs = resp.json()
        if not prs:
            return None

        pr = prs[0]
        return {
            'number': pr.get('number'),
            'title': pr.get('title', ''),
            'body': (pr.get('body') or '')[:5000],
            'url': pr.get('html_url', ''),
            'labels': [l.get('name', '') for l in pr.get('labels', [])],
            'state': pr.get('state', ''),
        }

    def get_commit_context(self, repository_url, commit_sha, branch=None):
        # type: (str, str, Optional[str]) -> Optional[Dict[str, Any]]
        """Fetch complete commit context for AI analysis.

        Combines: commit diff + Dockerfile + .tekton/ configs + PR info.
        Single entry point for the collector.
        """
        parsed = parse_github_repo(repository_url)
        if not parsed:
            logger.warning("Cannot parse GitHub URL: %s", repository_url)
            return None

        owner, repo = parsed
        ref = commit_sha

        logger.info("Fetching commit context: %s/%s@%s", owner, repo, ref[:8])

        context = {
            'owner': owner,
            'repo': repo,
            'commit': None,
            'dockerfile': None,
            'tekton_configs': {},
            'pr': None,
        }

        # 1. Commit diff
        commit = self.get_commit(owner, repo, ref)
        if commit:
            context['commit'] = commit
            logger.info("  Commit: %d files changed", len(commit.get('files', [])))
        else:
            logger.warning("  Commit not found: %s (check logs above for 403/401 errors)", ref)

        # 2. Dockerfile (try common locations)
        for dockerfile_path in ['Dockerfile', 'Containerfile']:
            content = self.get_file_content(owner, repo, dockerfile_path, ref=ref)
            if content:
                context['dockerfile'] = {
                    'path': dockerfile_path,
                    'content': content,
                }
                break

        # 3. .tekton/ pipeline configs
        tekton_files = self.get_directory_listing(owner, repo, '.tekton', ref=ref)
        if tekton_files:
            for fname in tekton_files:
                if fname.endswith(('.yaml', '.yml')):
                    content = self.get_file_content(
                        owner, repo,
                        '.tekton/{}'.format(fname),
                        ref=ref,
                    )
                    if content:
                        context['tekton_configs'][fname] = content

            logger.info("  Tekton configs: %d files", len(context['tekton_configs']))

        # 4. PR info (if commit is from a merge)
        pr = self.get_pr_for_commit(owner, repo, ref)
        if pr:
            context['pr'] = pr
            logger.info("  PR: #%s - %s", pr['number'], pr['title'][:60])

        # Summary of what was collected
        collected = []
        if context['commit']:
            collected.append('commit')
        if context['dockerfile']:
            collected.append('dockerfile')
        if context['tekton_configs']:
            collected.append('tekton')
        if context['pr']:
            collected.append('pr')

        if collected:
            logger.info("  Collected: %s", ', '.join(collected))
        else:
            logger.warning("  No context collected for %s/%s@%s (repo may be private or commit deleted)",
                         owner, repo, ref[:8])

        return context

    def check_rate_limit(self):
        # type: () -> Optional[Dict[str, int]]
        """Check remaining API rate limit."""
        resp = self._get('/rate_limit')
        if not resp:
            return None

        data = resp.json()
        core = data.get('resources', {}).get('core', {})
        return {
            'remaining': core.get('remaining', 0),
            'limit': core.get('limit', 0),
            'reset': core.get('reset', 0),
        }
