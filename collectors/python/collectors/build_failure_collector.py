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

import sys
import time
import subprocess
import json
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import replace
from datetime import datetime

from logger import setup_logger
from config import CollectorConfig
from repositories import DatabaseConnection, BuildFailureRepository
from clients import KubeArchiveClient, KubernetesClient, TektonResultsClient, UnifiedPipelineClient
from models import PipelineRun, BuildStatus, ScanResult, Component
from tekton_parsers import (
    extract_error_from_logs,
    extract_failed_step_from_logs,
    extract_pipelinerun_metadata,
    extract_pr_number_from_annotations,
    classify_pipelinerun_status,
)

logger = setup_logger(__name__)


class BuildFailureCollector:
    """Collects comprehensive failure data for troubleshooting and AI analysis."""

    def __init__(self, config):
        # type: (CollectorConfig) -> None
        self.config = config
        db = DatabaseConnection(config.db)
        self.db = db
        self.build_repo = BuildFailureRepository(db)

        self.kubearchive = KubeArchiveClient(
            api_url=config.k8s.kubearchive_api_url,
            namespace=config.k8s.namespace
        )
        self.k8s = KubernetesClient(
            namespace=config.k8s.namespace
        )
        self.tekton_results = TektonResultsClient(
            namespace=config.k8s.namespace
        )
        self.unified = UnifiedPipelineClient(
            namespace=config.k8s.namespace
        )

    def discover_components_from_cluster(self):
        # type: () -> List[Component]
        """Discover components with failed latest push builds.

        Queries KubeArchive for archived PipelineRuns and live cluster.
        """
        import requests

        try:
            push_latest = {}

            def process_pipelinerun(pr):
                labels = pr.get('metadata', {}).get('labels', {})
                comp = labels.get('appstudio.openshift.io/component')
                if not comp:
                    return
                event_type = labels.get('pipelinesascode.tekton.dev/event-type', '')
                if event_type != 'push':
                    return
                ts = pr.get('metadata', {}).get('creationTimestamp', '')
                conditions = pr.get('status', {}).get('conditions', [])
                if not conditions:
                    return
                c = conditions[-1]
                if comp not in push_latest or ts > push_latest[comp][0]:
                    annotations = pr.get('metadata', {}).get('annotations', {})
                    push_latest[comp] = (ts, c.get('status'), c.get('reason'), annotations)

            # Source 1: KubeArchive
            logger.info("Querying KubeArchive for application %s...", self.config.k8s.application_name)
            try:
                api_url = self.kubearchive.api_url
                token = self.kubearchive.token
                session = requests.Session()
                session.headers.update({
                    'Authorization': 'Bearer {}'.format(token),
                    'Accept': 'application/json'
                })

                url = "{}/apis/tekton.dev/v1/namespaces/{}/pipelineruns".format(
                    api_url, self.config.k8s.namespace
                )
                params = {
                    'labelSelector': 'appstudio.openshift.io/application={},pipelines.appstudio.openshift.io/type=build'.format(
                        self.config.k8s.application_name
                    ),
                    'limit': 500
                }

                total_archive = 0
                for _ in range(3):
                    resp = session.get(url, params=params, timeout=30)
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                    items = data.get('items', [])
                    total_archive += len(items)
                    for pr in items:
                        process_pipelinerun(pr)
                    cont = data.get('metadata', {}).get('continue')
                    if cont:
                        params['continue'] = cont
                    else:
                        break

                if total_archive:
                    logger.info("Found %d PipelineRuns in KubeArchive", total_archive)
            except Exception as e:
                logger.warning("KubeArchive query failed: %s", e)

            # Source 2: Live cluster
            try:
                pr_result = subprocess.run(
                    ['oc', 'get', 'pipelinerun',
                     '-n', self.config.k8s.namespace,
                     '-l', 'appstudio.openshift.io/application={},pipelines.appstudio.openshift.io/type=build'.format(
                         self.config.k8s.application_name
                     ),
                     '-o', 'json'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=15
                )

                if pr_result.returncode == 0:
                    data = json.loads(pr_result.stdout)
                    live_prs = data.get('items', [])
                    logger.info("Found %d PipelineRuns in live cluster", len(live_prs))
                    for pr in live_prs:
                        process_pipelinerun(pr)
            except Exception:
                pass

            failing_component_names = [
                comp for comp, (ts, status, reason, _) in push_latest.items()
                if status == 'False'
            ]

            if not failing_component_names:
                logger.info("No failing components found")
                return []

            logger.info("Found %d components with failed builds", len(failing_component_names))

            failing_components = []
            for component_name in failing_component_names:
                repo_url = ''
                branch = ''

                try:
                    comp_result = subprocess.run(
                        ['oc', 'get', 'component', component_name,
                         '-n', self.config.k8s.namespace,
                         '-o', 'json'],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        timeout=10
                    )

                    if comp_result.returncode == 0:
                        comp_data = json.loads(comp_result.stdout)
                        repo_url = comp_data.get('spec', {}).get('source', {}).get('git', {}).get('url', '')
                        branch = comp_data.get('spec', {}).get('source', {}).get('git', {}).get('revision', '')
                except Exception:
                    pass

                if not repo_url:
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

                failing_components.append(Component(
                    name=component_name,
                    repository_url=repo_url,
                    branch=branch,
                    namespace=self.config.k8s.namespace
                ))

            logger.info("Retrieved metadata for %d components", len(failing_components))
            return failing_components

        except Exception as e:
            import traceback
            logger.error("Error discovering components: %s", e)
            traceback.print_exc()
            return []

    def get_component_metadata(self, component_name):
        # type: (str) -> Optional[Component]
        """Fetch component metadata from Kubernetes."""
        try:
            result = subprocess.run(
                ['oc', 'get', 'component', component_name,
                 '-n', self.config.k8s.namespace, '-o', 'json'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=True,
                timeout=15
            )
            data = json.loads(result.stdout)

            repo_url = data.get('spec', {}).get('source', {}).get('git', {}).get('url', '')
            branch = data.get('spec', {}).get('source', {}).get('git', {}).get('revision', '')

            return Component(
                name=component_name,
                repository_url=repo_url,
                branch=branch,
                namespace=self.config.k8s.namespace
            )
        except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
            return None

    def get_last_failed_pipelinerun(self, component_name):
        # type: (str) -> Optional[Dict[str, Any]]
        """Get the most recent failed push PipelineRun for a component."""
        latest_failed = None

        def check_pr(pr):
            nonlocal latest_failed
            labels = pr.get('metadata', {}).get('labels', {})
            event_type = labels.get('pipelinesascode.tekton.dev/event-type', '')
            if event_type != 'push':
                return
            conditions = pr.get('status', {}).get('conditions', [])
            if conditions and conditions[-1].get('status') == 'False':
                ts = pr.get('metadata', {}).get('creationTimestamp', '')
                name = pr.get('metadata', {}).get('name')
                uid = pr.get('metadata', {}).get('uid')
                reason = conditions[-1].get('reason', '')
                status = classify_pipelinerun_status(reason)
                if status not in ('Cancelled', 'Timeout'):
                    status = BuildStatus.FAILED
                if not latest_failed or ts > latest_failed[0]:
                    latest_failed = (ts, name, uid, status)

        # Source 1: Live cluster
        try:
            result = subprocess.run(
                ['oc', 'get', 'pipelinerun',
                 '-n', self.config.k8s.namespace,
                 '-l', 'appstudio.openshift.io/component={},pipelines.appstudio.openshift.io/type=build'.format(
                     component_name
                 ),
                 '-o', 'json'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=15
            )

            if result.returncode == 0:
                data = json.loads(result.stdout)
                for pr in data.get('items', []):
                    check_pr(pr)
        except Exception:
            pass

        # Source 2: KubeArchive
        try:
            url = "{}/apis/tekton.dev/v1/namespaces/{}/pipelineruns".format(
                self.kubearchive.api_url, self.config.k8s.namespace
            )
            params = {
                'labelSelector': 'appstudio.openshift.io/component={},pipelines.appstudio.openshift.io/type=build'.format(
                    component_name
                ),
                'limit': 50
            }
            resp = self.kubearchive.session.get(url, params=params, timeout=30)

            if resp.status_code == 200:
                data = resp.json()
                for pr in data.get('items', []):
                    check_pr(pr)
        except Exception:
            pass

        if latest_failed:
            status = latest_failed[3] if len(latest_failed) > 3 else BuildStatus.FAILED
            return {
                'name': latest_failed[1],
                'uid': latest_failed[2],
                'status': status,
                'started': latest_failed[0]
            }

        return None

    def extract_all_details(self, pr_data, source='kubearchive'):
        # type: (Dict[str, Any], str) -> Dict[str, Any]
        return extract_pipelinerun_metadata(
            pr_data, self.config.k8s.namespace, self.config.k8s.application_name
        )

    def extract_pr_number(self, annotations):
        # type: (Dict[str, str]) -> Optional[int]
        return extract_pr_number_from_annotations(annotations)

    def extract_error_messages(self, logs):
        # type: (str) -> Tuple[Optional[str], Optional[str]]
        return extract_error_from_logs(logs)

    def extract_failed_step_from_logs(self, logs):
        # type: (str) -> Optional[str]
        return extract_failed_step_from_logs(logs)

    def get_comprehensive_logs(self, pr_name):
        # type: (str) -> Optional[str]
        """Fetch comprehensive logs from all TaskRuns and steps."""
        logs, source = self.unified.get_logs_complete(pr_name)

        if logs:
            logger.info("Got logs from %s (%d chars)", source, len(logs))
        else:
            logger.error("No logs available from any source")

        return logs

    def get_logs_from_active_pods(self, pr_name):
        # type: (str) -> Optional[str]
        """Fetch logs from active Kubernetes pods."""
        try:
            result = subprocess.run(
                ['oc', 'get', 'pod', '-n', self.config.k8s.namespace,
                 '-l', 'tekton.dev/pipelineRun={}'.format(pr_name),
                 '--no-headers', '-o', 'custom-columns=:metadata.name'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=True,
                timeout=10
            )

            pods = [p.strip() for p in result.stdout.strip().split('\n') if p.strip()]
            if not pods:
                return None

            all_logs = []
            for pod in pods[:10]:
                try:
                    logs_result = subprocess.run(
                        ['oc', 'logs', pod, '-n', self.config.k8s.namespace,
                         '--all-containers', '--tail=5000'],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        timeout=30
                    )
                    if logs_result.stdout:
                        all_logs.append("===== Pod: {} =====\n{}".format(pod, logs_result.stdout))
                except subprocess.TimeoutExpired:
                    continue

            return "\n\n".join(all_logs) if all_logs else None

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None

    def collect_comprehensive_failure(self, component):
        # type: (Component) -> Tuple[bool, bool, int]
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

        details = {}
        if pr_data:
            details = self.extract_all_details(pr_data, source=data_source)
            logger.info("Metadata from %s", data_source)
        else:
            logger.warning("Could not fetch metadata from any source, using partial data")

        error_message, error_type = self.extract_error_messages(logs) if logs else (None, None)
        failed_step = self.extract_failed_step_from_logs(logs) if logs else None

        duration = None
        if details.get('start_time') and details.get('completion_time'):
            try:
                start = datetime.fromisoformat(details['start_time'].replace('Z', '+00:00'))
                end = datetime.fromisoformat(details['completion_time'].replace('Z', '+00:00'))
                duration = int((end - start).total_seconds())
            except Exception:
                pass

        inserted = False
        try:
            status_value = pr_info['status'].value if isinstance(pr_info['status'], BuildStatus) else pr_info['status']
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

    def run(self, components=None, limit=None):
        # type: (Optional[List[Component]], Optional[int]) -> ScanResult
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
