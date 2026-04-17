#!/usr/bin/env python3
"""Main collector for CI build failures.

This script:
1. Scans components for failed PipelineRuns
2. Fetches logs from active pods or KubeArchive
3. Stores failures and logs in PostgreSQL database

Usage:
    python3 collect_failures.py [--components-file FILE] [--fetch-logs]
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


class FailureCollector:
    """Orchestrates collection of build failures and logs."""

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

    def get_failed_pipelineruns(self, component_name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get failed PipelineRuns for a component using kubectl tekton.

        Args:
            component_name: Component name.
            limit: Maximum number of PipelineRuns to fetch.

        Returns:
            List of PipelineRun dictionaries with name, uid, status.
        """
        try:
            # Use kubectl tekton to get tabular output
            result = subprocess.run(
                ['bash', '-c',
                 f"yes '' 2>/dev/null | kubectl tekton get pr -n {self.config.k8s.namespace} "
                 f"--labels='appstudio.openshift.io/component={component_name}' "
                 f"--limit {limit} 2>/dev/null | grep -v '^NAME' | grep -v '^Next' | "
                 f"grep -v '^$' | grep -v 'Press any key'"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                
                timeout=30
            )

            if result.returncode != 0 or not result.stdout.strip():
                return []

            # Parse tabular output: NAME  UID  STARTED  DURATION  STATUS
            pipelineruns = []
            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue

                parts = line.split()
                if len(parts) < 5:
                    continue

                name = parts[0]
                uid = parts[1]
                status_str = parts[-1]  # Last field is STATUS

                # Only include failed builds
                if status_str.startswith('Failed'):
                    pipelineruns.append({
                        'name': name,
                        'uid': uid,
                        'status': BuildStatus.FAILED
                    })

            return pipelineruns

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return []

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
            for pod in pods[:3]:  # Limit to first 3 pods
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

    def fetch_logs(self, pr_name: str) -> Optional[str]:
        """Fetch logs using multiple fallback methods.

        Tries in order:
        1. Active Kubernetes pods (fastest, for recent builds)
        2. KubeArchive API (for archived builds)

        Args:
            pr_name: PipelineRun name.

        Returns:
            Build logs or None if not available.
        """
        # Method 1: Active pods
        logs = self.get_logs_from_active_pods(pr_name)
        if logs:
            return logs[:100000]  # Truncate to 100KB

        # Method 2: KubeArchive
        logs = self.kubearchive.get_pipelinerun_logs(pr_name)
        if logs:
            return logs

        return None

    def collect_component_failures(
        self,
        component: Component,
        fetch_logs: bool = False
    ) -> Tuple[int, int, int]:
        """Collect failures for a single component.

        Args:
            component: Component to scan.
            fetch_logs: Whether to fetch logs immediately.

        Returns:
            Tuple of (failures_found, new_inserted, logs_fetched).
        """
        failures_found = 0
        new_inserted = 0
        logs_fetched = 0

        # Get failed PipelineRuns
        failed_prs = self.get_failed_pipelineruns(component.name)
        failures_found = len(failed_prs)

        if not failed_prs:
            return failures_found, new_inserted, logs_fetched

        # Process each failure
        for pr_data in failed_prs:
            # Check if already in DB
            if self.db.pipelinerun_exists(pr_data['name']):
                continue

            # Build PipelineRun object
            logs = None
            if fetch_logs:
                logs = self.fetch_logs(pr_data['name'])
                if logs:
                    logs_fetched += 1

            pipelinerun = PipelineRun(
                name=pr_data['name'],
                uid=pr_data['uid'],
                namespace=self.config.k8s.namespace,
                component=component.name,
                repository=component.repository_url.replace('https://github.com/', '').replace('.git', ''),
                repository_url=component.repository_url,
                branch=component.branch,
                status=pr_data['status'],
                build_logs=logs
            )

            # Insert into database
            if self.db.insert_pipelinerun(pipelinerun, self.config.k8s.application_name):
                new_inserted += 1

        # Update component health
        if new_inserted > 0:
            self.db.update_component_health(component.name)

        return failures_found, new_inserted, logs_fetched

    def run(
        self,
        components: Optional[List[Component]] = None,
        fetch_logs: bool = False
    ) -> ScanResult:
        """Run complete collection scan.

        Args:
            components: List of components to scan. If None, loads from config file.
            fetch_logs: Whether to fetch logs immediately for new failures.

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
        scan_id = self.db.create_scan(scan_type='python', scan_mode='full' if fetch_logs else 'simple')

        # Scan each component
        total_failures = 0
        total_new = 0
        total_logs = 0

        for i, component in enumerate(components, 1):
            print(f"[{i}/{len(components)}] {component.name}")

            if not component.repository_url:
                print(f"  ✗ Component not found in cluster")
                continue

            failures, new, logs = self.collect_component_failures(component, fetch_logs)
            total_failures += failures
            total_new += new
            total_logs += logs

            if failures > 0:
                print(f"  Failures: {failures}, New: {new}, Logs: {logs}")

        # Complete scan
        duration = time.time() - start_time
        result = ScanResult(
            scan_id=scan_id,
            components_scanned=len(components),
            failures_found=total_failures,
            new_failures=total_new,
            logs_fetched=total_logs,
            duration_seconds=duration
        )

        self.db.complete_scan(scan_id, result)

        return result


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Collect CI build failures')
    parser.add_argument(
        '--components-file',
        type=Path,
        help='File with component names (one per line)'
    )
    parser.add_argument(
        '--fetch-logs',
        action='store_true',
        help='Fetch logs immediately for new failures'
    )
    args = parser.parse_args()

    # Load configuration
    config = CollectorConfig.from_env()
    if args.components_file:
        config = replace(config, components_file=args.components_file)

    # Run collector
    collector = FailureCollector(config)
    result = collector.run(fetch_logs=args.fetch_logs)

    # Print summary
    print("\n========================================")
    print("Collection Complete")
    print("========================================")
    print(f"Components scanned: {result.components_scanned}")
    print(f"Failures found: {result.failures_found}")
    print(f"New inserted: {result.new_failures}")
    print(f"Logs fetched: {result.logs_fetched}")
    print(f"Duration: {result.duration_seconds:.1f}s")
    print()

    return 0


if __name__ == '__main__':
    sys.exit(main())
