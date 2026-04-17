#!/usr/bin/env python3
"""
CI Auto-Healing Build Failure Scanner

Modes:
  - trigger: Single scan, exit after completion
  - daemon: Continuous scanning at configured interval
  - component: Scan specific component(s)
"""

import os
import sys
import time
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any
import subprocess
import json

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.progress import track
from rich import print as rprint

# Load environment
load_dotenv(Path(__file__).parent.parent / ".env")

# Setup logging
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

console = Console()


class BuildFailureScanner:
    """Scanner for Konflux build failures"""

    def __init__(
        self,
        namespace: str = None,
        components_file: Optional[str] = None,
        lookback_hours: int = 48,
        max_prs: int = 50
    ):
        self.namespace = namespace or os.getenv("NAMESPACE", "NAMESPACE_PLACEHOLDER")
        self.components_file = components_file or os.getenv("COMPONENTS_FILE")
        self.lookback_hours = lookback_hours
        self.max_prs = max_prs

        # Import DB after env loaded
        try:
            from db.database import Database
            self.db = Database()
        except ImportError:
            logger.error("Failed to import database module")
            self.db = None

    def get_components_to_scan(self) -> List[str]:
        """Get list of components to scan"""
        if self.components_file and os.path.exists(self.components_file):
            logger.info(f"Reading components from {self.components_file}")
            with open(self.components_file) as f:
                components = [line.strip() for line in f if line.strip() and not line.startswith('#')]
            logger.info(f"Found {len(components)} components in file")
            return components
        else:
            logger.info(f"Scanning all components in namespace {self.namespace}")
            return self._get_all_components()

    def _get_all_components(self) -> List[str]:
        """Get all components from namespace"""
        try:
            cmd = f"oc get components -n {self.namespace} --no-headers -o custom-columns=NAME:.metadata.name"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
            components = [line.strip() for line in result.stdout.split('\n') if line.strip()]
            logger.info(f"Found {len(components)} components in namespace")
            return components
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get components: {e}")
            return []

    def scan_component(self, component: str) -> Dict[str, Any]:
        """Scan a single component for build failures"""
        logger.debug(f"Scanning component: {component}")

        results = {
            'component': component,
            'builds_found': 0,
            'failures_found': 0,
            'new_failures': 0,
            'pipelineruns': []
        }

        try:
            # Get component metadata
            component_data = self._get_component_metadata(component)
            if not component_data:
                logger.warning(f"Component {component} not found")
                return results

            # Check recent builds in Kubernetes
            k8s_prs = self._get_k8s_pipelineruns(component)

            # Check historical builds in Tekton Results (if needed)
            # tekton_prs = self._get_tekton_pipelineruns(component)

            # For now, just use K8s builds
            for pr in k8s_prs:
                results['builds_found'] += 1

                if pr.get('status') in ['Failed', 'Cancelled', 'Timeout']:
                    results['failures_found'] += 1

                    # Check if this failure is already in DB
                    if self.db and not self.db.build_failure_exists(pr['name']):
                        results['new_failures'] += 1

                        # Parse and store failure
                        failure_data = self._parse_pipelinerun(pr, component_data)
                        if failure_data and self.db:
                            self.db.insert_build_failure(failure_data)
                            logger.info(f"Stored new failure: {pr['name']}")

                results['pipelineruns'].append(pr)

        except Exception as e:
            logger.error(f"Error scanning component {component}: {e}", exc_info=True)

        return results

    def _get_component_metadata(self, component: str) -> Optional[Dict]:
        """Get component metadata from OpenShift"""
        try:
            cmd = f"oc get component {component} -n {self.namespace} -o json"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)

            spec = data.get('spec', {})
            source = spec.get('source', {}).get('git', {})

            return {
                'name': component,
                'repository': source.get('url', '').replace('https://github.com/', '').replace('.git', ''),
                'repository_url': source.get('url', ''),
                'branch': source.get('revision', ''),
                'context': source.get('context', '.'),
                'container_image': spec.get('containerImage', '')
            }
        except Exception as e:
            logger.error(f"Failed to get component metadata for {component}: {e}")
            return None

    def _get_k8s_pipelineruns(self, component: str) -> List[Dict]:
        """Get recent PipelineRuns from Kubernetes"""
        try:
            cmd = (
                f"oc get pipelinerun -n {self.namespace} "
                f"-l appstudio.openshift.io/component={component} "
                f"--sort-by=.metadata.creationTimestamp "
                f"-o json"
            )
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)

            pipelineruns = []
            for item in data.get('items', []):
                pr = {
                    'name': item['metadata']['name'],
                    'status': self._get_pr_status(item),
                    'start_time': item['status'].get('startTime'),
                    'completion_time': item['status'].get('completionTime'),
                    'yaml': item
                }
                pipelineruns.append(pr)

            logger.debug(f"Found {len(pipelineruns)} PipelineRuns for {component} in Kubernetes")
            return pipelineruns[-self.max_prs:]  # Limit to recent builds

        except Exception as e:
            logger.error(f"Failed to get K8s PipelineRuns for {component}: {e}")
            return []

    def _get_pr_status(self, pr_yaml: Dict) -> str:
        """Extract status from PipelineRun YAML"""
        conditions = pr_yaml.get('status', {}).get('conditions', [])
        if conditions:
            reason = conditions[0].get('reason', 'Unknown')
            return reason
        return 'Unknown'

    def _parse_pipelinerun(self, pr: Dict, component_data: Dict) -> Optional[Dict]:
        """Parse PipelineRun data for database insertion"""
        try:
            pr_yaml = pr['yaml']
            metadata = pr_yaml.get('metadata', {})
            spec = pr_yaml.get('spec', {})
            status = pr_yaml.get('status', {})
            annotations = metadata.get('annotations', {})

            # Extract commit info
            commit_sha = annotations.get('pipelinesascode.tekton.dev/sha', '')
            commit_message = annotations.get('pipelinesascode.tekton.dev/sha-title', '')
            commit_url = annotations.get('pipelinesascode.tekton.dev/sha-url', '')

            # Find failed task
            failed_task = None
            failed_task_name = None
            for task_run in status.get('childReferences', []):
                if task_run.get('kind') == 'TaskRun':
                    # Would need to get TaskRun details to check status
                    # For now, extract from status message
                    pass

            # Get error message
            conditions = status.get('conditions', [])
            error_message = conditions[0].get('message', '') if conditions else ''

            # Build failure data
            failure_data = {
                'component_name': component_data['name'],
                'pipelinerun_name': pr['name'],
                'application': metadata.get('labels', {}).get('appstudio.openshift.io/application'),
                'namespace': self.namespace,

                # Source
                'repository': component_data['repository'],
                'repository_url': component_data['repository_url'],
                'branch': component_data['branch'],
                'commit_sha': commit_sha,
                'commit_short_sha': commit_sha[:7] if commit_sha else None,
                'commit_message': commit_message,
                'commit_url': commit_url,

                # Build info
                'status': pr['status'],
                'failed_task_name': failed_task_name,
                'error_message': error_message,
                'failure_reason': conditions[0].get('reason', '') if conditions else '',

                # Timing
                'build_start_time': pr.get('start_time'),
                'build_completion_time': pr.get('completion_time'),

                # Metadata
                'raw_pipelinerun_yaml': pr_yaml,
                'konflux_url': self._generate_konflux_url(pr['name']),
            }

            return failure_data

        except Exception as e:
            logger.error(f"Failed to parse PipelineRun {pr['name']}: {e}", exc_info=True)
            return None

    def _generate_konflux_url(self, pr_name: str) -> str:
        """Generate Konflux UI URL for PipelineRun"""
        # This is a placeholder - adjust based on actual Konflux URL structure
        return f"https://console.redhat.com/preview/application-pipeline/workspaces/{self.namespace}/pipelineruns/{pr_name}"

    def scan_all(self, components: Optional[List[str]] = None) -> Dict[str, Any]:
        """Scan all components"""
        if components is None:
            components = self.get_components_to_scan()

        if not components:
            logger.warning("No components to scan")
            return {'components_scanned': 0}

        scan_results = {
            'scan_id': None,
            'started_at': datetime.now(),
            'components_scanned': 0,
            'failures_found': 0,
            'new_failures': 0,
            'results': []
        }

        # Start scan in DB
        if self.db:
            scan_results['scan_id'] = self.db.start_scan(
                scan_type='manual',
                scan_mode='full' if not self.components_file else 'incremental',
                config={'components_count': len(components)}
            )

        console.print(f"\n[bold blue]Scanning {len(components)} components...[/bold blue]\n")

        for component in track(components, description="Scanning components..."):
            result = self.scan_component(component)
            scan_results['results'].append(result)
            scan_results['components_scanned'] += 1
            scan_results['failures_found'] += result['failures_found']
            scan_results['new_failures'] += result['new_failures']

        # Complete scan in DB
        scan_results['completed_at'] = datetime.now()
        if self.db and scan_results['scan_id']:
            self.db.complete_scan(
                scan_id=scan_results['scan_id'],
                components_scanned=scan_results['components_scanned'],
                failures_found=scan_results['failures_found'],
                new_failures=scan_results['new_failures']
            )

        return scan_results

    def print_results(self, results: Dict[str, Any]):
        """Print scan results in a nice table"""
        console.print("\n[bold green]Scan Results[/bold green]")
        console.print(f"Scan ID: {results.get('scan_id', 'N/A')}")
        console.print(f"Duration: {(results.get('completed_at', datetime.now()) - results['started_at']).total_seconds():.1f}s\n")

        table = Table(title="Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", style="magenta")

        table.add_row("Components Scanned", str(results['components_scanned']))
        table.add_row("Total Failures Found", str(results['failures_found']))
        table.add_row("New Failures", str(results['new_failures']))

        console.print(table)

        # Show new failures
        if results['new_failures'] > 0:
            console.print("\n[bold yellow]New Failures:[/bold yellow]")
            for result in results['results']:
                if result['new_failures'] > 0:
                    console.print(f"  • {result['component']}: {result['new_failures']} new")


