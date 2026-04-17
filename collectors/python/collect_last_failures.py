#!/usr/bin/env python3
"""Collector focused on last failure per component with comprehensive logs.

This script:
1. For each component, gets the MOST RECENT failure
2. Fetches comprehensive logs from ALL TaskRuns
3. Extracts commit details (SHA, URL, message)
4. Stores complete data including Konflux UI links

Usage:
    python3 collect_last_failures.py [--components-file FILE]
"""

import sys
import time
import subprocess
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import replace
from datetime import datetime

from config import CollectorConfig
from database import Database
from kubearchive_client import KubeArchiveClient
from models import PipelineRun, BuildStatus, ScanResult, Component


class LastFailureCollector:
    """Collects last failure per component with full details."""

    def __init__(self, config: CollectorConfig):
        """Initialize collector.

        Args:
            config: Collector configuration.
        """
        self.config = config
        self.db = Database(config.db)
        self.kubearchive = KubeArchiveClient(
            api_url=config.k8s.kubearchive_api_url,
            namespace=config.k8s.namespace
        )

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
                check=True
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
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return None

    def get_last_failed_pipelinerun(self, component_name: str) -> Optional[Dict[str, Any]]:
        """Get the MOST RECENT failed PipelineRun for a component.

        Args:
            component_name: Component name.

        Returns:
            PipelineRun dictionary with name, uid, status or None if not found.
        """
        try:
            # Use kubectl tekton to get the most recent failure
            result = subprocess.run(
                ['bash', '-c',
                 f"yes '' 2>/dev/null | kubectl tekton get pr -n {self.config.k8s.namespace} "
                 f"--labels='appstudio.openshift.io/component={component_name}' "
                 f"--limit 20 2>/dev/null | grep -v '^NAME' | grep -v '^Next' | "
                 f"grep -v '^$' | grep -v 'Press any key' | head -20"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=30
            )

            if result.returncode != 0 or not result.stdout.strip():
                return None

            # Parse tabular output and find first (most recent) failure
            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue

                parts = line.split()
                if len(parts) < 5:
                    continue

                name = parts[0]
                uid = parts[1]
                status_str = parts[-1]  # Last field is STATUS

                # Return first failed build (most recent)
                if status_str.startswith('Failed'):
                    return {
                        'name': name,
                        'uid': uid,
                        'status': BuildStatus.FAILED
                    }

            return None

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None

    def extract_commit_details(self, pr_data: Dict[str, Any]) -> Dict[str, Optional[str]]:
        """Extract commit details from PipelineRun data.

        Args:
            pr_data: PipelineRun JSON from KubeArchive.

        Returns:
            Dictionary with commit_sha, commit_url, konflux_url.
        """
        annotations = pr_data.get('metadata', {}).get('annotations', {})
        params = pr_data.get('spec', {}).get('params', [])

        # Extract commit SHA (try multiple sources)
        commit_sha = (
            annotations.get('build.appstudio.redhat.com/commit_sha') or
            annotations.get('pipelinesascode.tekton.dev/sha') or
            next((p['value'] for p in params if p['name'] == 'revision'), None)
        )

        # Extract commit URL
        commit_url = annotations.get('pipelinesascode.tekton.dev/sha-url')

        # Generate commit URL if not present but have SHA and repo
        if not commit_url and commit_sha:
            repo_url = next((p['value'] for p in params if p['name'] == 'git-url'), None)
            if repo_url:
                commit_url = f"{repo_url.rstrip('.git')}/commit/{commit_sha}"

        # Extract Konflux UI URL
        konflux_url = annotations.get('pipelinesascode.tekton.dev/log-url')

        # Generate Konflux URL if not present
        if not konflux_url:
            pr_name = pr_data.get('metadata', {}).get('name')
            namespace = pr_data.get('metadata', {}).get('namespace', self.config.k8s.namespace)
            if pr_name:
                konflux_url = (
                    f"https://konflux-ui.apps.CLUSTER_DOMAIN"
                    f"/ns/{namespace}/pipelinerun/{pr_name}"
                )

        return {
            'commit_sha': commit_sha,
            'commit_url': commit_url,
            'konflux_url': konflux_url
        }

    def get_comprehensive_logs(self, pr_name: str) -> Tuple[Optional[str], Optional[str]]:
        """Fetch comprehensive logs from all TaskRuns.

        Tries both active pods and KubeArchive API.

        Args:
            pr_name: PipelineRun name.

        Returns:
            Tuple of (logs, konflux_url).
        """
        # Try active pods first (fastest for recent builds)
        logs = self.get_logs_from_active_pods(pr_name)
        if logs:
            return logs[:100000], None  # Truncate to 100KB

        # Try KubeArchive (for archived builds)
        logs = self.kubearchive.get_pipelinerun_logs(pr_name)

        return logs, None

    def get_logs_from_active_pods(self, pr_name: str) -> Optional[str]:
        """Fetch logs from active Kubernetes pods.

        Args:
            pr_name: PipelineRun name.

        Returns:
            Combined logs from all pods or None.
        """
        try:
            # Get pods for this PipelineRun
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
            for pod in pods[:5]:  # Get up to 5 pods (increased from 3)
                try:
                    logs_result = subprocess.run(
                        ['oc', 'logs', pod, '-n', self.config.k8s.namespace,
                         '--all-containers'],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        timeout=20
                    )
                    if logs_result.stdout:
                        all_logs.append(f"===== Pod: {pod} =====\n{logs_result.stdout}")
                except subprocess.TimeoutExpired:
                    continue

            return "\n\n".join(all_logs) if all_logs else None

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None

    def collect_last_failure(self, component: Component) -> Tuple[bool, bool]:
        """Collect last failure for a component with full details.

        Args:
            component: Component to scan.

        Returns:
            Tuple of (found_failure, inserted_new).
        """
        # Get last failed PipelineRun
        pr_info = self.get_last_failed_pipelinerun(component.name)
        if not pr_info:
            return False, False

        pr_name = pr_info['name']

        # Check if already in DB with logs
        if self.db.pipelinerun_exists(pr_name):
            # Check if it has logs
            has_logs = self.db.connection()
            try:
                with self.db.connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT build_logs IS NOT NULL AND LENGTH(build_logs) > 0 "
                        "FROM build_failures WHERE pipelinerun_name = %s",
                        (pr_name,)
                    )
                    result = cursor.fetchone()
                    if result and result[0]:
                        print(f"  ✓ Already have logs for {pr_name}")
                        return True, False
            except Exception:
                pass

        # Fetch comprehensive logs
        print(f"  Fetching logs for {pr_name}...")
        logs, _ = self.get_comprehensive_logs(pr_name)

        # Get full PipelineRun details from KubeArchive for commit info
        pr_data = self.kubearchive.get_pipelinerun(pr_name)
        commit_details = {}
        if pr_data:
            commit_details = self.extract_commit_details(pr_data)

        # Build PipelineRun object
        pipelinerun = PipelineRun(
            name=pr_name,
            uid=pr_info['uid'],
            namespace=self.config.k8s.namespace,
            component=component.name,
            repository=component.repository_url.replace('https://github.com/', '').replace('.git', ''),
            repository_url=component.repository_url,
            branch=component.branch,
            status=pr_info['status'],
            build_logs=logs,
            commit_sha=commit_details.get('commit_sha'),
            commit_url=commit_details.get('commit_url')
        )

        # Insert or update in database
        inserted = False
        if self.db.pipelinerun_exists(pr_name):
            # Update with logs and commit details
            try:
                with self.db.connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        UPDATE build_failures
                        SET build_logs = %s,
                            commit_sha = %s,
                            commit_url = %s,
                            konflux_url = %s,
                            last_updated_at = NOW()
                        WHERE pipelinerun_name = %s
                        """,
                        (logs, commit_details.get('commit_sha'),
                         commit_details.get('commit_url'),
                         commit_details.get('konflux_url'), pr_name)
                    )
                    conn.commit()
                    print(f"  ✓ Updated with logs ({len(logs) if logs else 0} chars)")
            except Exception as e:
                print(f"  ✗ Failed to update: {e}")
        else:
            # Insert new
            inserted = self.db.insert_pipelinerun(pipelinerun, self.config.k8s.application_name)
            if inserted:
                # Also update konflux_url
                try:
                    with self.db.connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE build_failures SET konflux_url = %s WHERE pipelinerun_name = %s",
                            (commit_details.get('konflux_url'), pr_name)
                        )
                        conn.commit()
                except Exception:
                    pass
                print(f"  ✓ Inserted with logs ({len(logs) if logs else 0} chars)")

        # Update component health
        self.db.update_component_health(component.name)

        return True, inserted

    def run(self, components: Optional[List[Component]] = None) -> ScanResult:
        """Run collection scan focusing on last failure per component.

        Args:
            components: List of components to scan. If None, loads from config file.

        Returns:
            ScanResult with statistics.
        """
        start_time = time.time()

        # Load components
        if components is None:
            if self.config.components_file:
                components = Component.from_file(
                    str(self.config.components_file),
                    self.config.k8s.namespace
                )
            else:
                raise ValueError("No components provided and no components file configured")

        # Enrich components with metadata
        components = [
            comp if comp.repository_url else replace(
                comp,
                **vars(self.get_component_metadata(comp.name) or comp)
            )
            for comp in components
        ]

        # Create scan record
        scan_id = self.db.create_scan(scan_type='python-last', scan_mode='comprehensive')

        # Scan each component
        total_found = 0
        total_new = 0
        total_logs = 0

        print(f"\n{'='*60}")
        print("Collecting Last Failure Per Component")
        print(f"{'='*60}\n")

        for i, component in enumerate(components, 1):
            print(f"[{i}/{len(components)}] {component.name}")

            if not component.repository_url:
                print(f"  ✗ Component not found in cluster")
                continue

            found, inserted = self.collect_last_failure(component)
            if found:
                total_found += 1
                total_logs += 1  # We always fetch logs in this mode
            if inserted:
                total_new += 1

        # Complete scan
        duration = time.time() - start_time
        result = ScanResult(
            scan_id=scan_id,
            components_scanned=len(components),
            failures_found=total_found,
            new_failures=total_new,
            logs_fetched=total_logs,
            duration_seconds=duration
        )

        self.db.complete_scan(scan_id, result)

        return result


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Collect last failure per component with comprehensive logs'
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

    # Run collector
    collector = LastFailureCollector(config)
    result = collector.run()

    # Print summary
    print(f"\n{'='*60}")
    print("Collection Complete")
    print(f"{'='*60}")
    print(f"Components scanned: {result.components_scanned}")
    print(f"Last failures found: {result.failures_found}")
    print(f"New inserted: {result.new_failures}")
    print(f"Logs fetched: {result.logs_fetched}")
    print(f"Duration: {result.duration_seconds:.1f}s")
    print()

    return 0


if __name__ == '__main__':
    sys.exit(main())
