"""Component status synchronization and cluster status checks.

Provides:
- StatusSynchronizer: marks resolved failures and records successes
- get_failing_build_components: queries cluster for failing push builds
- get_failing_conforma_components: queries cluster for failing Conforma tests
"""

import time
from typing import List, Optional, Dict, Any, Set
from dataclasses import replace

from config import CollectorConfig
from logger import setup_logger
from repositories import DatabaseConnection, BuildFailureRepository
from clients import KubernetesClient
from clients.pipelinerun_query import query_pipelineruns
from models import Component, BuildStatus
from tekton_parsers import classify_build_status

logger = setup_logger(__name__)


# ---------------------------------------------------------------------------
# Cluster query functions
# ---------------------------------------------------------------------------

def get_failing_build_components(config):
    # type: (CollectorConfig) -> Dict[str, Any]
    """Get failing components from KubeArchive + live cluster (push builds only).

    Groups by component, tracks latest push build and latest incoming build
    separately. Returns components whose latest push build failed, and
    identifies which ones have a successful incoming re-trigger.
    """
    label_selector = (
        'appstudio.openshift.io/application={},'
        'pipelines.appstudio.openshift.io/type=build'
    ).format(config.k8s.application_name)

    pipelineruns = query_pipelineruns(config.k8s.namespace, label_selector)
    if not pipelineruns:
        return {'failing': set(), 'retriggered': set(), 'running': {}}

    push_latest = {}
    incoming_latest = {}
    running_builds = {}

    for pr in pipelineruns:
        labels = pr.get('metadata', {}).get('labels', {})
        comp = labels.get('appstudio.openshift.io/component')
        if not comp:
            continue
        event_type = labels.get('pipelinesascode.tekton.dev/event-type', '')
        ts = pr.get('metadata', {}).get('creationTimestamp', '')
        pr_name = pr.get('metadata', {}).get('name', '')
        conditions = pr.get('status', {}).get('conditions', [])

        if not conditions:
            if event_type in ('push', 'incoming'):
                if comp not in running_builds or ts > running_builds[comp][0]:
                    running_builds[comp] = (ts, pr_name)
            continue

        c = conditions[-1]
        status = c.get('status')
        reason = c.get('reason')

        if status == 'Unknown':
            if event_type in ('push', 'incoming'):
                if comp not in running_builds or ts > running_builds[comp][0]:
                    running_builds[comp] = (ts, pr_name)
            continue

        if event_type == 'push':
            if comp not in push_latest or ts > push_latest[comp][0]:
                push_latest[comp] = (ts, status, reason)
        elif event_type == 'incoming':
            if comp not in incoming_latest or ts > incoming_latest[comp][0]:
                incoming_latest[comp] = (ts, status, reason)

    failing = set()
    retriggered = set()
    for comp, (push_ts, push_status, push_reason) in push_latest.items():
        if push_status == 'False':
            failing.add(comp)
            if comp in incoming_latest:
                inc_ts, inc_status, inc_reason = incoming_latest[comp]
                if inc_status == 'True' and inc_ts > push_ts:
                    retriggered.add(comp)

    active_running = {}
    for comp, (run_ts, run_pr) in running_builds.items():
        is_newer = True
        if comp in push_latest and run_ts <= push_latest[comp][0]:
            is_newer = False
        if comp in incoming_latest and run_ts <= incoming_latest[comp][0]:
            is_newer = False
        if is_newer:
            active_running[comp] = {'timestamp': run_ts, 'pipelinerun_name': run_pr, 'type': 'build'}

    return {'failing': failing, 'retriggered': retriggered, 'running': active_running}


