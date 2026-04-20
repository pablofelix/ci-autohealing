#!/usr/bin/env python3
"""Sync component status - mark resolved failures and detect new successes.

This script:
1. Checks which components are currently failing
2. Marks as resolved components that were failing but now succeed
3. Records successful builds for historical tracking
4. Updates component health status

Usage:
    python3 sync_component_status.py [--components-file FILE]
"""

import sys
import time
import subprocess
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Set
from dataclasses import replace
from datetime import datetime

from config import CollectorConfig
from database import Database
from models import Component, BuildStatus


class StatusSynchronizer:
    """Synchronizes component status between Kubernetes and database."""

    def __init__(self, config: CollectorConfig):
        """Initialize synchronizer.

        Args:
            config: Collector configuration.
        """
        self.config = config
        self.db = Database(config.db)

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

    def get_current_status(self, component_name: str) -> Optional[Dict[str, Any]]:
        """Get current push build status for a component from live cluster + KubeArchive.

        Only considers push builds (not incoming re-triggers) so that a
        successful re-trigger doesn't incorrectly resolve a push failure.

        Args:
            component_name: Component name.

        Returns:
            Dict with last_build status, name, uid or None.
        """
        import requests

        latest = None  # (timestamp, name, uid, reason)

        def check_pr(pr):
            """Track latest push build only."""
            nonlocal latest
            labels = pr.get('metadata', {}).get('labels', {})
            event_type = labels.get('pipelinesascode.tekton.dev/event-type', '')
            if event_type != 'push':
                return
            ts = pr.get('metadata', {}).get('creationTimestamp', '')
            conditions = pr.get('status', {}).get('conditions', [])
            if conditions:
                name = pr.get('metadata', {}).get('name')
                uid = pr.get('metadata', {}).get('uid')
                reason = conditions[-1].get('reason', '')
                if not latest or ts > latest[0]:
                    latest = (ts, name, uid, reason)

        # Source 1: Live cluster
        try:
            result = subprocess.run(
                ['oc', 'get', 'pipelinerun', '-n', self.config.k8s.namespace,
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
            kubearchive_url = self._get_kubearchive_url()
            token = self._get_oc_token()
            if kubearchive_url and token:
                session = requests.Session()
                session.headers.update({
                    'Authorization': f'Bearer {token}',
                    'Accept': 'application/json'
                })
                url = f"{kubearchive_url}/apis/tekton.dev/v1/namespaces/{self.config.k8s.namespace}/pipelineruns"
                params = {
                    'labelSelector': f'appstudio.openshift.io/component={component_name},pipelines.appstudio.openshift.io/type=build',
                    'limit': 50
                }
                resp = session.get(url, params=params, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    for pr in data.get('items', []):
                        check_pr(pr)
        except Exception:
            pass

        if not latest:
            return None

        ts, name, uid, reason = latest

        # Map reason to BuildStatus
        if reason in ('Succeeded', 'Completed'):
            status = BuildStatus.SUCCEEDED
        elif reason == 'Failed':
            status = BuildStatus.FAILED
        elif reason in ('PipelineRunTimeout',):
            status = BuildStatus.FAILED
        elif reason == 'Running':
            status = BuildStatus.RUNNING
        else:
            status = BuildStatus.PENDING

        return {
            'name': name,
            'uid': uid,
            'status': status
        }

    @staticmethod
    def _get_kubearchive_url() -> Optional[str]:
        """Get KubeArchive API URL."""
        try:
            result = subprocess.run(
                ['oc', 'get', 'cm', '-n', 'product-kubearchive', 'kubearchive-api-url',
                 '-o', 'jsonpath={.data.URL}'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return "https://kubearchive-api-server-product-kubearchive.apps.CLUSTER_DOMAIN"

    @staticmethod
    def _get_oc_token() -> Optional[str]:
        """Get OpenShift authentication token."""
        try:
            result = subprocess.run(
                ['oc', 'whoami', '-t'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def get_unresolved_components(self) -> Set[str]:
        """Get list of components with unresolved failures.

        Returns:
            Set of component names.
        """
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT DISTINCT component_name
                    FROM build_failures
                    WHERE is_resolved = FALSE
                      AND application = %s
                    """,
                    (self.config.k8s.application_name,)
                )
                return {row[0] for row in cursor.fetchall()}
        except Exception:
            return set()

    def mark_as_resolved(self, component_name: str, resolution_pr_name: str) -> bool:
        """Mark all failures for a component as resolved.

        Args:
            component_name: Component name.
            resolution_pr_name: PipelineRun that succeeded.

        Returns:
            True if updated.
        """
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE build_failures
                    SET is_resolved = TRUE,
                        resolved_at = NOW(),
                        resolution_type = 'auto-detected',
                        resolution_pr_url = %s,
                        last_updated_at = NOW()
                    WHERE component_name = %s
                      AND is_resolved = FALSE
                      AND application = %s
                    """,
                    (f"https://konflux-ui.apps.CLUSTER_DOMAIN"
                     f"/ns/{self.config.k8s.namespace}/pipelinerun/{resolution_pr_name}",
                     component_name, self.config.k8s.application_name)
                )
                updated = cursor.rowcount
                conn.commit()
                return updated > 0
        except Exception as e:
            print(f"  ✗ Failed to mark as resolved: {e}")
            return False

    def record_success(self, component: Component, pr_info: Dict[str, Any]) -> bool:
        """Record a successful build (for historical tracking).

        Args:
            component: Component that succeeded.
            pr_info: PipelineRun info.

        Returns:
            True if recorded.
        """
        # Check if already recorded
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT 1 FROM build_failures WHERE pipelinerun_name = %s",
                    (pr_info['name'],)
                )
                if cursor.fetchone():
                    return False  # Already recorded

                # Insert success record
                cursor.execute(
                    """
                    INSERT INTO build_failures (
                        component_name, pipelinerun_name, pipelinerun_uid,
                        application, namespace, repository, repository_url, branch,
                        status, is_resolved
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (component.name, pr_info['name'], pr_info['uid'],
                     self.config.k8s.application_name, self.config.k8s.namespace,
                     component.repository_url.replace('https://github.com/', '').replace('.git', ''),
                     component.repository_url, component.branch,
                     'Succeeded', True)
                )
                conn.commit()
                return True
        except Exception as e:
            print(f"  ✗ Failed to record success: {e}")
            return False

    def get_last_db_status(self, component_name: str) -> Optional[str]:
        """Get last known status from database.

        Args:
            component_name: Component name.

        Returns:
            Status string or None.
        """
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT status
                    FROM build_failures
                    WHERE component_name = %s
                      AND application = %s
                    ORDER BY first_detected_at DESC
                    LIMIT 1
                    """,
                    (component_name, self.config.k8s.application_name)
                )
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def sync_component(self, component: Component) -> Dict[str, Any]:
        """Synchronize status for one component.

        Args:
            component: Component to sync.

        Returns:
            Dict with sync results.
        """
        result = {
            'component': component.name,
            'was_failing': False,
            'now_resolved': False,
            'success_recorded': False,
            'current_status': None
        }

        # Check if component was failing (unresolved failures in DB)
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM build_failures
                    WHERE component_name = %s
                      AND is_resolved = FALSE
                      AND application = %s
                    """,
                    (component.name, self.config.k8s.application_name)
                )
                unresolved_count = cursor.fetchone()[0]
                result['was_failing'] = unresolved_count > 0
        except Exception:
            pass

        # Get current status from Kubernetes
        current = self.get_current_status(component.name)

        if not current:
            # No PipelineRuns in Kubernetes
            # If component was failing, mark as resolved (archived/cleaned up)
            if result['was_failing']:
                if self.mark_as_resolved(component.name, 'archived'):
                    result['now_resolved'] = True
                    print(f"  ✓ Marked as resolved (no PipelineRuns in cluster - likely archived)")
                    result['current_status'] = 'archived'
                    return result

            # Use last known status from DB
            db_status = self.get_last_db_status(component.name)
            result['current_status'] = db_status if db_status else 'unknown'
            print(f"  → current status: {result['current_status']} (from DB, not in K8s)")
            return result

        result['current_status'] = current['status'].value

        # If was failing and now succeeded, mark as resolved
        if result['was_failing'] and current['status'] == BuildStatus.SUCCEEDED:
            if self.mark_as_resolved(component.name, current['name']):
                result['now_resolved'] = True
                print(f"  ✓ Marked as resolved (last build: {current['name']})")

            # Record the success
            if self.record_success(component, current):
                result['success_recorded'] = True
                print(f"  ✓ Recorded success build")

        # If currently succeeded and no record exists, record it
        elif not result['was_failing'] and current['status'] == BuildStatus.SUCCEEDED:
            if self.record_success(component, current):
                result['success_recorded'] = True
                print(f"  ✓ Recorded success build (no previous failures)")

        # If currently failing but not recorded yet
        elif current['status'] == BuildStatus.FAILED:
            print(f"  → current status: Failed (will be collected by comprehensive collector)")

        return result

    def run(self, components: Optional[List[Component]] = None) -> Dict[str, Any]:
        """Run status synchronization.

        Args:
            components: List of components to sync.

        Returns:
            Dict with overall statistics.
        """
        start_time = time.time()

        # Load components
        if components is None:
            # Get components that have unresolved failures in the DB
            print(f"Loading components with unresolved failures from database...")
            unresolved_names = self.get_unresolved_components()

            if not unresolved_names:
                print(f"  ✓ No unresolved failures found")
                return {
                    'components_synced': 0,
                    'resolved': 0,
                    'successes': 0,
                    'duration': time.time() - start_time
                }

            print(f"  ✓ Found {len(unresolved_names)} components with unresolved failures")

            # Create Component objects from names
            components = [
                Component(
                    name=name,
                    namespace=self.config.k8s.namespace,
                    repository_url='',
                    branch=''
                )
                for name in unresolved_names
            ]

        # Enrich components with metadata
        print(f"Enriching component metadata...")
        components = [
            comp if comp.repository_url else replace(
                comp,
                **vars(self.get_component_metadata(comp.name) or comp)
            )
            for comp in components
        ]

        print(f"\n{'='*70}")
        print("Component Status Synchronization")
        print(f"{'='*70}\n")

        total_resolved = 0
        total_successes = 0
        components_synced = 0

        for i, component in enumerate(components, 1):
            print(f"[{i}/{len(components)}] {component.name}")

            if not component.repository_url:
                print(f"  ✗ Component not found in cluster")
                continue

            result = self.sync_component(component)
            components_synced += 1

            if result['now_resolved']:
                total_resolved += 1
            if result['success_recorded']:
                total_successes += 1

            if not result['now_resolved'] and not result['success_recorded']:
                status = result['current_status']
                was_failing = "was failing, " if result['was_failing'] else ""
                print(f"  → {was_failing}current status: {status}")

        duration = time.time() - start_time

        print(f"\n{'='*70}")
        print("Synchronization Complete")
        print(f"{'='*70}")
        print(f"Components synced: {components_synced}")
        print(f"Failures resolved: {total_resolved}")
        print(f"Successes recorded: {total_successes}")
        print(f"Duration: {duration:.1f}s")
        print()

        return {
            'components_synced': components_synced,
            'resolved': total_resolved,
            'successes': total_successes,
            'duration': duration
        }


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Synchronize component status - mark resolved and track successes'
    )
    parser.add_argument(
        '--components-file',
        type=Path,
        help='File with component names (one per line)'
    )
    args = parser.parse_args()

    # Load configuration
    config = CollectorConfig.from_env()
    if args.components_file:
        config = replace(config, components_file=args.components_file)

    # Run synchronizer
    synchronizer = StatusSynchronizer(config)
    result = synchronizer.run()

    return 0


if __name__ == '__main__':
    sys.exit(main())
