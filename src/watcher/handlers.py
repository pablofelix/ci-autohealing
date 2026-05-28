"""Event handlers for the K8s watch daemon.

Each handler processes one type of K8s watch event and takes the appropriate
action (upsert to DB, fetch logs, trigger analysis). Handlers are synchronous
— the daemon runs them in thread executors.
"""

import logging
from datetime import datetime

from tekton_parsers import (
    extract_error_from_logs,
    extract_pipelinerun_metadata,
)

logger = logging.getLogger(__name__)


class BuildEventHandler:
    """Processes PipelineRun events for build pipelines.

    On terminal failure: upserts to build_failures, fetches logs, optionally
    triggers AI analysis. On terminal success: records successful build and
    resolves any prior failures for the component.
    """

    _MAX_SEEN_UIDS = 5000

    def __init__(self, application, namespace, build_repo, pipeline_client=None,
                 analyzer=None, auto_analyze=True):
        self.application = application
        self.namespace = namespace
        self.build_repo = build_repo
        self.pipeline_client = pipeline_client
        self.analyzer = analyzer
        self.auto_analyze = auto_analyze
        self._seen_uids = set()

    def handle(self, event):
        event_type = event['type']
        pr_data = event['object']

        if event_type == 'DELETED':
            return

        if not self._is_terminal(pr_data):
            return

        metadata = pr_data.get('metadata', {})
        uid = metadata.get('uid', '')
        pr_name = metadata.get('name', '')

        if len(self._seen_uids) >= self._MAX_SEEN_UIDS:
            self._seen_uids.clear()
        if uid in self._seen_uids:
            return
        self._seen_uids.add(uid)

        labels = metadata.get('labels', {})
        component = labels.get('appstudio.openshift.io/component', '')
        if not component:
            return

        conditions = pr_data.get('status', {}).get('conditions', [])
        cond = conditions[-1] if conditions else {}
        cond_status = cond.get('status', '')

        if cond_status == 'True':
            self._handle_success(pr_data, pr_name, uid, component, labels)
        elif cond_status == 'False':
            self._handle_failure(pr_data, pr_name, uid, component, labels)

    def _handle_success(self, pr_data, pr_name, uid, component, labels):
        repo_url = ''
        branch = ''
        params = pr_data.get('spec', {}).get('params', [])
        for p in params:
            if p.get('name') == 'git-url':
                repo_url = p.get('value', '')
            elif p.get('name') == 'revision':
                branch = p.get('value', '')

        self.build_repo.record_successful_build(
            component, pr_name, uid, self.application,
            self.namespace, repo_url, branch,
        )
        self.build_repo.mark_resolved(
            component, self.application, self.namespace, pr_name,
        )
        logger.info("[%s] Build succeeded: %s (%s)", self.application, component, pr_name)

    def _handle_failure(self, pr_data, pr_name, uid, component, labels):
        details = extract_pipelinerun_metadata(pr_data, self.namespace, self.application)

        repo_url = details.get('repository_url', '')
        branch = details.get('branch', '')

        logs = None
        error_message = None
        error_type = None
        failed_step = details.get('failed_tasks', [None])[0] if details.get('failed_tasks') else None

        if self.pipeline_client:
            try:
                logs = self.pipeline_client.get_pipelinerun_logs(pr_name, self.namespace)
            except Exception as e:
                logger.warning("[%s] Failed to fetch logs for %s: %s",
                               self.application, pr_name, e)

        if logs:
            error_message, error_type = extract_error_from_logs(logs)

        start = pr_data.get('status', {}).get('startTime')
        end = pr_data.get('status', {}).get('completionTime')
        duration = None
        if start and end:
            try:
                t0 = datetime.fromisoformat(start.rstrip('Z'))
                t1 = datetime.fromisoformat(end.rstrip('Z'))
                duration = int((t1 - t0).total_seconds())
            except (ValueError, TypeError):
                pass

        is_new = self.build_repo.upsert_failure(
            pr_name=pr_name,
            pr_uid=uid,
            component_name=component,
            application=self.application,
            namespace=self.namespace,
            repo_url=repo_url,
            branch=branch,
            status='Failed',
            logs=logs,
            details=details,
            error_message=error_message,
            error_type=error_type,
            failed_step=failed_step,
            duration=duration,
        )

        if is_new:
            logger.info("[%s] New build failure: %s (%s)", self.application, component, pr_name)
        else:
            logger.info("[%s] Updated build failure: %s (%s)", self.application, component, pr_name)

        if is_new and self.auto_analyze and self.analyzer and logs:
            try:
                self.analyzer.analyze(component, self.application)
                logger.info("[%s] AI analysis triggered for %s", self.application, component)
            except Exception as e:
                logger.warning("[%s] AI analysis failed for %s: %s",
                               self.application, component, e)

    @staticmethod
    def _is_terminal(pr_data):
        conditions = pr_data.get('status', {}).get('conditions', [])
        if not conditions:
            return False
        c = conditions[-1]
        return c.get('status') in ('True', 'False')


class ComponentEventHandler:
    """Processes Component CR events (ADDED/MODIFIED/DELETED)."""

    def __init__(self, application, namespace, health_repo=None):
        self.application = application
        self.namespace = namespace
        self.health_repo = health_repo

    def handle(self, event):
        event_type = event['type']
        comp_data = event['object']
        name = comp_data.get('metadata', {}).get('name', '')

        if event_type == 'DELETED':
            logger.info("[%s] Component deleted: %s", self.application, name)
            return

        spec = comp_data.get('spec', {})
        git_source = spec.get('source', {}).get('git', {})
        repo_url = git_source.get('url', '')
        branch = git_source.get('revision', '')

        logger.info("[%s] Component %s: %s (repo=%s, branch=%s)",
                    self.application, event_type, name, repo_url, branch)