def get_failing_conforma_components(config):
    # type: (CollectorConfig) -> Dict[str, Any]
    """Query KubeArchive + live cluster for Conforma test PipelineRuns.

    Returns dict with:
      - failing: set of component names with latest conforma test failed
      - details: dict of component -> {scenario, pr_name, pr_uid, timestamp}
      - running: dict of component -> running test info
    """
    label_selector = (
        'appstudio.openshift.io/application={},'
        'pipelines.appstudio.openshift.io/type=test'
    ).format(config.k8s.application_name)

    pipelineruns = query_pipelineruns(config.k8s.namespace, label_selector)
    if not pipelineruns:
        return {'failing': set(), 'details': {}, 'running': {}}

    latest_per_component = {}
    running_conforma = {}

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
        pr_name = pr.get('metadata', {}).get('name', '')
        pr_uid = pr.get('metadata', {}).get('uid', '')
        conditions = pr.get('status', {}).get('conditions', [])

        if not conditions:
            if comp not in running_conforma or ts > running_conforma[comp][0]:
                running_conforma[comp] = (ts, pr_name, scenario)
            continue

        c = conditions[-1]
        status = c.get('status')

        if status == 'Unknown':
            if comp not in running_conforma or ts > running_conforma[comp][0]:
                running_conforma[comp] = (ts, pr_name, scenario)
            continue

        if comp not in latest_per_component or ts > latest_per_component[comp][0]:
            latest_per_component[comp] = (ts, pr_name, pr_uid, status, scenario)

    failing = set()
    details = {}
    for comp, (ts, pr_name, pr_uid, status, scenario) in latest_per_component.items():
        if status == 'False':
            failing.add(comp)
            details[comp] = {
                'scenario': scenario,
                'pipelinerun_name': pr_name,
                'pipelinerun_uid': pr_uid,
                'timestamp': ts
            }

    active_running = {}
    for comp, (run_ts, run_pr, run_scenario) in running_conforma.items():
        if comp not in latest_per_component or run_ts > latest_per_component[comp][0]:
            active_running[comp] = {
                'timestamp': run_ts,
                'pipelinerun_name': run_pr,
                'scenario': run_scenario,
                'type': 'conforma'
            }

    return {'failing': failing, 'details': details, 'running': active_running}


# ---------------------------------------------------------------------------
# StatusSynchronizer class
# ---------------------------------------------------------------------------

