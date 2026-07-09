"""Comprehensive build failure collector.

Collects everything needed to understand and fix CI build failures:
- Last failure per component (most recent)
- Comprehensive logs from ALL TaskRuns and steps
- Commit details (SHA, URL, message, author, timestamp)
- Failed task/step identification
- Error message extraction
- Pipeline configuration URL
- Build timing and duration
- Konflux UI links
"""

import time
from dataclasses import replace
from datetime import datetime

from kubernetes import client

from clients import KubeArchiveClient, KubernetesClient, TektonResultsClient, UnifiedPipelineClient
from clients.pipelinerun_query import query_pipelineruns
from logger import setup_logger
from models import Component, ScanResult
from openshift_auth import _ensure_k8s_config
from repositories import BuildFailureRepository, DatabaseConnection
from tekton_parsers import (
    classify_build_status,
    extract_error_from_logs,
    extract_failed_step_from_logs,
    extract_failed_step_from_pipelinerun,
    extract_pipelinerun_metadata,
    extract_pr_number_from_annotations,
)

logger = setup_logger(__name__)


class BuildFailureCollector:
    """Collects comprehensive failure data for troubleshooting and AI analysis."""

    def __init__(self, config, db=None, build_repo=None,
                 kubearchive=None, k8s=None, tekton_results=None, unified=None):
        self.config = config
        if db is None:
            db = DatabaseConnection(config.db)
        self.db = db
        self.build_repo = build_repo or BuildFailureRepository(db)
        self.kubearchive = kubearchive or KubeArchiveClient(
            api_url=config.k8s.kubearchive_api_url,
            namespace=config.k8s.namespace
        )
        self.k8s = k8s or KubernetesClient(namespace=config.k8s.namespace)
        self.tekton_results = tekton_results or TektonResultsClient(namespace=config.k8s.namespace)
        self.unified = unified or UnifiedPipelineClient(namespace=config.k8s.namespace)

    _PUSH_EVENT_TYPES = {'push', 'incoming'}

    def _collect_push_latest(self, pipelineruns):
        """Build a map of component -> (timestamp, status, reason, annotations)
        from the latest push PipelineRun per component."""
        push_latest = {}
        for pr in pipelineruns:
            labels = pr.get('metadata', {}).get('labels', {})
            comp = labels.get('appstudio.openshift.io/component')
            if not comp:
                continue
            event_type = labels.get('pipelinesascode.tekton.dev/event-type', '')
            if event_type not in self._PUSH_EVENT_TYPES:
                continue
            ts = pr.get('metadata', {}).get('creationTimestamp', '')
            conditions = pr.get('status', {}).get('conditions', [])
            if not conditions:
                continue
            c = conditions[-1]
            if comp not in push_latest or ts > push_latest[comp][0]:
                annotations = pr.get('metadata', {}).get('annotations', {})
                push_latest[comp] = (ts, c.get('status'), c.get('reason'), annotations)
        return push_latest

    def _enrich_component_metadata(self, component_name, push_latest):
        """Get repository URL and branch for a component from cluster or annotations."""
        repo_url = ''
        branch = ''

        try:
            _ensure_k8s_config()
            api = client.CustomObjectsApi()
            comp_data = api.get_namespaced_custom_object(
                group='appstudio.redhat.com', version='v1alpha1',
                namespace=self.config.k8s.namespace,
                plural='components', name=component_name,
                _request_timeout=10,
            )
            repo_url = comp_data.get('spec', {}).get('source', {}).get('git', {}).get('url', '')
            branch = comp_data.get('spec', {}).get('source', {}).get('git', {}).get('revision', '')
        except Exception:
            pass

        if not repo_url and component_name in push_latest:
            pr_annotations = push_latest[component_name][3]
            repo_url = (
                pr_annotations.get('pipelinesascode.tekton.dev/repo-url', '') or
                pr_annotations.get('pipelinesascode.tekton.dev/source-repo-url', '')
            )
            branch = (
                pr_annotations.get('build.appstudio.redhat.com/target_branch', '') or
                pr_annotations.get('pipelinesascode.tekton.dev/branch', '')
            )
            if repo_url:
                logger.info("%s: metadata from PipelineRun annotations (cluster unavailable)", component_name)

        return Component(
            name=component_name,
            repository_url=repo_url,
            branch=branch,
            namespace=self.config.k8s.namespace
        )

    def discover_components_from_cluster(self):
        """Discover components with failed latest push builds.

        Queries both KubeArchive/cluster and Tekton Results to get the most
        complete picture of failing components.
        """
        try:
            application = self.config.k8s.application_name

            # Source 1: KubeArchive + live cluster
            label_selector = (
                'appstudio.openshift.io/application={},'
                'pipelines.appstudio.openshift.io/type=build'
            ).format(application)

            logger.info("Querying for application %s...", application)
            pipelineruns = query_pipelineruns(
                self.config.k8s.namespace, label_selector,
                kubearchive_url=self.kubearchive.api_url,
                session=self.kubearchive.session,
            )
            logger.info("Found %d PipelineRuns", len(pipelineruns))

            push_latest = self._collect_push_latest(pipelineruns)

            # Source 2: Tekton Results (catches PipelineRuns that expired from KubeArchive)
            try:
                tr_pipelineruns = self.tekton_results.query_pipelinerun_records(
                    application=application, page_size=100
                )
                if tr_pipelineruns:
                    tr_push_latest = self._collect_push_latest(tr_pipelineruns)
                    added = 0
                    for comp, data in tr_push_latest.items():
                        if comp not in push_latest or data[0] > push_latest[comp][0]:
                            push_latest[comp] = data
                            added += 1
                    if added:
                        logger.info("Tekton Results added %d component(s) not in KubeArchive", added)
            except Exception as e:
                logger.debug("Tekton Results query skipped: %s", e)

            failing_component_names = [
                comp for comp, (ts, status, reason, _) in push_latest.items()
                if status == 'False'
            ]

            if not failing_component_names:
                logger.info("No failing components found")
                return []

            logger.info("Found %d components with failed builds", len(failing_component_names))

            failing_components = [
                self._enrich_component_metadata(name, push_latest)
                for name in failing_component_names
            ]

            logger.info("Retrieved metadata for %d components", len(failing_components))
            return failing_components

        except Exception as e:
            import traceback
            logger.error("Error discovering components: %s", e)
            traceback.print_exc()
            return []

    def get_component_metadata(self, component_name):
        """Fetch component metadata from Kubernetes."""
        meta = self.k8s.get_component_metadata(component_name)
        if not meta:
            return None
        return Component(
            name=component_name,
            repository_url=meta['repository_url'],
            branch=meta['branch'],
            namespace=self.config.k8s.namespace
        )

    def _extract_latest_failed(self, pipelineruns):
        """Find the latest failed push PipelineRun from a list."""
        latest_failed = None
        for pr in pipelineruns:
            labels = pr.get('metadata', {}).get('labels', {})
            if labels.get('pipelinesascode.tekton.dev/event-type', '') not in self._PUSH_EVENT_TYPES:
                continue
            conditions = pr.get('status', {}).get('conditions', [])
            if not conditions or conditions[-1].get('status') != 'False':
                continue
            ts = pr.get('metadata', {}).get('creationTimestamp', '')
            if not latest_failed or ts > latest_failed[0]:
                reason = conditions[-1].get('reason', '')
                latest_failed = (
                    ts,
                    pr.get('metadata', {}).get('name'),
                    pr.get('metadata', {}).get('uid'),
                    classify_build_status(reason),
                    reason,
                )

        if latest_failed:
            return {
                'name': latest_failed[1],
                'uid': latest_failed[2],
                'status': latest_failed[3],
                'started': latest_failed[0],
                'reason': latest_failed[4] if len(latest_failed) > 4 else None,
            }
        return None

    def get_last_failed_pipelinerun(self, component_name):
        """Get the most recent failed push PipelineRun for a component.

        Queries both KubeArchive/cluster and Tekton Results, returns the
        most recent failure across all sources.
        """
        label_selector = (
            'appstudio.openshift.io/component={},'
            'pipelines.appstudio.openshift.io/type=build'
        ).format(component_name)

        pipelineruns = query_pipelineruns(
            self.config.k8s.namespace, label_selector,
            kubearchive_url=self.kubearchive.api_url,
            session=self.kubearchive.session,
            max_pages=1, page_size=50,
        )

        ka_result = self._extract_latest_failed(pipelineruns)

        # Also check Tekton Results for a potentially newer failure
        tr_result = None
        try:
            tr_prs = self.tekton_results.query_pipelinerun_records(
                application=self.config.k8s.application_name,
                component=component_name,
                page_size=10
            )
            tr_result = self._extract_latest_failed(tr_prs)
        except Exception as e:
            logger.debug("Tekton Results query for %s failed: %s", component_name, e)

        if ka_result and tr_result:
            if tr_result['started'] > ka_result['started']:
                logger.info("Tekton Results has newer failure than KubeArchive")
                return tr_result
            return ka_result

        return ka_result or tr_result

    def extract_all_details(self, pr_data, source='kubearchive'):
        return extract_pipelinerun_metadata(
            pr_data, self.config.k8s.namespace, self.config.k8s.application_name
        )

    def extract_pr_number(self, annotations):
        return extract_pr_number_from_annotations(annotations)

    def extract_error_messages(self, logs):
        return extract_error_from_logs(logs)

    def extract_failed_step_from_logs(self, logs):
        return extract_failed_step_from_logs(logs)

    def _extract_taskrun_logs_section(self, logs, task_name):
        """Extract the log section for a specific TaskRun from PipelineRun logs.

        Args:
            logs: Complete PipelineRun logs
            task_name: Name of the TaskRun to extract (e.g., 'fips-check')

        Returns:
            Logs section for that TaskRun, or None if not found
        """
        if not logs or not task_name:
            return None

        # Look for markers like: ===== TaskRun: ...-fips-check-0 / Step: ... =====
        import re
        pattern = r'(===== TaskRun: [^/]*{task}[^/]* /.*?(?=\n=====|$))'.format(task=re.escape(task_name))

        matches = re.findall(pattern, logs, re.DOTALL)
        if matches:
            # Return all sections for this TaskRun concatenated
            return '\n'.join(matches)

        return None

    def _find_failed_taskrun(self, pr_name, pr_data):
        """Find the TaskRun that actually failed and extract its error and logs.

        Tries KubeArchive first, then falls back to Tekton Results.

        Returns:
            Tuple of (failed_task_name, error_from_taskrun, failed_task_logs)
        """
        if not pr_data:
            return None, None, None

        child_refs = pr_data.get('status', {}).get('childReferences', [])

        for ref in child_refs:
            if ref.get('kind') != 'TaskRun':
                continue

            task_name = ref.get('pipelineTaskName')
            tr_name = ref.get('name')

            if not tr_name:
                continue

            try:
                tr_data = self.kubearchive.get_taskrun(tr_name)
                if tr_data:
                    conditions = tr_data.get('status', {}).get('conditions', [])
                    if conditions and conditions[-1].get('status') == 'False':
                        logger.info("Found failed TaskRun: %s (task: %s)", tr_name, task_name)

                        condition_msg = conditions[-1].get('message', '')

                        pod_name = tr_data.get('status', {}).get('podName')
                        tr_logs = None
                        if pod_name:
                            tr_logs = self.kubearchive.get_pod_logs(pod_name, namespace=self.config.k8s.namespace)

                        error_msg = None
                        if tr_logs:
                            error_msg, _ = self.extract_error_messages(tr_logs)
                        if not error_msg and condition_msg:
                            error_msg = condition_msg

                        return task_name, error_msg, tr_logs
            except Exception as e:
                logger.debug("Could not fetch TaskRun %s: %s", tr_name, e)
                continue

        # Fallback 1: Tekton Results (has records KubeArchive may have lost)
        try:
            task_name, logs, _ = self.tekton_results.find_failed_taskrun(pr_name)
            if task_name:
                logger.info("Found failed TaskRun via Tekton Results (task: %s)", task_name)
                error_msg = None
                if logs:
                    error_msg, _ = self.extract_error_messages(logs)
                return task_name, error_msg, logs
        except Exception as e:
            logger.debug("Tekton Results fallback failed: %s", e)

        # Fallback 2: simple metadata-based approach
        return extract_failed_step_from_pipelinerun(pr_data), None, None

    def get_comprehensive_logs(self, pr_name):
        """Fetch comprehensive logs from all TaskRuns and steps.

        Tries KubeArchive/cluster first, then falls back to Tekton Results.
        """
        logs, source = self.unified.get_logs_complete(pr_name)

        if logs:
            logger.info("Got logs from %s (%d chars)", source, len(logs))
            return logs

        # Fallback: Tekton Results stores logs in cloud storage (S3)
        try:
            logs = self.tekton_results.get_pipelinerun_logs(pr_name)
            if logs:
                logger.info("Got logs from TektonResults (%d chars)", len(logs))
                return logs
        except Exception as e:
            logger.debug("Tekton Results logs fallback failed: %s", e)

        logger.error("No logs available from any source")
        return None

    def get_logs_from_active_pods(self, pr_name):
        """Fetch logs from active Kubernetes pods."""
        try:
            _ensure_k8s_config()
            v1 = client.CoreV1Api()
            pod_list = v1.list_namespaced_pod(
                self.config.k8s.namespace,
                label_selector='tekton.dev/pipelineRun={}'.format(pr_name),
                _request_timeout=10,
            )
            pods = [p.metadata.name for p in pod_list.items if p.metadata]
            if not pods:
                return None

            all_logs = []
            for pod_name in pods[:10]:
                try:
                    logs = v1.read_namespaced_pod_log(
                        pod_name, self.config.k8s.namespace,
                        tail_lines=5000,
                        _request_timeout=30,
                    )
                    if logs:
                        all_logs.append("===== Pod: {} =====\n{}".format(pod_name, logs))
                except Exception:
                    continue

            return "\n\n".join(all_logs) if all_logs else None

        except Exception:
            return None

    def collect_comprehensive_failure(self, component):
        """Collect comprehensive data for last failure.

        Returns:
            Tuple of (found_failure, inserted_new, log_length).
        """
        pr_info = self.get_last_failed_pipelinerun(component.name)
        if not pr_info:
            return False, False, 0

        pr_name = pr_info['name']
        logger.info("Latest failure: %s", pr_name)

        try:
            if self.build_repo.is_pipelinerun_complete(pr_name):
                logger.info("Already have complete data")
                return True, False, 0
        except Exception:
            pass

        logger.info("Fetching comprehensive logs...")
        logs = self.get_comprehensive_logs(pr_name)
        log_length = len(logs) if logs else 0

        logger.info("Fetching PipelineRun metadata...")
        pr_data, data_source = self.unified.get_pipelinerun_complete(pr_name)

        # Fallback: Tekton Results for PipelineRun metadata
        if not pr_data:
            try:
                tr_prs = self.tekton_results.query_pipelinerun_records(
                    application=self.config.k8s.application_name,
                    component=component.name,
                    page_size=5
                )
                for tr_pr in tr_prs:
                    if tr_pr.get('metadata', {}).get('name') == pr_name:
                        pr_data = tr_pr
                        data_source = 'TektonResults'
                        break
            except Exception as e:
                logger.debug("Tekton Results metadata fallback failed: %s", e)

        details = {}
        if pr_data:
            details = self.extract_all_details(pr_data, source=data_source)
            logger.info("Metadata from %s", data_source)
        else:
            logger.warning("Could not fetch metadata from any source, using partial data")

        author = details.get('commit_author', '')
        msg = details.get('commit_message', '')
        _nightly_authors = ('Openshift-AI DevOps', 'github-actions[bot]')
        _nightly_messages = ('Updating the operator repo', 'Updating the catalog.yaml')
        is_scheduled = '-on-schedule-' in pr_name
        if is_scheduled or (author in _nightly_authors and any(m in msg for m in _nightly_messages)):
            details['trigger_type'] = 'nightly'
        elif details.get('pr_number'):
            details['trigger_type'] = 'pull_request'

        # Calculate duration first (needed for timeout messages)
        duration = None
        if details.get('start_time') and details.get('completion_time'):
            try:
                start = datetime.fromisoformat(details['start_time'].replace('Z', '+00:00'))
                end = datetime.fromisoformat(details['completion_time'].replace('Z', '+00:00'))
                duration = int((end - start).total_seconds())
            except Exception:
                pass

        # Check PipelineRun failure reason before parsing logs
        failure_reason = pr_info.get('reason', '')

        if failure_reason in ('PipelineRunTimeout', 'TaskRunTimeout'):
            # Timeout: use timeout-specific message, don't parse logs
            error_message = f"Build timed out after {duration}s" if duration else "Build timed out"
            error_type = "Timeout"
            failed_step = None
        elif failure_reason == 'PipelineRunCancelled':
            # Cancelled: use cancellation message
            error_message = "Build was cancelled"
            error_type = "Cancelled"
            failed_step = None
        else:
            # Failed or other: find the TaskRun that failed
            failed_step, taskrun_error, failed_task_logs = self._find_failed_taskrun(pr_name, pr_data)

            # Append failed task's logs if they're missing from comprehensive logs
            if failed_step and failed_task_logs:
                task_section_header = "===== TaskRun:"
                task_in_logs = (
                    logs and failed_step in logs
                    and task_section_header in logs
                    and any(
                        failed_step in line
                        for line in logs.split('\n')
                        if line.startswith(task_section_header)
                    )
                )
                if not task_in_logs:
                    section = "\n\n===== TaskRun: {}-{} / Step: run-{} =====\n{}".format(
                        pr_name, failed_step, failed_step, failed_task_logs
                    )
                    logs = (logs or '') + section
                    log_length = len(logs)
                    logger.info("Appended %d chars of %s logs (missing from comprehensive logs)",
                                len(section), failed_step)

            # Extract error from the failed TaskRun's section in logs
            error_message = None
            error_type = None

            if failed_step and logs:
                task_logs = self._extract_taskrun_logs_section(logs, failed_step)
                if task_logs:
                    error_message, error_type = self.extract_error_messages(task_logs)

            # If no error found in task-specific logs, try all logs
            if not error_message and logs:
                error_message, error_type = self.extract_error_messages(logs)

            # Use the error from _find_failed_taskrun (includes condition messages)
            if not error_message and taskrun_error:
                error_message = taskrun_error
                error_type = "Task Failure"

            # If still no error, use a generic message
            if not error_message and failed_step:
                error_message = f"{failed_step} task failed - check Konflux UI for details"
                error_type = "Task Failure"

            # Fallback to log parsing for the step name if metadata didn't have it
            if not failed_step and logs:
                failed_step = self.extract_failed_step_from_logs(logs)

        inserted = False
        try:
            status_value = pr_info['status'].value
            inserted = self.build_repo.upsert_failure(
                pr_name=pr_name, pr_uid=pr_info['uid'],
                component_name=component.name,
                application=self.config.k8s.application_name,
                namespace=self.config.k8s.namespace,
                repo_url=component.repository_url,
                branch=component.branch,
                status=status_value,
                logs=logs, details=details,
                error_message=error_message, error_type=error_type,
                failed_step=failed_step, duration=duration
            )
            if inserted:
                logger.info("Inserted with %d chars of logs", log_length)
            else:
                logger.info("Updated with %d chars of logs", log_length)

        except Exception as e:
            logger.error("Database error: %s", e)
            return True, False, log_length

        self.build_repo.update_component_health(component.name)

        return True, inserted, log_length

    def check_resolution_via_history(self, component_name):
        """Check if a component's failure is resolved by querying build history.

        Queries Tekton Results for the component's recent builds. If the newest
        push build succeeded, the failure is resolved.

        Returns:
            Dict with resolution info if resolved, None if still failing.
        """
        try:
            history = self.tekton_results.query_component_build_history(
                application=self.config.k8s.application_name,
                component=component_name,
                page_size=10
            )
            if not history:
                return None

            newest = max(history,
                         key=lambda p: p.get('metadata', {}).get('creationTimestamp', ''))
            conditions = newest.get('status', {}).get('conditions', [])
            if not conditions:
                return None

            last_condition = conditions[-1]
            if last_condition.get('status') == 'True':
                pr_name = newest.get('metadata', {}).get('name', '')
                ts = newest.get('metadata', {}).get('creationTimestamp', '')
                annotations = newest.get('metadata', {}).get('annotations', {})
                commit_sha = (
                    annotations.get('build.appstudio.redhat.com/commit_sha') or
                    annotations.get('pipelinesascode.tekton.dev/sha')
                )
                return {
                    'resolved': True,
                    'resolution_pr_name': pr_name,
                    'resolution_timestamp': ts,
                    'resolution_type': 'auto-detected',
                    'resolution_commit_sha': commit_sha,
                }
        except Exception as e:
            logger.debug("Resolution check failed for %s: %s", component_name, e)

        return None

    def get_component_build_history(self, component_name, limit=10):
        """Get build history for a component from Tekton Results.

        Returns list of dicts with: name, timestamp, status, commit_sha, failed_task.
        """
        try:
            history = self.tekton_results.query_component_build_history(
                application=self.config.k8s.application_name,
                component=component_name,
                page_size=limit
            )
        except Exception as e:
            logger.debug("Build history query failed for %s: %s", component_name, e)
            return []

        results = []
        for pr in history:
            metadata = pr.get('metadata', {})
            conditions = pr.get('status', {}).get('conditions', [])
            if not conditions:
                continue

            last_condition = conditions[-1]
            status_flag = last_condition.get('status')
            reason = last_condition.get('reason', '')

            if status_flag == 'True':
                status = 'Succeeded'
            elif status_flag == 'False':
                status = classify_build_status(reason).value
            else:
                continue

            annotations = metadata.get('annotations', {})
            commit_sha = (
                annotations.get('build.appstudio.redhat.com/commit_sha') or
                annotations.get('pipelinesascode.tekton.dev/sha')
            )

            failed_task = None
            if status != 'Succeeded':
                for ref in pr.get('status', {}).get('childReferences', []):
                    if ref.get('kind') == 'TaskRun':
                        failed_task = ref.get('pipelineTaskName')

            # Extract output image from status.results
            pr_results = pr.get('status', {}).get('results', [])
            image_url = next(
                (r['value'] for r in pr_results if r.get('name') == 'IMAGE_URL'), None
            )

            results.append({
                'name': metadata.get('name', ''),
                'timestamp': metadata.get('creationTimestamp', ''),
                'status': status,
                'commit_sha': commit_sha[:8] if commit_sha else None,
                'failed_task': failed_task,
                'image_url': image_url,
            })

        return results

    def run(self, components=None, limit=None):
        """Run comprehensive collection scan."""
        start_time = time.time()

        if components is None:
            logger.info("Discovering failing components from cluster...")
            components = self.discover_components_from_cluster()

            if not components and self.config.components_file:
                logger.info("Falling back to components file: %s", self.config.components_file)
                components = Component.from_file(
                    str(self.config.components_file),
                    self.config.k8s.namespace
                )

            if not components:
                raise ValueError("No components discovered from cluster and no components file configured")

        if limit:
            components = components[:limit]

        logger.info("Enriching component metadata...")
        components = [
            comp if comp.repository_url else replace(
                comp,
                **vars(self.get_component_metadata(comp.name) or comp)
            )
            for comp in components
        ]

        scan_id = self.db.create_scan(scan_type='python-comprehensive', scan_mode='full')

        total_found = 0
        total_new = 0
        total_log_chars = 0

        logger.info("=" * 70)
        logger.info("Comprehensive CI Failure Collection")
        logger.info("=" * 70)

        for i, component in enumerate(components, 1):
            logger.info("[%d/%d] %s", i, len(components), component.name)

            if not component.repository_url:
                logger.error("Component not found in cluster")
                continue

            found, inserted, log_chars = self.collect_comprehensive_failure(component)
            if found:
                total_found += 1
                total_log_chars += log_chars
            if inserted:
                total_new += 1

        # Check for resolved failures via build history
        total_resolved = 0
        try:
            unresolved = self.build_repo.find_unresolved_component_names(
                self.config.k8s.application_name
            )
            currently_failing = {c.name for c in components}
            candidates = unresolved - currently_failing

            for comp_name in candidates:
                resolution = self.check_resolution_via_history(comp_name)
                if resolution:
                    pr_name = resolution['resolution_pr_name']
                    commit_sha = resolution.get('resolution_commit_sha')
                    self.build_repo.upsert_failure(
                        pr_name=pr_name, pr_uid='',
                        component_name=comp_name,
                        application=self.config.k8s.application_name,
                        namespace=self.config.k8s.namespace,
                        repo_url='', branch='', status='Succeeded',
                        details={'commit_sha': commit_sha,
                                 'commit_short_sha': commit_sha[:8] if commit_sha else ''},
                    )
                    if self.build_repo.mark_resolved(
                        comp_name, self.config.k8s.application_name,
                        self.config.k8s.namespace, pr_name,
                        resolution_commit_sha=commit_sha,
                    ):
                        total_resolved += 1
                        logger.info("Auto-resolved %s (successful build: %s)",
                                    comp_name, pr_name)
        except Exception as e:
            logger.debug("Resolution check phase failed: %s", e)

        if total_resolved:
            logger.info("Auto-resolved %d component(s) via build history", total_resolved)

        duration = time.time() - start_time
        result = ScanResult(
            scan_id=scan_id,
            components_scanned=len(components),
            failures_found=total_found,
            new_failures=total_new,
            logs_fetched=total_found,
            duration_seconds=duration
        )

        self.db.complete_scan(scan_id, result)

        return result
