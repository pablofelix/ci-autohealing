#!/usr/bin/env python3
"""
Check synchronization status between Konflux and local database.
Returns JSON with sync status and missing/extra components.

Uses KubeArchive API to get archived PipelineRuns (live cluster only
retains a few recent ones, while KubeArchive has the full history).
"""

import sys
import json
import subprocess
from typing import Set, Dict, Any, Optional

import requests

from config import CollectorConfig
from repositories import DatabaseConnection, BuildFailureRepository, SyncStatusRepository
from openshift_auth import is_logged_in, get_openshift_token, discover_kubearchive_api_url, create_authenticated_session


def get_failing_components_from_cluster(config):
    # type: (CollectorConfig) -> Dict[str, Any]
    """Get failing components from KubeArchive + live cluster (push builds only).

    Queries KubeArchive for archived PipelineRuns and live cluster.
    Groups by component, tracks latest push build and latest incoming build
    separately. Returns components whose latest push build failed, and
    identifies which ones have a successful incoming re-trigger.
    """
    token = get_openshift_token()
    if not token:
        return {'failing': set(), 'retriggered': set()}

    push_latest = {}
    incoming_latest = {}
    running_builds = {}

    def process_pipelinerun(pr):
        labels = pr.get('metadata', {}).get('labels', {})
        comp = labels.get('appstudio.openshift.io/component')
        if not comp:
            return
        event_type = labels.get('pipelinesascode.tekton.dev/event-type', '')
        ts = pr.get('metadata', {}).get('creationTimestamp', '')
        pr_name = pr.get('metadata', {}).get('name', '')
        conditions = pr.get('status', {}).get('conditions', [])

        if not conditions:
            if event_type in ('push', 'incoming'):
                if comp not in running_builds or ts > running_builds[comp][0]:
                    running_builds[comp] = (ts, pr_name)
            return

        c = conditions[-1]
        status = c.get('status')
        reason = c.get('reason')

        if status == 'Unknown':
            if event_type in ('push', 'incoming'):
                if comp not in running_builds or ts > running_builds[comp][0]:
                    running_builds[comp] = (ts, pr_name)
            return

        if event_type == 'push':
            if comp not in push_latest or ts > push_latest[comp][0]:
                push_latest[comp] = (ts, status, reason)
        elif event_type == 'incoming':
            if comp not in incoming_latest or ts > incoming_latest[comp][0]:
                incoming_latest[comp] = (ts, status, reason)

    namespace = config.k8s.namespace
    application_name = config.k8s.application_name

    # Source 1: KubeArchive (has archived PipelineRuns)
    try:
        api_url = discover_kubearchive_api_url()
        session = create_authenticated_session(token)

        url = "{}/apis/tekton.dev/v1/namespaces/{}/pipelineruns".format(api_url, namespace)
        params = {
            'labelSelector': 'appstudio.openshift.io/application={},pipelines.appstudio.openshift.io/type=build'.format(application_name),
            'limit': 500
        }

        for _ in range(3):
            resp = session.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                break
            data = resp.json()
            for pr in data.get('items', []):
                process_pipelinerun(pr)
            cont = data.get('metadata', {}).get('continue')
            if cont:
                params['continue'] = cont
            else:
                break
    except Exception:
        pass

    # Source 2: Live cluster PipelineRuns
    try:
        result = subprocess.run(
            ['oc', 'get', 'pipelinerun',
             '-n', namespace,
             '-l', 'appstudio.openshift.io/application={},pipelines.appstudio.openshift.io/type=build'.format(application_name),
             '-o', 'json'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=15
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            for pr in data.get('items', []):
                process_pipelinerun(pr)
    except Exception:
        pass

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


def main():
    """Check sync status and return JSON."""
    import time
    start_time = time.time()

    config = CollectorConfig.from_env()
    db = DatabaseConnection(config.db)
    build_repo = BuildFailureRepository(db)
    sync_repo = SyncStatusRepository(db)
    application_name = config.k8s.application_name

    status = {
        'cluster_connected': False,
        'in_sync': False,
        'cluster_components': [],
        'db_components': [],
        'missing_in_db': [],
        'extra_in_db': [],
        'retriggered_components': [],
        'running_builds': [],
        'error': None
    }

    if not is_logged_in():
        status['error'] = 'Not logged into OpenShift cluster'
        sync_repo.save_build_sync_status(application_name, status, time.time() - start_time)
        print(json.dumps(status))
        sys.exit(0)

    status['cluster_connected'] = True

    db_components = build_repo.find_failing_component_names(application_name)
    if db_components is None:
        status['error'] = 'Database connection failed'
        sync_repo.save_build_sync_status(application_name, status, time.time() - start_time)
        print(json.dumps(status))
        sys.exit(0)

    cluster_result = get_failing_components_from_cluster(config)
    cluster_components = cluster_result['failing']
    retriggered = cluster_result['retriggered']
    running = cluster_result.get('running', {})

    missing_in_db = cluster_components - db_components
    extra_in_db = db_components - cluster_components

    status['cluster_components'] = sorted(cluster_components)
    status['db_components'] = sorted(db_components)
    status['missing_in_db'] = sorted(missing_in_db)
    status['extra_in_db'] = sorted(extra_in_db)
    status['retriggered_components'] = sorted(retriggered)
    status['running_builds'] = [
        {'component': comp, **info} for comp, info in sorted(running.items())
    ]
    status['in_sync'] = len(missing_in_db) == 0 and len(extra_in_db) == 0

    sync_repo.save_build_sync_status(application_name, status, time.time() - start_time)

    print(json.dumps(status))


if __name__ == '__main__':
    main()
