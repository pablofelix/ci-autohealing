#!/usr/bin/env python3
"""Collect Conforma (Enterprise Contract) test failure details.

For each component with a failing Conforma test:
1. Get the latest failed test PipelineRun from KubeArchive
2. Extract summary (violations/warnings/successes counts)
3. Extract detailed-report (human-readable violation list)
4. Extract report-json (full violation JSON)
5. Insert/update in conforma_results table
"""

import os
import sys
import json
import subprocess
import time
from typing import Dict, Any, Optional, List, Set

import requests

from config import CollectorConfig
from repositories import DatabaseConnection, ConformaRepository
from clients import KubeArchiveClient
from tekton_parsers import extract_conforma_component_info, extract_verify_taskrun_name


APPLICATION_NAME = os.getenv('APPLICATION_NAME', 'acme-v2-0')
NAMESPACE = os.getenv('NAMESPACE', 'NAMESPACE_PLACEHOLDER')


class ConformaCollector:

    def __init__(self, config: CollectorConfig):
        self.config = config
        db = DatabaseConnection(config.db)
        self.conforma_repo = ConformaRepository(db)
        self.kubearchive = KubeArchiveClient(
            api_url=config.k8s.kubearchive_api_url,
            namespace=config.k8s.namespace
        )

    def get_failing_conforma_pipelineruns(self) -> Dict[str, Dict[str, Any]]:
        """Get components whose latest Conforma PipelineRun failed.

        Tracks the latest PipelineRun per component regardless of status,
        then returns only those whose latest result is a failure.

        Returns:
            Dict of component_name -> {pr_name, pr_uid, scenario, timestamp, pr_data}
        """
        latest_per_component = {}

        def process_pr(pr):
            labels = pr.get('metadata', {}).get('labels', {})
            comp = labels.get('appstudio.openshift.io/component')
            if not comp:
                return
            scenario = labels.get('test.appstudio.openshift.io/scenario', '')
            if not scenario.startswith('conforma-'):
                return
            pipeline_type = labels.get('pipelines.appstudio.openshift.io/type', '')
            if pipeline_type != 'test':
                return

            ts = pr.get('metadata', {}).get('creationTimestamp', '')
            conditions = pr.get('status', {}).get('conditions', [])
            if not conditions:
                return

            status = conditions[-1].get('status')
            if status == 'Unknown':
                return

            if comp not in latest_per_component or ts > latest_per_component[comp]['timestamp']:
                latest_per_component[comp] = {
                    'pr_name': pr.get('metadata', {}).get('name', ''),
                    'pr_uid': pr.get('metadata', {}).get('uid', ''),
                    'scenario': scenario,
                    'timestamp': ts,
                    'status': status,
                    'pr_data': pr
                }

        # Source 1: KubeArchive
        try:
            url = f"{self.kubearchive.api_url}/apis/tekton.dev/v1/namespaces/{self.config.k8s.namespace}/pipelineruns"
            params = {
                'labelSelector': f'appstudio.openshift.io/application={self.config.k8s.application_name},pipelines.appstudio.openshift.io/type=test',
                'limit': 500
            }

            for _ in range(3):
                resp = self.kubearchive.session.get(url, params=params, timeout=30)
                if resp.status_code != 200:
                    break
                data = resp.json()
                for pr in data.get('items', []):
                    process_pr(pr)
                cont = data.get('metadata', {}).get('continue')
                if cont:
                    params['continue'] = cont
                else:
                    break
        except Exception as e:
            print(f"  KubeArchive error: {e}", file=sys.stderr)

        # Source 2: Live cluster
        try:
            result = subprocess.run(
                ['oc', 'get', 'pipelinerun', '-n', self.config.k8s.namespace,
                 '-l', f'appstudio.openshift.io/application={self.config.k8s.application_name},pipelines.appstudio.openshift.io/type=test',
                 '-o', 'json'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=15
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                for pr in data.get('items', []):
                    process_pr(pr)
        except Exception:
            pass

        # Only return components whose latest PipelineRun failed
        return {
            comp: info for comp, info in latest_per_component.items()
            if info.get('status') == 'False'
        }

    def get_verify_taskrun(self, pr_data):
        # type: (Dict[str, Any]) -> Optional[str]
        """Find the 'verify' TaskRun name from PipelineRun childReferences."""
        return extract_verify_taskrun_name(pr_data)

    def get_step_logs(self, pod_name: str, step_name: str) -> Optional[str]:
        """Fetch logs for a specific step from KubeArchive."""
        container = f"step-{step_name}"
        return self.kubearchive.get_pod_logs(pod_name, container=container)

    def extract_violation_details(self, pr_name: str, pr_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract violation details from Conforma PipelineRun logs.

        Returns dict with: violations_count, warnings_count, successes_count,
        violation_summary, violation_details
        """
        result = {
            'violations_count': 0,
            'warnings_count': 0,
            'successes_count': 0,
            'violation_summary': '',
            'violation_details': None
        }

        # Find verify TaskRun
        verify_tr_name = self.get_verify_taskrun(pr_data)
        if not verify_tr_name:
            print(f"    ⚠ No 'verify' TaskRun found")
            return result

        # Get TaskRun to find pod name
        tr_data = self.kubearchive.get_taskrun(verify_tr_name)
        if not tr_data:
            print(f"    ⚠ Cannot fetch TaskRun: {verify_tr_name}")
            return result

        pod_name = tr_data.get('status', {}).get('podName')
        if not pod_name:
            print(f"    ⚠ No pod name in TaskRun")
            return result

        # Step 1: summary step — compact JSON with counts
        summary_logs = self.get_step_logs(pod_name, 'summary')
        if summary_logs:
            try:
                summary = json.loads(summary_logs.strip())
                result['violations_count'] = summary.get('failures', 0)
                result['warnings_count'] = summary.get('warnings', 0)
                result['successes_count'] = summary.get('successes', 0)
                print(f"    ✓ Summary: {result['violations_count']} violations, {result['warnings_count']} warnings, {result['successes_count']} successes")
            except json.JSONDecodeError:
                print(f"    ⚠ Could not parse summary step")

        # Step 2: detailed-report step — human-readable violations
        detailed_logs = self.get_step_logs(pod_name, 'detailed-report')
        if detailed_logs:
            result['violation_summary'] = detailed_logs[:10000]
            print(f"    ✓ Detailed report: {len(detailed_logs)} chars")

        # Step 3: report-json step — full JSON
        report_logs = self.get_step_logs(pod_name, 'report-json')
        if report_logs:
            try:
                report_json = json.loads(report_logs.strip())
                result['violation_details'] = report_json
                print(f"    ✓ Report JSON: {len(report_logs)} chars")
            except json.JSONDecodeError:
                print(f"    ⚠ Could not parse report-json step")

        return result

    def get_component_repo_url(self, component_name: str) -> str:
        """Look up repository URL from the Kubernetes component resource."""
        try:
            result = subprocess.run(
                ['oc', 'get', 'component', component_name,
                 '-n', self.config.k8s.namespace,
                 '-o', 'jsonpath={.spec.source.git.url}'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return ''

    def extract_component_info(self, pr_data, component_name):
        # type: (Dict[str, Any], str) -> Dict[str, Any]
        """Extract snapshot, image, repo, commit from PipelineRun."""
        repo_url_fallback = self.get_component_repo_url(component_name)
        return extract_conforma_component_info(pr_data, component_name, repo_url_fallback)

    def save_to_db(self, component, scenario, pr_name, pr_uid, violations, comp_info):
        # type: (str, str, str, str, Dict[str, Any], Dict[str, Any]) -> bool
        """Save Conforma result to database."""
        try:
            result = self.conforma_repo.upsert_violation(
                application=self.config.k8s.application_name,
                component=component, scenario=scenario,
                pr_name=pr_name, pr_uid=pr_uid,
                violations=violations, comp_info=comp_info
            )
            if result:
                print("    ✓ Saved to DB")
            return result
        except Exception as e:
            print("    ✗ DB error: {}".format(e))
            return False

    def resolve_fixed_components(self, currently_failing):
        # type: (Set[str],) -> int
        """Mark components that are no longer failing as resolved."""
        return self.conforma_repo.resolve_fixed_components(
            self.config.k8s.application_name, currently_failing
        )

    def run(self) -> Dict[str, Any]:
        start_time = time.time()

        print(f"\n{'='*70}")
        print("Conforma Test Failure Collection")
        print(f"Application: {self.config.k8s.application_name}")
        print(f"{'='*70}\n")

        # Get failing components
        print("Discovering failing Conforma tests...")
        failing = self.get_failing_conforma_pipelineruns()

        if not failing:
            print("  ✓ No failing Conforma tests found")
            return {'collected': 0, 'resolved': 0, 'duration': time.time() - start_time}

        print(f"  ✓ Found {len(failing)} components with failing Conforma tests\n")

        collected = 0
        for i, (component, info) in enumerate(sorted(failing.items()), 1):
            print(f"[{i}/{len(failing)}] {component}")
            print(f"  Scenario: {info['scenario']}")
            print(f"  PipelineRun: {info['pr_name']}")

            # Extract violation details from logs
            violations = self.extract_violation_details(info['pr_name'], info['pr_data'])

            # Extract component info (image, repo, commit)
            comp_info = self.extract_component_info(info['pr_data'], component)

            # Save to DB
            if self.save_to_db(component, info['scenario'], info['pr_name'],
                              info['pr_uid'], violations, comp_info):
                collected += 1

            print()

        # Mark resolved components
        resolved = self.resolve_fixed_components(set(failing.keys()))

        duration = time.time() - start_time

        print(f"{'='*70}")
        print("Collection Complete")
        print(f"{'='*70}")
        print(f"Components collected: {collected}")
        print(f"Components resolved: {resolved}")
        print(f"Duration: {duration:.1f}s")
        print()

        return {'collected': collected, 'resolved': resolved, 'duration': duration}


def main():
    config = CollectorConfig.from_env()
    collector = ConformaCollector(config)
    collector.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
