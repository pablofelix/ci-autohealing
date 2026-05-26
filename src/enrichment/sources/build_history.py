"""Build history source - compares failing build with last successful one.

Queries Tekton Results for the last successful build of the same component,
then uses GitHub to get the diff between the last success and current failure.
This shows ALL changes that happened between "it worked" and "it broke".
"""

import os

from enrichment.context_source import ContextSource
from logger import setup_logger

logger = setup_logger(__name__)

MAX_COMPARE_FILES = 20
MAX_PATCH_LENGTH = 2000


class BuildHistorySource(ContextSource):
    """Compares current failure with last successful build."""

    def __init__(self, config, github_client=None):
        super().__init__(config)
        self._github_client = github_client

    def source_name(self):
        return 'build_history'

    @property
    def requires_external_api(self):
        return True

    @property
    def timeout_seconds(self):
        return 60

    def fetch(self, failure):
        # type: (Dict[str, Any]) -> Optional[Dict[str, Any]]
        component = failure.get('component_name', '')
        application = failure.get('application', '') or self.config.k8s.application_name
        fail_sha = failure.get('commit_sha')
        repo_url = failure.get('repository_url', '')

        if not fail_sha or not repo_url:
            return None

        last_success = self._find_last_success(application, component, fail_sha)
        if not last_success:
            return None

        success_sha = last_success['commit_sha']
        logger.info("Last success: %s, current failure: %s", success_sha[:8], fail_sha[:8])

        comparison = self._compare_commits(repo_url, success_sha, fail_sha)
        if not comparison:
            return None

        return {
            'last_successful_build': {
                'commit_sha': success_sha,
                'pipelinerun': last_success.get('pipelinerun_name', ''),
                'completed_at': last_success.get('completed_at', ''),
            },
            'changes_since_success': {
                'total_commits': comparison.get('total_commits', 0),
                'files_changed': comparison.get('files_changed', []),
                'summary': comparison.get('summary', ''),
            },
        }

    def _find_last_success(self, application, component, fail_sha):
        # type: (str, str, str) -> Optional[Dict[str, Any]]
        try:
            from clients.tekton_results import TektonResultsClient
            ns = self.config.k8s.namespace
            tr = TektonResultsClient(namespace=ns)
            history = tr.query_component_build_history(application, component, page_size=10)
        except Exception as e:
            logger.warning("Cannot query build history: %s", e)
            return None

        for pr_data in history:
            conditions = pr_data.get('status', {}).get('conditions', [])
            if not conditions:
                continue
            last_cond = conditions[-1]
            if last_cond.get('status') == 'True' and last_cond.get('type') == 'Succeeded':
                annotations = pr_data.get('metadata', {}).get('annotations', {})
                sha = (annotations.get('build.appstudio.redhat.com/commit_sha')
                       or annotations.get('build.appstudio.openshift.io/commit_sha')
                       or annotations.get('pipelinesascode.tekton.dev/sha', ''))
                if sha and sha != fail_sha:
                    pr_name = pr_data.get('metadata', {}).get('name', '')
                    completed = last_cond.get('lastTransitionTime', '')
                    return {
                        'commit_sha': sha,
                        'pipelinerun_name': pr_name,
                        'completed_at': completed,
                    }

        logger.info("No previous successful build found for %s", component)
        return None

    def _compare_commits(self, repo_url, base_sha, head_sha):
        # type: (str, str, str) -> Optional[Dict[str, Any]]
        if self._github_client is None:
            token = getattr(self.config, 'github_token', None) or os.environ.get('GITHUB_TOKEN')
            if not token:
                logger.warning("No GitHub token for commit comparison")
                return None
            from clients.github_client import GitHubClient
            self._github_client = GitHubClient(token)

        try:
            owner, repo = self._parse_repo(repo_url)
            if not owner or not repo:
                return None

            base_short = base_sha[:8]
            head_short = head_sha[:8]
            logger.info("Comparing %s/%s: %s...%s", owner, repo, base_short, head_short)
            response = self._github_client._session.get(
                'https://api.github.com/repos/{}/{}/compare/{}...{}'.format(
                    owner, repo, base_short, head_short),
                timeout=30,
            )

            if response.status_code != 200:
                logger.warning("GitHub compare failed: %d", response.status_code)
                return None

            data = response.json()

            files_changed = []
            for f in data.get('files', [])[:MAX_COMPARE_FILES]:
                entry = {
                    'filename': f.get('filename', ''),
                    'status': f.get('status', ''),
                    'additions': f.get('additions', 0),
                    'deletions': f.get('deletions', 0),
                }
                patch = f.get('patch', '')
                if patch:
                    entry['patch'] = patch[:MAX_PATCH_LENGTH]
                files_changed.append(entry)

            total_commits = len(data.get('commits', []))
            commit_messages = [
                c.get('commit', {}).get('message', '').split('\n')[0]
                for c in data.get('commits', [])[:10]
            ]

            summary = '{} commits, {} files changed between last success ({}) and failure ({})'.format(
                total_commits, len(data.get('files', [])), base_sha[:8], head_sha[:8])

            return {
                'total_commits': total_commits,
                'commit_messages': commit_messages,
                'files_changed': files_changed,
                'summary': summary,
            }

        except Exception as e:
            logger.warning("Commit comparison failed: %s", e)
            return None

    def _parse_repo(self, repo_url):
        # type: (str) -> tuple
        url = repo_url.rstrip('/')
        if url.endswith('.git'):
            url = url[:-4]
        parts = url.split('/')
        if len(parts) >= 2:
            return parts[-2], parts[-1]
        return None, None