def trigger_mode(scanner: BuildFailureScanner, components: Optional[List[str]] = None):
    """Single scan and exit"""
    logger.info("Starting trigger mode scan")
    results = scanner.scan_all(components)
    scanner.print_results(results)
    logger.info("Trigger scan completed")


def daemon_mode(scanner: BuildFailureScanner, interval: int = 300):
    """Continuous scanning"""
    logger.info(f"Starting daemon mode (interval: {interval}s)")
    console.print(f"[bold green]Daemon mode started[/bold green] - scanning every {interval}s")
    console.print("Press Ctrl+C to stop\n")

    scan_count = 0
    try:
        while True:
            scan_count += 1
            console.print(f"\n[bold blue]═══ Scan #{scan_count} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ═══[/bold blue]")

            results = scanner.scan_all()
            scanner.print_results(results)

            console.print(f"\n[dim]Next scan in {interval}s...[/dim]")
            time.sleep(interval)

    except KeyboardInterrupt:
        console.print("\n[yellow]Daemon stopped by user[/yellow]")
        logger.info(f"Daemon completed {scan_count} scans")


def main():
    parser = argparse.ArgumentParser(
        description="CI Auto-Healing Build Failure Scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single scan (trigger mode)
  ./scanner.py --mode trigger

  # Daemon mode (continuous scanning)
  ./scanner.py --mode daemon --interval 300

  # Scan specific components
  ./scanner.py --mode component --components odh-feature-server-v3-4 odh-trustyai-v3-4

  # Use custom components file
  ./scanner.py --mode trigger --components-file /path/to/components.txt
        """
    )

    parser.add_argument(
        '--mode',
        choices=['trigger', 'daemon', 'component'],
        default='trigger',
        help='Scanner mode (default: trigger)'
    )

    parser.add_argument(
        '--interval',
        type=int,
        default=int(os.getenv('SCANNER_INTERVAL', '300')),
        help='Scan interval in seconds for daemon mode (default: 300)'
    )

    parser.add_argument(
        '--components',
        nargs='+',
        help='Specific components to scan (for component mode)'
    )

    parser.add_argument(
        '--components-file',
        help='File containing list of components (one per line)'
    )

    parser.add_argument(
        '--namespace',
        default=os.getenv('NAMESPACE', 'NAMESPACE_PLACEHOLDER'),
        help='Kubernetes namespace (default: NAMESPACE_PLACEHOLDER)'
    )

    parser.add_argument(
        '--lookback-hours',
        type=int,
        default=int(os.getenv('SCANNER_LOOKBACK_HOURS', '48')),
        help='How far back to look for builds (hours)'
    )

    args = parser.parse_args()

    # Create scanner
    scanner = BuildFailureScanner(
        namespace=args.namespace,
        components_file=args.components_file,
        lookback_hours=args.lookback_hours
    )

    # Run appropriate mode
    if args.mode == 'trigger':
        trigger_mode(scanner)
    elif args.mode == 'daemon':
        daemon_mode(scanner, interval=args.interval)
    elif args.mode == 'component':
        if not args.components:
            console.print("[red]Error: --components required for component mode[/red]")
            sys.exit(1)
        trigger_mode(scanner, components=args.components)


if __name__ == '__main__':
    main()
