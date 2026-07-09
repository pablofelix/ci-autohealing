"""GitHub REST API client for fetching commit context.

Fetches commit diffs, file contents, and PR info via HTTP.
No disk usage — all data returned in memory.
"""

import base64
import re
from urllib.parse import quote

import requests

from logger import setup_logger

logger = setup_logger(__name__)

# Max diff size to store (avoid storing huge diffs)
MAX_DIFF_CHARS = 50000
MAX_FILE_CONTENT_CHARS = 30000


def parse_github_repo(repository_url):
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
        self._session = requests.Session()
        self._session.headers.update({
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'ci-autohealing/1.0',
        })
        if token:
            self._session.headers['Authorization'] = 'token {}'.format(token)

    def _get(self, path, params=None, accept=None):
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
            'labels': [lbl.get('name', '') for lbl in pr.get('labels', [])],
            'state': pr.get('state', ''),
        }

    def get_commit_context(self, repository_url, commit_sha, branch=None):
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

    # ------------------------------------------------------------------
    # Write methods (require repo write scope on the token)
    # ------------------------------------------------------------------

    def _post(self, path, payload):
        """POST with JSON payload. Returns response on 2xx, None on error."""
        url = '{}{}'.format(self.API_BASE, path)
        try:
            resp = self._session.post(url, json=payload, timeout=30)
        except requests.RequestException as e:
            logger.error("GitHub POST %s: %s", path, str(e)[:100])
            return None
        if resp.status_code in (200, 201, 422):
            return resp
        logger.error("GitHub POST %s: HTTP %d — %s", path, resp.status_code,
                     resp.text[:200])
        return None

    def _put(self, path, payload):
        """PUT with JSON payload. Returns response on 2xx, None on error."""
        url = '{}{}'.format(self.API_BASE, path)
        try:
            resp = self._session.put(url, json=payload, timeout=30)
        except requests.RequestException as e:
            logger.error("GitHub PUT %s: %s", path, str(e)[:100])
            return None
        if resp.status_code in (200, 201):
            return resp
        logger.error("GitHub PUT %s: HTTP %d — %s", path, resp.status_code,
                     resp.text[:200])
        return None

    def get_ref_sha(self, owner, repo, branch):
        """Return the commit SHA that a branch points to, or None if not found."""
        resp = self._get('/repos/{}/{}/git/ref/heads/{}'.format(owner, repo, branch))
        if not resp:
            return None
        return resp.json().get('object', {}).get('sha')

    def is_commit_on_branch(self, owner, repo, sha, branch):
        """Check if a commit SHA is reachable from a branch.

        Uses the compare endpoint: if base..head returns status 'behind' or
        'identical', the commit is on the branch. 'ahead' or 'diverged' means it isn't.
        """
        resp = self._get('/repos/{}/{}/compare/{}...{}'.format(
            owner, repo, sha, branch))
        if not resp:
            return None
        status = resp.json().get('status', '')
        return status in ('behind', 'identical')

    def get_file_sha(self, owner, repo, path, ref):
        """Return the blob SHA of a file (needed to update an existing file)."""
        resp = self._get('/repos/{}/{}/contents/{}'.format(owner, repo, quote(path, safe='')),
                         params={'ref': ref})
        if not resp:
            return None
        return resp.json().get('sha')

    def create_branch(self, owner, repo, branch_name, from_sha):
        """Create a new branch pointing to from_sha. Returns True on success."""
        path = '/repos/{}/{}/git/refs'.format(owner, repo)
        payload = {'ref': 'refs/heads/{}'.format(branch_name), 'sha': from_sha}
        resp = self._post(path, payload)
        if resp is None:
            return False
        if resp.status_code == 422:
            err = resp.json().get('message', '')
            if 'already exists' in err:
                logger.warning("Branch %s already exists in %s/%s", branch_name, owner, repo)
                return True
            logger.error("GitHub create_branch 422: %s", err)
            return False
        logger.info("Created branch %s in %s/%s", branch_name, owner, repo)
        return True

    def put_file(self, owner, repo, path, content, message, branch, existing_sha=None):
        """Create or update a file on branch. Returns True on success.

        existing_sha is required when updating (not creating) a file — GitHub
        uses it as a collision guard. Pass None when creating a new file.
        """
        encoded = base64.b64encode(content.encode('utf-8')).decode('ascii')
        payload = {
            'message': message,
            'content': encoded,
            'branch': branch,
        }
        if existing_sha:
            payload['sha'] = existing_sha

        api_path = '/repos/{}/{}/contents/{}'.format(owner, repo, quote(path, safe=''))
        resp = self._put(api_path, payload)
        if resp is None:
            return False
        logger.info("Updated %s on %s/%s@%s", path, owner, repo, branch)
        return True

    def create_pull_request(self, owner, repo, title, body, head, base='main'):
        """Create a PR and return {'url': ..., 'number': ...}, or None on failure.

        head: branch name that contains the changes (e.g. 'ci-autohealing/comp/42').
        base: target branch (default: 'main').
        """
        path = '/repos/{}/{}/pulls'.format(owner, repo)
        payload = {
            'title': title,
            'body': body,
            'head': head,
            'base': base,
            'draft': False,
        }
        resp = self._post(path, payload)
        if resp is None:
            return None
        data = resp.json()
        url = data.get('html_url', '')
        number = data.get('number')
        if not url:
            return None
        logger.info("Created PR #%s: %s", number, url)
        return {'url': url, 'number': number}

    def get_pull_request(self, owner, repo, pr_number):
        """Return PR metadata including merge status, or None if not found.

        Returned dict keys: number, state, merged, merged_at, merge_commit_sha, title.
        state is 'open' or 'closed'. merged is True only when the PR was merged
        (a closed-but-not-merged PR has merged=False).
        """
        resp = self._get('/repos/{}/{}/pulls/{}'.format(owner, repo, pr_number))
        if not resp:
            return None
        data = resp.json()
        return {
            'number': data.get('number'),
            'state': data.get('state'),
            'merged': data.get('merged', False),
            'merged_at': data.get('merged_at'),
            'merge_commit_sha': data.get('merge_commit_sha'),
            'title': data.get('title', ''),
            'head_sha': data.get('head', {}).get('sha'),
            'base_branch': data.get('base', {}).get('ref', ''),
        }

    def list_pull_requests(self, owner, repo, base=None, state='open', limit=10):
        """List PRs for a repo, optionally filtered by base branch."""
        params = {'state': state, 'per_page': min(limit, 100), 'sort': 'updated', 'direction': 'desc'}
        if base:
            params['base'] = base
        resp = self._get('/repos/{}/{}/pulls'.format(owner, repo), params=params)
        if not resp:
            return []
        return [
            {
                'number': pr.get('number'),
                'title': pr.get('title', ''),
                'state': pr.get('state', ''),
                'url': pr.get('html_url', ''),
                'author': pr.get('user', {}).get('login', ''),
                'base_branch': pr.get('base', {}).get('ref', ''),
                'updated_at': pr.get('updated_at', ''),
                'merged_at': pr.get('merged_at'),
                'merge_commit_sha': pr.get('merge_commit_sha'),
                'merged': pr.get('merged_at') is not None,
            }
            for pr in resp.json()[:limit]
        ]

    def compare_commits(self, owner, repo, base, head):
        """Get files changed between two commits using the GitHub compare API.

        Uses GET /repos/{owner}/{repo}/compare/{base}...{head}.
        More reliable than get_pr_for_commit() when no PR exists (e.g. direct
        pushes, cherry-picks, or cases where GitHub's commit→PR lookup times out).

        Args:
            owner: Repository owner.
            repo: Repository name.
            base: Base commit SHA (e.g. the failing commit).
            head: Head commit SHA (e.g. the fixing commit).

        Returns:
            List of changed filenames, or [] on any error.
        """
        resp = self._get('/repos/{}/{}/compare/{}...{}'.format(owner, repo, base, head))
        if not resp:
            return []
        try:
            return [
                f['filename'] for f in resp.json().get('files', [])[:200]
                if f.get('filename')
            ]
        except Exception:
            return []

    def check_rate_limit(self):
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

    def get_workflow_runs(self, owner, repo, workflow_file, limit=5):
        """Get recent workflow runs for a GitHub Actions workflow.

        Args:
            owner: Repository owner
            repo: Repository name
            workflow_file: Workflow filename (e.g. 'trigger-nightlies.yaml')
            limit: Max runs to return

        Returns:
            List of run dicts with: status, conclusion, created_at, html_url
        """
        path = '/repos/{}/{}/actions/workflows/{}/runs'.format(
            owner, repo, workflow_file)
        response = self._get(path, params={'per_page': limit})
        if not response:
            return []
        data = response.json()
        runs = []
        for run in data.get('workflow_runs', [])[:limit]:
            runs.append({
                'id': run.get('id'),
                'status': run.get('status'),
                'conclusion': run.get('conclusion'),
                'created_at': run.get('created_at'),
                'updated_at': run.get('updated_at'),
                'html_url': run.get('html_url'),
                'head_branch': run.get('head_branch'),
                'run_number': run.get('run_number'),
            })
        return runs
