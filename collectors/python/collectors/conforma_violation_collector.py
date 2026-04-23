"""Conforma (Enterprise Contract) test failure collector.

For each component with a failing Conforma test:
1. Get the latest failed test PipelineRun from KubeArchive
2. Extract summary (violations/warnings/successes counts)
3. Extract detailed-report (human-readable violation list)
4. Extract report-json (full violation JSON)
5. Insert/update in conforma_results table
"""

import sys
import json
import subprocess
import time
from typing import Dict, Any, Optional, List, Set

import requests

from config import CollectorConfig
from logger import setup_logger
from repositories import DatabaseConnection, ConformaRepository
from clients import KubeArchiveClient
from tekton_parsers import extract_conforma_component_info, extract_verify_taskrun_name

logger = setup_logger(__name__)


class ConformaViolationCollector:
    """Collects Conforma test violation details."""

    def __init__(self, config):
        # type: (CollectorConfig) -> None
        self.config = config
        db = DatabaseConnection(config.db)
        self.conforma_repo = ConformaRepository(db)
        self.kubearchive = KubeArchiveClient(
            api_url=config.k8s.kubearchive_api_url,
            namespace=config.k8s.namespace
        )

    def get_failing_conforma_pipelineruns(self):
        # type: () -> Dict[str, Dict[str, Any]]
        """Get components whose latest Conforma PipelineRun failed."""
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
            url = "{}/apis/tekton.dev/v1/namespaces/{}/pipelineruns".format(
                self.kubearchive.api_url, self.config.k8s.namespace
            )
            params = {
                'labelSelector': 'appstudio.openshift.io/application={},pipelines.appstudio.openshift.io/type=test'.format(
                    self.config.k8s.application_name
                ),
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
            logger.error("KubeArchive error: %s", e)

        # Source 2: Live cluster
        try:
            result = subprocess.run(
                ['oc', 'get', 'pipelinerun', '-n', self.config.k8s.namespace,
                 '-l', 'appstudio.openshift.io/application={},pipelines.appstudio.openshift.io/type=test'.format(
                     self.config.k8s.application_name
                 ),
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

        return {
            comp: info for comp, info in latest_per_component.items()
            if info.get('status') == 'False'
        }

    def get_verify_taskrun(self, pr_data):
        # type: (Dict[str, Any]) -> Optional[str]
        return extract_verify_taskrun_name(pr_data)

    def get_step_logs(self, pod_name, step_name):
        # type: (str, str) -> Optional[str]
        container = "step-{}".format(step_name)
        return self.kubearchive.get_pod_logs(pod_name, container=container)

    def extract_violation_details(self, pr_name, pr_data):
        # type: (str, Dict[str, Any]) -> Dict[str, Any]
        """Extract violation details from Conforma PipelineRun logs."""
        result = {
            'violations_count': 0,
            'warnings_count': 0,
            'successes_count': 0,
            'violation_summary': '',
            'violation_details': None
        }

        verify_tr_name = self.get_verify_taskrun(pr_data)
        if not verify_tr_name:
            logger.warning("No 'verify' TaskRun found")
            return result

        tr_data = self.kubearchive.get_taskrun(verify_tr_name)
        if not tr_data:
            logger.warning("Cannot fetch TaskRun: %s", verify_tr_name)
            return result

        pod_name = tr_data.get('status', {}).get('podName')
        if not pod_name:
            logger.warning("No pod name in TaskRun")
            return result

        summary_logs = self.get_step_logs(pod_name, 'summary')
        if summary_logs:
            try:
                summary = json.loads(summary_logs.strip())
                result['violations_count'] = summary.get('failures', 0)
                result['warnings_count'] = summary.get('warnings', 0)
                result['successes_count'] = summary.get('successes', 0)
                logger.info("Summary: %d violations, %d warnings, %d successes",
                            result['violations_count'], result['warnings_count'], result['successes_count'])
            except json.JSONDecodeError:
                logger.warning("Could not parse summary step")

        detailed_logs = self.get_step_logs(pod_name, 'detailed-report')
        if detailed_logs:
            result['violation_summary'] = detailed_logs[:10000]
            logger.info("Detailed report: %d chars", len(detailed_logs))

        report_logs = self.get_step_logs(pod_name, 'report-json')
        if report_logs:
            try:
                report_json = json.loads(report_logs.strip())
                result['violation_details'] = report_json
                logger.info("Report JSON: %d chars", len(report_logs))
            except json.JSONDecodeError:
                logger.warning("Could not parse report-json step")

        return result

    def get_component_repo_url(self, component_name):
        # type: (str) -> str
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
        repo_url_fallback = self.get_component_repo_url(component_name)
        return extract_conforma_component_info(pr_data, component_name, repo_url_fallback)

    def save_to_db(self, component, scenario, pr_name, pr_uid, violations, comp_info):
        # type: (str, str, str, str, Dict[str, Any], Dict[str, Any]) -> bool
        try:
            result = self.conforma_repo.upsert_violation(
                application=self.config.k8s.application_name,
                component=component, scenario=scenario,
                pr_name=pr_name, pr_uid=pr_uid,
                violations=violations, comp_info=comp_info
            )
            if result:
                logger.info("Saved to DB")
            return result
        except Exception as e:
            logger.error("DB error: %s", e)
            return False

    def resolve_fixed_components(self, currently_failing):
        # type: (Set[str],) -> int
        return self.conforma_repo.resolve_fixed_components(
            self.config.k8s.application_name, currently_failing
        )

    def run(self):
        # type: () -> Dict[str, Any]
        start_time = time.time()

        logger.info("=" * 70)
        logger.info("Conforma Test Failure Collection")
        logger.info("Application: %s", self.config.k8s.application_name)
        logger.info("=" * 70)

        logger.info("Discovering failing Conforma tests...")
        failing = self.get_failing_conforma_pipelineruns()

        if not failing:
            logger.info("No failing Conforma tests found")
            return {'collected': 0, 'resolved': 0, 'duration': time.time() - start_time}

        logger.info("Found %d components with failing Conforma tests", len(failing))

        collected = 0
        for i, (component, info) in enumerate(sorted(failing.items()), 1):
            logger.info("[%d/%d] %s", i, len(failing), component)
            logger.info("Scenario: %s", info['scenario'])
            logger.info("PipelineRun: %s", info['pr_name'])

            violations = self.extract_violation_details(info['pr_name'], info['pr_data'])
            comp_info = self.extract_component_info(info['pr_data'], component)

            if self.save_to_db(component, info['scenario'], info['pr_name'],
                              info['pr_uid'], violations, comp_info):
                collected += 1

            logger.info("")

        resolved = self.resolve_fixed_components(set(failing.keys()))
        duration = time.time() - start_time

        logger.info("=" * 70)
        logger.info("Collection Complete")
        logger.info("=" * 70)
        logger.info("Components collected: %d", collected)
        logger.info("Components resolved: %d", resolved)
        logger.info("Duration: %.1fs", duration)

        return {'collected': collected, 'resolved': resolved, 'duration': duration}
