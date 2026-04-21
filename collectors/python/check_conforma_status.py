#!/usr/bin/env python3
"""Check Conforma (Enterprise Contract) test status from KubeArchive.

Queries KubeArchive for integration test PipelineRuns with conforma-* scenarios,
identifies components whose latest test failed, and caches results in sync_status.
"""

import os
import sys
import json
import subprocess
from typing import Dict, Any, Optional, Set

import requests

APPLICATION_NAME = os.getenv('APPLICATION_NAME', 'acme-v2-0')
NAMESPACE = os.getenv('NAMESPACE', 'NAMESPACE_PLACEHOLDER')


def get_oc_token() -> Optional[str]:
    try:
        result = subprocess.run(
            ['oc', 'whoami', '-t'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def get_kubearchive_url() -> str:
    try:
        result = subprocess.run(
            ['oc', 'get', 'cm', '-n', 'product-kubearchive', 'kubearchive-api-url',
             '-o', 'jsonpath={.data.URL}'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return "https://kubearchive-api-server-product-kubearchive.apps.CLUSTER_DOMAIN"


def get_failing_conforma_components() -> Dict[str, Any]:
    """Query KubeArchive + live cluster for Conforma test PipelineRuns.

    Returns dict with:
      - failing: set of component names with latest conforma test failed
      - details: dict of component -> {scenario, violations, warnings, successes, pr_name, pr_uid, timestamp}
    """
    token = get_oc_token()
    if not token:
        return {'failing': set(), 'details': {}}

    # Track latest conforma test per component
    # component -> (timestamp, pr_name, pr_uid, status, scenario)
    latest_per_component = {}
    running_conforma = {}  # component -> (timestamp, pr_name, scenario)

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
        api_url = get_kubearchive_url()
        session = requests.Session()
        session.headers.update({
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json'
        })

        url = f"{api_url}/apis/tekton.dev/v1/namespaces/{NAMESPACE}/pipelineruns"
        params = {
            'labelSelector': f'appstudio.openshift.io/application={APPLICATION_NAME},pipelines.appstudio.openshift.io/type=test',
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
        print(f"  KubeArchive query error: {e}", file=sys.stderr)

    # Source 2: Live cluster
    try:
        result = subprocess.run(
            ['oc', 'get', 'pipelinerun', '-n', NAMESPACE,
             '-l', f'appstudio.openshift.io/application={APPLICATION_NAME},pipelines.appstudio.openshift.io/type=test',
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

    # Only report running conforma tests that are newer than completed ones
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


def get_conforma_components_from_db() -> Set[str]:
    """Get components with unresolved Conforma failures from DB."""
    try:
        result = subprocess.run(
            ['docker', 'exec', 'ci-autohealing-db',
             'psql', '-U', 'postgres', '-d', 'konflux_monitoring', '-tAc',
             f"""
             SELECT DISTINCT component_name FROM conforma_results
             WHERE application = '{APPLICATION_NAME}'
               AND is_resolved = FALSE;
             """],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=10
        )
        if result.returncode != 0:
            return set()
        return {line.strip() for line in result.stdout.strip().split('\n') if line.strip()}
    except Exception:
        return set()


def save_conforma_status(failing_components: Set[str], running: Dict[str, Any] = None) -> None:
    """Save failing Conforma components and running tests to sync_status cache."""
    try:
        components_json = json.dumps(sorted(failing_components))
        running_json = json.dumps([
            {'component': comp, **info} for comp, info in sorted((running or {}).items())
        ])
        sql = f"""
        UPDATE sync_status
        SET conforma_components = '{components_json}',
            running_conforma = '{running_json}'
        WHERE application = '{APPLICATION_NAME}';
        """
        subprocess.run(
            ['docker', 'exec', 'ci-autohealing-db',
             'psql', '-U', 'postgres', '-d', 'konflux_monitoring', '-c', sql],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
        )
    except Exception:
        pass


def main():
    import time
    start_time = time.time()

    print(f"Checking Conforma test status for {APPLICATION_NAME}...")

    cluster_result = get_failing_conforma_components()
    failing = cluster_result['failing']
    details = cluster_result['details']
    running = cluster_result.get('running', {})

    db_components = get_conforma_components_from_db()

    missing_in_db = failing - db_components
    extra_in_db = db_components - failing

    # Save to sync_status cache
    save_conforma_status(failing, running)

    duration = time.time() - start_time

    print(f"  Conforma failures in cluster: {len(failing)}")
    print(f"  Conforma failures in DB: {len(db_components)}")
    if running:
        print(f"  Conforma tests running: {len(running)} ({sorted(running.keys())})")
    if missing_in_db:
        print(f"  Missing in DB: {sorted(missing_in_db)}")
    if extra_in_db:
        print(f"  Extra in DB (resolved?): {sorted(extra_in_db)}")
    print(f"  Duration: {duration:.1f}s")

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
