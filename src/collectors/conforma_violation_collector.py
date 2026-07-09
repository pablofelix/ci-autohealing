"""Conforma (Enterprise Contract) test failure collector.

For each component with a failing Conforma test:
1. Get the latest failed test PipelineRun from KubeArchive
2. Extract summary (violations/warnings/successes counts)
3. Extract detailed-report (human-readable violation list)
4. Extract report-json (full violation JSON)
5. Insert/update in conforma_results table
"""

import json
import time

from clients import KubeArchiveClient, KubernetesClient, TektonResultsClient
from clients.pipelinerun_query import query_pipelineruns
from logger import setup_logger
from repositories import ConformaRepository, DatabaseConnection
from tekton_parsers import extract_conforma_component_info, extract_verify_taskrun_name

logger = setup_logger(__name__)


class ConformaViolationCollector:
    """Collects Conforma test violation details."""

    def __init__(self, config, db=None, conforma_repo=None, kubearchive=None, k8s=None,
                 tekton_results=None):
        self.config = config
        if db is None:
            db = DatabaseConnection(config.db)
        self.conforma_repo = conforma_repo or ConformaRepository(db)
        self.kubearchive = kubearchive or KubeArchiveClient(
            api_url=config.k8s.kubearchive_api_url,
            namespace=config.k8s.namespace
        )
        self.k8s = k8s or KubernetesClient(namespace=config.k8s.namespace)
        self.tekton_results = tekton_results or TektonResultsClient(namespace=config.k8s.namespace)

    def _collect_conforma_latest(self, pipelineruns):
        """Build a map of (component, scenario) -> latest conforma PipelineRun info."""
        latest_per_key = {}
        for pr in pipelineruns:
            labels = pr.get('metadata', {}).get('labels', {})
            comp = labels.get('appstudio.openshift.io/component')
            if not comp:
                continue
            scenario = labels.get('test.appstudio.openshift.io/scenario', '')
            if not scenario.startswith('conforma-'):
                continue
            pipeline_type = labels.get('pipelines.appstudio.openshift.io/type', '')
            if pipeline_type != 'test':
                continue

            ts = pr.get('metadata', {}).get('creationTimestamp', '')
            conditions = pr.get('status', {}).get('conditions', [])
            if not conditions:
                continue

            status = conditions[-1].get('status')
            if status == 'Unknown':
                continue

            key = (comp, scenario)
            if key not in latest_per_key or ts > latest_per_key[key]['timestamp']:
                latest_per_key[key] = {
                    'pr_name': pr.get('metadata', {}).get('name', ''),
                    'pr_uid': pr.get('metadata', {}).get('uid', ''),
                    'component': comp,
                    'scenario': scenario,
                    'timestamp': ts,
                    'status': status,
                    'pr_data': pr
                }
        return latest_per_key

    def get_conforma_pipelineruns(self):
        """Get latest Conforma PipelineRun status per (component, scenario).

        Queries both KubeArchive/cluster and Tekton Results, merging by
        (component, scenario) key and keeping the newest per pair.
        """
        label_selector = (
            'appstudio.openshift.io/application={},'
            'pipelines.appstudio.openshift.io/type=test'
        ).format(self.config.k8s.application_name)

        pipelineruns = query_pipelineruns(
            self.config.k8s.namespace, label_selector,
            kubearchive_url=self.kubearchive.api_url,
            session=self.kubearchive.session,
        )

        latest_per_key = self._collect_conforma_latest(pipelineruns)

        # Merge Tekton Results (catches test PipelineRuns that expired from KubeArchive)
        try:
            tr_pipelineruns = self.tekton_results.query_conforma_records(
                application=self.config.k8s.application_name,
                page_size=100
            )
            if tr_pipelineruns:
                tr_latest = self._collect_conforma_latest(tr_pipelineruns)
                added = 0
                for key, info in tr_latest.items():
                    if key not in latest_per_key or info['timestamp'] > latest_per_key[key]['timestamp']:
                        latest_per_key[key] = info
                        added += 1
                if added:
                    logger.info("Tekton Results added/updated %d conforma pair(s)", added)
        except Exception as e:
            logger.debug("Tekton Results conforma query skipped: %s", e)

        return latest_per_key

    def get_verify_taskrun(self, pr_data):
        return extract_verify_taskrun_name(pr_data)

    def get_step_logs(self, pod_name, step_name):
        container = "step-{}".format(step_name)
        return self.kubearchive.get_pod_logs(pod_name, container=container)

    def extract_violation_details(self, pr_name, pr_data):
        """Extract violation details from Conforma PipelineRun logs.

        Tries KubeArchive first, falls back to Tekton Results for logs.
        """
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

        pod_name = None
        if tr_data:
            pod_name = tr_data.get('status', {}).get('podName')

        if pod_name:
            summary_logs = self.get_step_logs(pod_name, 'summary')
            detailed_logs = self.get_step_logs(pod_name, 'detailed-report')
            report_logs = self.get_step_logs(pod_name, 'report-json')
        else:
            logger.info("KubeArchive TaskRun unavailable, trying Tekton Results")
            summary_logs = None
            detailed_logs = None
            report_logs = None

            # Fallback: fetch all logs from Tekton Results and parse sections
            try:
                tr_logs = self.tekton_results.get_pipelinerun_logs(pr_name, max_log_size=200000)
                if tr_logs:
                    summary_logs = self._extract_step_section(tr_logs, 'summary')
                    detailed_logs = self._extract_step_section(tr_logs, 'detailed-report')
                    report_logs = self._extract_step_section(tr_logs, 'report-json')
                    if summary_logs or detailed_logs:
                        logger.info("Got conforma logs from Tekton Results")
            except Exception as e:
                logger.debug("Tekton Results logs fallback failed: %s", e)

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

        if detailed_logs:
            result['violation_summary'] = detailed_logs[:100000]
            logger.info("Detailed report: %d chars", len(detailed_logs))

        if report_logs:
            try:
                report_json = json.loads(report_logs.strip())
                result['violation_details'] = report_json
                logger.info("Report JSON: %d chars", len(report_logs))
            except json.JSONDecodeError:
                logger.warning("Could not parse report-json step")

        return result

    @staticmethod
    def _extract_step_section(logs, step_name):
        """Extract a specific step's output from combined TaskRun logs."""
        import re
        pattern = r'===== TaskRun: [^=]*/ Step: {step}\b[^=]*=====(.*?)(?=\n===== |$)'.format(
            step=re.escape(step_name)
        )
        match = re.search(pattern, logs, re.DOTALL)
        return match.group(1).strip() if match else None

    def get_component_repo_url(self, component_name):
        meta = self.k8s.get_component_metadata(component_name)
        return meta['repository_url'] if meta else ''

    def extract_component_info(self, pr_data, component_name):
        repo_url_fallback = self.get_component_repo_url(component_name)
        return extract_conforma_component_info(pr_data, component_name, repo_url_fallback)

    def _resolve_trigger_type(self, snapshot_name):
        """Look up the Snapshot CR to determine whether this build was scheduled.

        Reads the ``pac.test.appstudio.openshift.io/original-prname`` label:
          - ends with ``-on-schedule`` → 'scheduled' (nightly; FIPS runs)
          - ends with ``-on-push``     → 'push'
          - unreachable / unknown      → 'push'  (safe default)

        One Kubernetes API call per new conforma result — only happens at
        collection time, not on every display.
        """
        if not snapshot_name:
            return 'push'
        try:
            snapshot = self.k8s.get_snapshot(snapshot_name)
            trigger_type = self.k8s.extract_trigger_type(snapshot)
            logger.debug("Snapshot %s → trigger_type=%s", snapshot_name, trigger_type)
            return trigger_type
        except Exception as exc:
            logger.debug("Could not resolve trigger_type for %s: %s", snapshot_name, exc)
            return 'push'

    def save_to_db(self, component, scenario, pr_name, pr_uid, violations, comp_info):
        """Save violation to DB. Detects future scenarios and scheduled builds."""
        try:
            is_future = '-future' in scenario
            if is_future:
                logger.info("Future scenario detected (informational, non-blocking)")

            snapshot_name = comp_info.get('snapshot_name', '')
            trigger_type = self._resolve_trigger_type(snapshot_name)
            if trigger_type == 'scheduled':
                logger.info("Scheduled (nightly) build detected — FIPS check runs")

            result = self.conforma_repo.upsert_violation(
                application=self.config.k8s.application_name,
                component=component, scenario=scenario,
                pr_name=pr_name, pr_uid=pr_uid,
                violations=violations, comp_info=comp_info,
                is_future=is_future,
                trigger_type=trigger_type,
            )
            if result:
                logger.info("Saved to DB")
            return result
        except Exception as e:
            logger.error("DB error: %s", e)
            return False

    def get_conforma_history(self, component_name, limit=10):
        """Get conforma test history for a component from Tekton Results.

        Returns list of dicts with: name, timestamp, scenario, status, violations_count.
        """
        try:
            records = self.tekton_results.query_conforma_records(
                application=self.config.k8s.application_name,
                component=component_name,
                page_size=limit
            )
        except Exception as e:
            logger.debug("Conforma history query failed for %s: %s", component_name, e)
            return []

        results = []
        for pr in records:
            metadata = pr.get('metadata', {})
            labels = metadata.get('labels', {})
            scenario = labels.get('test.appstudio.openshift.io/scenario', '')
            if not scenario.startswith('conforma-'):
                continue

            conditions = pr.get('status', {}).get('conditions', [])
            if not conditions:
                continue

            last_condition = conditions[-1]
            status_flag = last_condition.get('status')
            if status_flag == 'Unknown':
                continue

            status = 'Passed' if status_flag == 'True' else 'Failed'

            results.append({
                'name': metadata.get('name', ''),
                'timestamp': metadata.get('creationTimestamp', ''),
                'scenario': scenario,
                'status': status,
            })

        return results

    def resolve_fixed_components(self, currently_failing, all_seen):
        return self.conforma_repo.resolve_fixed_components(
            self.config.k8s.application_name, currently_failing, all_seen
        )

    def _resolve_deleted_components(self):
        """Auto-resolve conforma violations for components deleted from the cluster."""
        app = self.config.k8s.application_name
        unresolved = self.conforma_repo.find_unresolved_component_names(app)
        resolved_count = 0
        for comp in unresolved:
            if self.k8s.get_component_metadata(comp) is None:
                count = self.conforma_repo.resolve_deleted_component(comp, app)
                if count:
                    resolved_count += count
                    logger.info("Component %s deleted from cluster — resolved %d conforma violations", comp, count)
        return resolved_count

    def run(self):
        start_time = time.time()

        logger.info("=" * 70)
        logger.info("Conforma Test Failure Collection")
        logger.info("Application: %s", self.config.k8s.application_name)
        logger.info("=" * 70)

        logger.info("Discovering failing Conforma tests...")
        all_results = self.get_conforma_pipelineruns()
        failing = {
            key: info for key, info in all_results.items()
            if info.get('status') == 'False'
        }
        all_seen = set(all_results.keys())

        if not failing:
            logger.info("No failing Conforma tests found")
            resolved = self.resolve_fixed_components(set(), all_seen) if all_seen else 0
            return {'collected': 0, 'resolved': resolved, 'duration': time.time() - start_time}

        logger.info("Found %d component/scenario pairs with failing Conforma tests", len(failing))

        collected = 0
        for i, (_key, info) in enumerate(sorted(failing.items()), 1):
            component = info['component']
            logger.info("[%d/%d] %s", i, len(failing), component)
            logger.info("Scenario: %s", info['scenario'])
            logger.info("PipelineRun: %s", info['pr_name'])

            violations = self.extract_violation_details(info['pr_name'], info['pr_data'])
            comp_info = self.extract_component_info(info['pr_data'], component)

            if self.save_to_db(component, info['scenario'], info['pr_name'],
                              info['pr_uid'], violations, comp_info):
                collected += 1

            logger.info("")

        resolved = self.resolve_fixed_components(set(failing.keys()), all_seen)

        deleted_resolved = self._resolve_deleted_components()
        resolved += deleted_resolved

        duration = time.time() - start_time

        logger.info("=" * 70)
        logger.info("Collection Complete")
        logger.info("=" * 70)
        logger.info("Components collected: %d", collected)
        logger.info("Components resolved: %d (includes %d deleted)", resolved, deleted_resolved)
        logger.info("Duration: %.1fs", duration)

        return {'collected': collected, 'resolved': resolved, 'duration': duration}