class StatusSynchronizer:
    """Synchronizes component status between Kubernetes and database."""

    def __init__(self, config, db=None, build_repo=None, k8s=None):
        # type: (CollectorConfig, ...) -> None
        self.config = config
        if db is None:
            db = DatabaseConnection(config.db)
        self.build_repo = build_repo or BuildFailureRepository(db)
        self.k8s = k8s or KubernetesClient(namespace=config.k8s.namespace)

    def get_component_metadata(self, component_name):
        # type: (str,) -> Optional[Component]
        meta = self.k8s.get_component_metadata(component_name)
        if not meta:
            return None
        return Component(
            name=component_name,
            repository_url=meta['repository_url'],
            branch=meta['branch'],
            namespace=self.config.k8s.namespace
        )

    def get_current_status(self, component_name):
        # type: (str,) -> Optional[Dict[str, Any]]
        """Get current push build status from live cluster + KubeArchive."""
        label_selector = (
            'appstudio.openshift.io/component={},'
            'pipelines.appstudio.openshift.io/type=build'
        ).format(component_name)

        pipelineruns = query_pipelineruns(
            self.config.k8s.namespace, label_selector,
            max_pages=1, page_size=50,
        )

        latest = None
        for pr in pipelineruns:
            labels = pr.get('metadata', {}).get('labels', {})
            if labels.get('pipelinesascode.tekton.dev/event-type', '') != 'push':
                continue
            conditions = pr.get('status', {}).get('conditions', [])
            if not conditions:
                continue
            ts = pr.get('metadata', {}).get('creationTimestamp', '')
            if not latest or ts > latest[0]:
                latest = (
                    ts,
                    pr.get('metadata', {}).get('name'),
                    pr.get('metadata', {}).get('uid'),
                    conditions[-1].get('reason', ''),
                )

        if not latest:
            return None

        ts, name, uid, reason = latest
        status = classify_build_status(reason)
        return {'name': name, 'uid': uid, 'status': status}

    def sync_component(self, component):
        # type: (Component,) -> Dict[str, Any]
        app = self.config.k8s.application_name
        ns = self.config.k8s.namespace
        result = {
            'component': component.name,
            'was_failing': False,
            'now_resolved': False,
            'success_recorded': False,
            'current_status': None
        }

        result['was_failing'] = self.build_repo.count_unresolved(component.name, app) > 0

        current = self.get_current_status(component.name)

        if not current:
            if result['was_failing']:
                if self.build_repo.mark_resolved(component.name, app, ns, 'archived'):
                    result['now_resolved'] = True
                    logger.info("Marked as resolved (no PipelineRuns in cluster - likely archived)")
                    result['current_status'] = 'archived'
                    return result

            db_status = self.build_repo.get_last_status(component.name, app)
            result['current_status'] = db_status if db_status else 'unknown'
            logger.info("Current status: %s (from DB, not in K8s)", result['current_status'])
            return result

        result['current_status'] = current['status'].value

        if result['was_failing'] and current['status'] == BuildStatus.SUCCEEDED:
            if self.build_repo.mark_resolved(component.name, app, ns, current['name']):
                result['now_resolved'] = True
                logger.info("Marked as resolved (last build: %s)", current['name'])

            if self.build_repo.record_successful_build(
                component.name, current['name'], current['uid'],
                app, ns, component.repository_url, component.branch
            ):
                result['success_recorded'] = True
                logger.info("Recorded success build")

        elif not result['was_failing'] and current['status'] == BuildStatus.SUCCEEDED:
            if self.build_repo.record_successful_build(
                component.name, current['name'], current['uid'],
                app, ns, component.repository_url, component.branch
            ):
                result['success_recorded'] = True
                logger.info("Recorded success build (no previous failures)")

        elif current['status'] == BuildStatus.FAILED:
            logger.info("Current status: Failed (will be collected by comprehensive collector)")

        return result

    def run(self, components=None):
        # type: (Optional[List[Component]],) -> Dict[str, Any]
        start_time = time.time()
        app = self.config.k8s.application_name

        if components is None:
            logger.info("Loading components with unresolved failures from database...")
            unresolved_names = self.build_repo.find_unresolved_component_names(app)

            if not unresolved_names:
                logger.info("No unresolved failures found")
                return {
                    'components_synced': 0, 'resolved': 0,
                    'successes': 0, 'duration': time.time() - start_time
                }

            logger.info("Found %d components with unresolved failures", len(unresolved_names))

            components = [
                Component(name=name, namespace=self.config.k8s.namespace,
                          repository_url='', branch='')
                for name in unresolved_names
            ]

        logger.info("Enriching component metadata...")
        components = [
            comp if comp.repository_url else replace(
                comp, **vars(self.get_component_metadata(comp.name) or comp)
            )
            for comp in components
        ]

        logger.info("=" * 70)
        logger.info("Component Status Synchronization")
        logger.info("=" * 70)

        total_resolved = 0
        total_successes = 0
        components_synced = 0

        for i, component in enumerate(components, 1):
            logger.info("[%d/%d] %s", i, len(components), component.name)

            if not component.repository_url:
                logger.warning("Component CR not found in cluster — syncing status anyway")

            result = self.sync_component(component)
            components_synced += 1

            if result['now_resolved']:
                total_resolved += 1
            if result['success_recorded']:
                total_successes += 1

            if not result['now_resolved'] and not result['success_recorded']:
                status = result['current_status']
                was_failing = "was failing, " if result['was_failing'] else ""
                logger.info("%scurrent status: %s", was_failing, status)

        duration = time.time() - start_time

        logger.info("=" * 70)
        logger.info("Synchronization Complete")
        logger.info("=" * 70)
        logger.info("Components synced: %d", components_synced)
        logger.info("Failures resolved: %d", total_resolved)
        logger.info("Successes recorded: %d", total_successes)
        logger.info("Duration: %.1fs", duration)

        return {
            'components_synced': components_synced,
            'resolved': total_resolved,
            'successes': total_successes,
            'duration': duration
        }
