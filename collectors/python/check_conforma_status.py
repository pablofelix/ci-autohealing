#!/usr/bin/env python3
"""Check Conforma (Enterprise Contract) test status from KubeArchive.

Queries KubeArchive for integration test PipelineRuns with conforma-* scenarios,
identifies components whose latest test failed, and caches results in sync_status.
"""

import sys
import json
import subprocess
from typing import Dict, Any, Optional, Set

import requests

from config import CollectorConfig
from database import Database
from openshift_auth import get_openshift_token, discover_kubearchive_api_url, create_authenticated_session


def get_failing_conforma_components(config):
    # type: (CollectorConfig) -> Dict[str, Any]
    """Query KubeArchive + live cluster for Conforma test PipelineRuns.

    Returns dict with:
      - failing: set of component names with latest conforma test failed
      - details: dict of component -> {scenario, pr_name, pr_uid, timestamp}
      - running: dict of component -> running test info
    """
    token = get_openshift_token()
    if not token:
        return {'failing': set(), 'details': {}}

    namespace = config.k8s.namespace
    application_name = config.k8s.application_name

    latest_per_component = {}
    running_conforma = {}

    def process_pipelinerun(pr):
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
        pr_name = pr.get('metadata', {}).get('name', '')
        pr_uid = pr.get('metadata', {}).get('uid', '')
        conditions = pr.get('status', {}).get('conditions', [])

        if not conditions:
            if comp not in running_conforma or ts > running_conforma[comp][0]:
                running_conforma[comp] = (ts, pr_name, scenario)
            return

        c = conditions[-1]
        status = c.get('status')

        if status == 'Unknown':
            if comp not in running_conforma or ts > running_conforma[comp][0]:
                running_conforma[comp] = (ts, pr_name, scenario)
            return

        if comp not in latest_per_component or ts > latest_per_component[comp][0]:
            latest_per_component[comp] = (ts, pr_name, pr_uid, status, scenario)

    # Source 1: KubeArchive
    try:
        api_url = discover_kubearchive_api_url()
        session = create_authenticated_session(token)

        url = "{}/apis/tekton.dev/v1/namespaces/{}/pipelineruns".format(api_url, namespace)
        params = {
            'labelSelector': 'appstudio.openshift.io/application={},pipelines.appstudio.openshift.io/type=test'.format(application_name),
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
    except Exception as e:
        print("  KubeArchive query error: {}".format(e), file=sys.stderr)

    # Source 2: Live cluster
    try:
        result = subprocess.run(
            ['oc', 'get', 'pipelinerun', '-n', namespace,
             '-l', 'appstudio.openshift.io/application={},pipelines.appstudio.openshift.io/type=test'.format(application_name),
             '-o', 'json'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=15
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            for pr in data.get('items', []):
                process_pipelinerun(pr)
    except Exception:
        pass

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


def get_conforma_components_from_db(db, application_name):
    # type: (Database, str) -> Set[str]
    """Get components with unresolved Conforma failures from DB."""
    try:
        with db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT component_name FROM conforma_results
                WHERE application = %s
                  AND is_resolved = FALSE
                """,
                (application_name,)
            )
            return {row[0] for row in cursor.fetchall()}
    except Exception:
        return set()


def save_conforma_status(db, application_name, failing_components, running=None):
    # type: (Database, str, Set[str], Optional[Dict[str, Any]]) -> None
    """Save failing Conforma components and running tests to sync_status cache."""
    try:
        components_json = json.dumps(sorted(failing_components))
        running_json = json.dumps([
            {'component': comp, **info} for comp, info in sorted((running or {}).items())
        ])

        with db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE sync_status
                SET conforma_components = %s,
                    running_conforma = %s
                WHERE application = %s
                """,
                (components_json, running_json, application_name)
            )
    except Exception:
        pass


def main():
    import time
    start_time = time.time()

    config = CollectorConfig.from_env()
    db = Database(config.db)
    application_name = config.k8s.application_name

    print("Checking Conforma test status for {}...".format(application_name))

    cluster_result = get_failing_conforma_components(config)
    failing = cluster_result['failing']
    details = cluster_result['details']
    running = cluster_result.get('running', {})

    db_components = get_conforma_components_from_db(db, application_name)

    missing_in_db = failing - db_components
    extra_in_db = db_components - failing

    save_conforma_status(db, application_name, failing, running)

    duration = time.time() - start_time

    print("  Conforma failures in cluster: {}".format(len(failing)))
    print("  Conforma failures in DB: {}".format(len(db_components)))
    if running:
        print("  Conforma tests running: {} ({})".format(len(running), sorted(running.keys())))
    if missing_in_db:
        print("  Missing in DB: {}".format(sorted(missing_in_db)))
    if extra_in_db:
        print("  Extra in DB (resolved?): {}".format(sorted(extra_in_db)))
    print("  Duration: {:.1f}s".format(duration))

    status = {
        'failing_components': sorted(failing),
        'db_components': sorted(db_components),
        'missing_in_db': sorted(missing_in_db),
        'extra_in_db': sorted(extra_in_db),
        'running_conforma': [{'component': c, **i} for c, i in sorted(running.items())],
        'details': {k: v for k, v in details.items()},
        'duration': round(duration, 2)
    }

    print(json.dumps(status))


if __name__ == '__main__':
    main()
