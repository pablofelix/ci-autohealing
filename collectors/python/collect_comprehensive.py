#!/usr/bin/env python3
"""Comprehensive collector for CI failures - Optimized for problem solving.

Collects EVERYTHING needed to understand and fix failures:
- Last failure per component (most recent)
- Comprehensive logs from ALL TaskRuns and steps
- Commit details (SHA, URL, message, author, timestamp)
- Failed task/step identification
- Error message extraction
- Pipeline configuration URL
- Build timing and duration
- Konflux UI links

This collector is optimized for:
1. Human troubleshooting (easy access to all relevant info)
2. AI analysis (complete context for automated fixes)

Usage:
    python3 collect_comprehensive.py [--components-file FILE] [--limit N]
"""

import sys
import time
import subprocess
import json
import re
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import replace
from datetime import datetime

from config import CollectorConfig
from database import Database
from kubearchive_client import KubeArchiveClient
from kubernetes_client import KubernetesClient
from tekton_results_client import TektonResultsClient
from unified_collector import UnifiedCollector
from models import PipelineRun, BuildStatus, ScanResult, Component


class ComprehensiveCollector:
    """Collects comprehensive failure data for troubleshooting and AI analysis."""

    def __init__(self, config: CollectorConfig):
        """Initialize collector.

        Args:
            config: Collector configuration.
        """
        self.config = config
        self.db = Database(config.db)

        # Initialize all available API clients
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

        # Unified collector that tries all APIs
        self.unified = UnifiedCollector(
            namespace=config.k8s.namespace
        )

    def discover_components_from_cluster(self) -> List[Component]:
        """Discover components dynamically from KubeArchive + live cluster.

        Queries KubeArchive for archived PipelineRuns (live cluster only retains
        a few recent ones) and finds components with failed latest builds.

        Returns:
            List of Component objects with failing builds.
        """
        import requests

        try:
            push_latest = {}      # component -> (timestamp, status, reason, annotations)

            def process_pipelinerun(pr):
                """Track latest push build per component."""
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

            # Source 1: KubeArchive (has the full PipelineRun history)
            print(f"  → Querying KubeArchive for application {self.config.k8s.application_name}...")
            try:
                api_url = self.kubearchive.api_url
                token = self.kubearchive.token
                session = requests.Session()
                session.headers.update({
                    'Authorization': f'Bearer {token}',
                    'Accept': 'application/json'
                })

                url = f"{api_url}/apis/tekton.dev/v1/namespaces/{self.config.k8s.namespace}/pipelineruns"
                params = {
                    'labelSelector': f'appstudio.openshift.io/application={self.config.k8s.application_name},pipelines.appstudio.openshift.io/type=build',
                    'limit': 500
                }

                # Paginate through results (max 3 pages = 1500 PipelineRuns)
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
                    print(f"  ✓ Found {total_archive} PipelineRuns in KubeArchive")
            except Exception as e:
                print(f"  ⚠ KubeArchive query failed: {e}")

            # Source 2: Live cluster (may have very recent PipelineRuns)
            try:
                pr_result = subprocess.run(
                    ['oc', 'get', 'pipelinerun',
                     '-n', self.config.k8s.namespace,
                     '-l', f'appstudio.openshift.io/application={self.config.k8s.application_name},pipelines.appstudio.openshift.io/type=build',
                     '-o', 'json'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=15
                )

                if pr_result.returncode == 0:
                    data = json.loads(pr_result.stdout)
                    live_prs = data.get('items', [])
                    print(f"  ✓ Found {len(live_prs)} PipelineRuns in live cluster")
                    for pr in live_prs:
                        process_pipelinerun(pr)
            except Exception:
                pass

            # Find components whose latest push build failed
            failing_component_names = [
                comp for comp, (ts, status, reason, _) in push_latest.items()
                if status == 'False'
            ]

            if not failing_component_names:
                print(f"  ✓ No failing components found")
                return []

            print(f"  ✓ Found {len(failing_component_names)} components with failed builds")

            # Get metadata for failing components
            # Try oc get component first, fall back to PipelineRun annotations
            failing_components = []
            for component_name in failing_component_names:
                repo_url = ''
                branch = ''

                # Try getting metadata from cluster
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

                # Fall back to PipelineRun annotations if cluster metadata unavailable
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
                        print(f"  → {component_name}: metadata from PipelineRun annotations (cluster unavailable)")

                failing_components.append(Component(
                    name=component_name,
                    repository_url=repo_url,
                    branch=branch,
                    namespace=self.config.k8s.namespace
                ))

            print(f"  ✓ Retrieved metadata for {len(failing_components)} components")
            return failing_components

        except Exception as e:
            import traceback
            print(f"  ✗ Error discovering components: {e}")
            traceback.print_exc()
            return []

    def get_component_metadata(self, component_name: str) -> Optional[Component]:
        """Fetch component metadata from Kubernetes.

        Args:
            component_name: Component name.

        Returns:
            Component with repository details or None if not found.
        """
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

    def get_last_failed_pipelinerun(self, component_name: str) -> Optional[Dict[str, Any]]:
        """Get the MOST RECENT failed push PipelineRun for a component.

        Checks both live cluster and KubeArchive for archived PipelineRuns.
        Only considers push builds (not incoming re-triggers or pull_request).

        Args:
            component_name: Component name.

        Returns:
            PipelineRun dictionary with name, uid, status, start_time or None.
        """
        latest_failed = None  # (timestamp, name, uid)

        def check_pr(pr):
            """Check if PR is a failed push build and track if latest."""
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
                status = BuildStatus.FAILED
                if reason == 'PipelineRunCancelled':
                    status = 'Cancelled'
                elif reason in ('PipelineRunTimeout', 'TaskRunTimeout'):
                    status = 'Timeout'
                if not latest_failed or ts > latest_failed[0]:
                    latest_failed = (ts, name, uid, status)

        # Source 1: Live cluster
        try:
            result = subprocess.run(
                ['oc', 'get', 'pipelinerun',
                 '-n', self.config.k8s.namespace,
                 '-l', f'appstudio.openshift.io/component={component_name},pipelines.appstudio.openshift.io/type=build',
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

        # Source 2: KubeArchive (archived PipelineRuns)
        try:
            import requests
            url = f"{self.kubearchive.api_url}/apis/tekton.dev/v1/namespaces/{self.config.k8s.namespace}/pipelineruns"
            params = {
                'labelSelector': f'appstudio.openshift.io/component={component_name},pipelines.appstudio.openshift.io/type=build',
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

    def extract_all_details(self, pr_data: Dict[str, Any], source: str = 'kubearchive') -> Dict[str, Any]:
        """Extract ALL useful details from PipelineRun.

        Args:
            pr_data: PipelineRun JSON from KubeArchive or Kubernetes API.
            source: Source of data ('kubearchive' or 'kubernetes').

        Returns:
            Dictionary with all extracted details.
        """
        if not pr_data:
            return {}

        annotations = pr_data.get('metadata', {}).get('annotations', {})
        params = pr_data.get('spec', {}).get('params', [])
        status = pr_data.get('status', {})

        # Extract commit details
        commit_sha = (
            annotations.get('build.appstudio.redhat.com/commit_sha') or
            annotations.get('pipelinesascode.tekton.dev/sha') or
            next((p['value'] for p in params if p.get('name') == 'revision'), None)
        )

        commit_url = annotations.get('pipelinesascode.tekton.dev/sha-url')
        if not commit_url and commit_sha:
            repo_url = next((p['value'] for p in params if p.get('name') == 'git-url'), None)
            if repo_url:
                commit_url = f"{repo_url.rstrip('.git')}/commit/{commit_sha}"

        commit_message = annotations.get('pipelinesascode.tekton.dev/sha-title', '')
        commit_author = annotations.get('pipelinesascode.tekton.dev/sender', '')

        # Extract Konflux UI URL
        konflux_url = annotations.get('pipelinesascode.tekton.dev/log-url')
        if not konflux_url:
            pr_name = pr_data.get('metadata', {}).get('name')
            namespace = pr_data.get('metadata', {}).get('namespace', self.config.k8s.namespace)
            app_name = self.config.k8s.application_name
            if pr_name:
                konflux_url = (
                    f"https://konflux-ui.apps.CLUSTER_DOMAIN"
                    f"/ns/{namespace}/applications/{app_name}/pipelineruns/{pr_name}/logs"
                )

        # Extract pipeline configuration URL
        pipeline_url = (
            annotations.get('build.appstudio.openshift.io/pipeline-url') or
            annotations.get('pipelinesascode.tekton.dev/repo-url', '')
        )

        # Extract repository details
        repo_url = next((p['value'] for p in params if p.get('name') == 'git-url'), '')
        branch = annotations.get('pipelinesascode.tekton.dev/branch', '')

        # Extract build times
        start_time = status.get('startTime')
        completion_time = status.get('completionTime')

        # Extract failed task information
        failed_tasks = []
        child_refs = status.get('childReferences', [])
        for ref in child_refs:
            if ref.get('kind') == 'TaskRun':
                task_status = ref.get('pipelineTaskName')
                if task_status:
                    failed_tasks.append(task_status)

        return {
            'commit_sha': commit_sha,
            'commit_short_sha': commit_sha[:8] if commit_sha else None,
            'commit_url': commit_url,
            'commit_message': commit_message,
            'commit_author': commit_author,
            'konflux_url': konflux_url,
            'pipeline_url': pipeline_url,
            'repository_url': repo_url,
            'branch': branch,
            'start_time': start_time,
            'completion_time': completion_time,
            'failed_tasks': failed_tasks,
            'pr_number': self.extract_pr_number(annotations),
            'pr_url': annotations.get('pipelinesascode.tekton.dev/pull-request-url')
        }

    def extract_pr_number(self, annotations: Dict[str, str]) -> Optional[int]:
        """Extract PR number from annotations if this is a PR build.

        Args:
            annotations: PipelineRun annotations.

        Returns:
            PR number or None.
        """
        pr_url = annotations.get('pipelinesascode.tekton.dev/pull-request-url', '')
        if pr_url:
            match = re.search(r'/pull/(\d+)', pr_url)
            if match:
                return int(match.group(1))
        return None

    def extract_error_messages(self, logs: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract error messages and type from logs.

        Args:
            logs: Build logs text.

        Returns:
            Tuple of (error_message, error_type).
        """
        if not logs:
            return None, None

        # Common error patterns
        error_patterns = [
            (r'ERROR:\s*(.{0,500})', 'Build Error'),
            (r'FAIL(?:ED)?:\s*(.{0,500})', 'Test Failure'),
            (r'(?:exit|return) code (\d+)', 'Exit Code'),
            (r'(timeout|timed out)', 'Timeout'),
            (r'(resource not found|404)', 'Resource Not Found'),
            (r'(?:fatal|FATAL):\s*(.{0,500})', 'Fatal Error'),
        ]

        for pattern, error_type in error_patterns:
            match = re.search(pattern, logs, re.IGNORECASE | re.MULTILINE)
            if match:
                return match.group(0)[:500], error_type

        return None, None

    def extract_failed_step_from_logs(self, logs: str) -> Optional[str]:
        """Extract failed step name from logs.

        Args:
            logs: Build logs.

        Returns:
            Failed step name or None.
        """
        if not logs:
            return None

        # Look for TaskRun/Step markers
        match = re.search(r'===== TaskRun: ([^/]+) / Step: ([^=]+) =====', logs)
        if match:
            return match.group(2).strip()

        return None

    def get_comprehensive_logs(self, pr_name: str) -> Optional[str]:
        """Fetch comprehensive logs from ALL TaskRuns and steps.

        Uses UnifiedCollector which tries multiple Python API methods:
        1. Kubernetes API (most current data)
        2. KubeArchive API (archived data)
        3. Active pods via oc (last resort)

        Args:
            pr_name: PipelineRun name.

        Returns:
            Combined logs or None.
        """
        logs, source = self.unified.get_logs_complete(pr_name)

        if logs:
            print(f"    ✓ Got logs from {source} ({len(logs)} chars)")
        else:
            print(f"    ✗ No logs available from any source")

        return logs

    def get_logs_from_active_pods(self, pr_name: str) -> Optional[str]:
        """Fetch logs from active Kubernetes pods.

        Args:
            pr_name: PipelineRun name.

        Returns:
            Combined logs from all pods or None.
        """
        try:
            result = subprocess.run(
                ['oc', 'get', 'pod', '-n', self.config.k8s.namespace,
                 f'-l', f'tekton.dev/pipelineRun={pr_name}',
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

            # Collect logs from all pods
            all_logs = []
            for pod in pods[:10]:  # Up to 10 pods
                try:
                    logs_result = subprocess.run(
                        ['oc', 'logs', pod, '-n', self.config.k8s.namespace,
                         '--all-containers', '--tail=5000'],  # Last 5000 lines per container
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        timeout=30
                    )
                    if logs_result.stdout:
                        all_logs.append(f"===== Pod: {pod} =====\n{logs_result.stdout}")
                except subprocess.TimeoutExpired:
                    continue

            return "\n\n".join(all_logs) if all_logs else None

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None

    def collect_comprehensive_failure(self, component: Component) -> Tuple[bool, bool, int]:
        """Collect comprehensive data for last failure.

        Args:
            component: Component to scan.

        Returns:
            Tuple of (found_failure, inserted_new, log_length).
        """
        # Get last failed PipelineRun
        pr_info = self.get_last_failed_pipelinerun(component.name)
        if not pr_info:
            return False, False, 0

        pr_name = pr_info['name']
        print(f"  Latest failure: {pr_name}")

        # Check if already in DB with complete data
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT build_logs IS NOT NULL AND LENGTH(build_logs) > 100,
                           commit_sha IS NOT NULL,
                           konflux_url IS NOT NULL
                    FROM build_failures
                    WHERE pipelinerun_name = %s
                    """,
                    (pr_name,)
                )
                result = cursor.fetchone()
                if result and all(result):
                    print(f"  ✓ Already have complete data")
                    return True, False, 0
        except Exception:
            pass

        # Fetch comprehensive logs
        print(f"  → Fetching comprehensive logs...")
        logs = self.get_comprehensive_logs(pr_name)
        log_length = len(logs) if logs else 0

        # Get full PipelineRun details using UnifiedCollector
        print(f"  → Fetching PipelineRun metadata...")

        pr_data, data_source = self.unified.get_pipelinerun_complete(pr_name)

        details = {}
        if pr_data:
            details = self.extract_all_details(pr_data, source=data_source)
            print(f"  ✓ Metadata from {data_source}")
        else:
            print(f"  ⚠ Could not fetch metadata from any source, using partial data")

        # Extract error information from logs
        error_message, error_type = self.extract_error_messages(logs) if logs else (None, None)
        failed_step = self.extract_failed_step_from_logs(logs) if logs else None

        # Calculate duration
        duration = None
        if details.get('start_time') and details.get('completion_time'):
            try:
                start = datetime.fromisoformat(details['start_time'].replace('Z', '+00:00'))
                end = datetime.fromisoformat(details['completion_time'].replace('Z', '+00:00'))
                duration = int((end - start).total_seconds())
            except Exception:
                pass

        # Insert or update in database
        inserted = False
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()

                # Check if exists
                cursor.execute(
                    "SELECT id FROM build_failures WHERE pipelinerun_name = %s",
                    (pr_name,)
                )
                exists = cursor.fetchone()

                if exists:
                    # Update with comprehensive data
                    cursor.execute(
                        """
                        UPDATE build_failures SET
                            build_logs = COALESCE(%s, build_logs),
                            commit_sha = COALESCE(%s, commit_sha),
                            commit_short_sha = COALESCE(%s, commit_short_sha),
                            commit_url = COALESCE(%s, commit_url),
                            commit_message = COALESCE(%s, commit_message),
                            commit_author = COALESCE(%s, commit_author),
                            konflux_url = COALESCE(%s, konflux_url),
                            logs_full_url = COALESCE(%s, logs_full_url),
                            pr_number = COALESCE(%s, pr_number),
                            pr_url = COALESCE(%s, pr_url),
                            error_message = COALESCE(%s, error_message),
                            error_type = COALESCE(%s, error_type),
                            failed_step_name = COALESCE(%s, failed_step_name),
                            build_duration_seconds = COALESCE(%s, build_duration_seconds),
                            repository_url = COALESCE(%s, repository_url),
                            branch = COALESCE(%s, branch),
                            last_updated_at = NOW()
                        WHERE pipelinerun_name = %s
                        """,
                        (logs, details.get('commit_sha'), details.get('commit_short_sha'),
                         details.get('commit_url'), details.get('commit_message'),
                         details.get('commit_author'), details.get('konflux_url'),
                         details.get('pipeline_url'), details.get('pr_number'),
                         details.get('pr_url'), error_message, error_type,
                         failed_step, duration, details.get('repository_url'),
                         details.get('branch'), pr_name)
                    )
                    print(f"  ✓ Updated with {log_length} chars of logs")
                else:
                    # Insert new
                    cursor.execute(
                        """
                        INSERT INTO build_failures (
                            component_name, pipelinerun_name, pipelinerun_uid,
                            application, namespace, repository, repository_url, branch,
                            commit_sha, commit_short_sha, commit_url, commit_message, commit_author,
                            pr_number, pr_url, status, error_message, error_type,
                            failed_step_name, build_duration_seconds,
                            konflux_url, logs_full_url, build_logs
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (component.name, pr_name, pr_info['uid'],
                         self.config.k8s.application_name, self.config.k8s.namespace,
                         component.repository_url.replace('https://github.com/', '').replace('.git', ''),
                         component.repository_url, component.branch,
                         details.get('commit_sha'), details.get('commit_short_sha'),
                         details.get('commit_url'), details.get('commit_message'),
                         details.get('commit_author'), details.get('pr_number'),
                         details.get('pr_url'),
                         pr_info['status'].value if isinstance(pr_info['status'], BuildStatus) else pr_info['status'],
                         error_message, error_type,
                         failed_step, duration, details.get('konflux_url'),
                         details.get('pipeline_url'), logs)
                    )
                    inserted = True
                    print(f"  ✓ Inserted with {log_length} chars of logs")

                conn.commit()

        except Exception as e:
            print(f"  ✗ Database error: {e}")
            return True, False, log_length

        # Update component health
        self.db.update_component_health(component.name)

        return True, inserted, log_length

    def run(
        self,
        components: Optional[List[Component]] = None,
        limit: Optional[int] = None
    ) -> ScanResult:
        """Run comprehensive collection scan.

        Args:
            components: List of components to scan.
            limit: Limit number of components to process.

        Returns:
            ScanResult with statistics.
        """
        start_time = time.time()

        # Load components
        if components is None:
            # Try automatic discovery from cluster first
            print(f"Discovering failing components from cluster...")
            components = self.discover_components_from_cluster()

            # Fallback to file if discovery fails and file is configured
            if not components and self.config.components_file:
                print(f"Falling back to components file: {self.config.components_file}")
                components = Component.from_file(
                    str(self.config.components_file),
                    self.config.k8s.namespace
                )

            if not components:
                raise ValueError("No components discovered from cluster and no components file configured")

        # Apply limit if specified
        if limit:
            components = components[:limit]

        # Enrich components with metadata if needed
        print(f"Enriching component metadata...")
        components = [
            comp if comp.repository_url else replace(
                comp,
                **vars(self.get_component_metadata(comp.name) or comp)
            )
            for comp in components
        ]

        # Create scan record
        scan_id = self.db.create_scan(scan_type='python-comprehensive', scan_mode='full')

        # Scan each component
        total_found = 0
        total_new = 0
        total_log_chars = 0

        print(f"\n{'='*70}")
        print("Comprehensive CI Failure Collection")
        print(f"{'='*70}\n")

        for i, component in enumerate(components, 1):
            print(f"[{i}/{len(components)}] {component.name}")

            if not component.repository_url:
                print(f"  ✗ Component not found in cluster")
                continue

            found, inserted, log_chars = self.collect_comprehensive_failure(component)
            if found:
                total_found += 1
                total_log_chars += log_chars
            if inserted:
                total_new += 1

        # Complete scan
        duration = time.time() - start_time
        result = ScanResult(
            scan_id=scan_id,
            components_scanned=len(components),
            failures_found=total_found,
            new_failures=total_new,
            logs_fetched=total_found,  # We fetch logs for all found
            duration_seconds=duration
        )

        self.db.complete_scan(scan_id, result)

        return result


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Comprehensive CI failure collector - optimized for troubleshooting'
    )
    parser.add_argument(
        '--components-file',
        type=Path,
        help='File with component names (one per line)'
    )
    parser.add_argument(
        '--limit',
        type=int,
        help='Limit number of components to process'
    )
    args = parser.parse_args()

    # Load configuration
    config = CollectorConfig.from_env()
    if args.components_file:
        config = replace(config, components_file=args.components_file)

    # Run collector
    collector = ComprehensiveCollector(config)
    result = collector.run(limit=args.limit)

    # Print summary
    print(f"\n{'='*70}")
    print("Collection Complete")
    print(f"{'='*70}")
    print(f"Components scanned: {result.components_scanned}")
    print(f"Failures found: {result.failures_found}")
    print(f"New failures inserted: {result.new_failures}")
    print(f"Logs collected: {result.logs_fetched}")
    print(f"Duration: {result.duration_seconds:.1f}s")
    print()

    return 0


if __name__ == '__main__':
    sys.exit(main())
