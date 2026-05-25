#!/usr/bin/env python3
"""Check build synchronization status entry point.

Use collectors.status_synchronizer.get_failing_build_components for the implementation.
"""

import sys
import json
import time

from config import CollectorConfig
from repositories import DatabaseConnection, BuildFailureRepository, SyncStatusRepository
from clients import TektonResultsClient
from openshift_auth import is_logged_in
from collectors.status_synchronizer import get_failing_build_components
from logger import setup_logger

logger = setup_logger(__name__)


def _batch_get_component_metadata(component_names, namespace):
    # type: (list, str) -> dict
    """Fetch repo URL and branch for components via K8s API (individual gets)."""
    result = {}
    try:
        from kubernetes import client, config
        config.load_kube_config()
        api = client.CustomObjectsApi()
        for name in component_names:
            try:
                item = api.get_namespaced_custom_object(
                    group='appstudio.redhat.com', version='v1alpha1',
                    namespace=namespace, plural='components', name=name,
                )
                git = item.get('spec', {}).get('source', {}).get('git', {})
                result[name] = {
                    'repository_url': git.get('url', ''),
                    'branch': git.get('revision', ''),
                }
            except Exception:
                pass
    except Exception as e:
        logger.debug("K8s API component metadata fetch failed: %s", e)
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--recent-days', type=int, default=None,
                        help='Only query PipelineRuns from the last N days')
    args = parser.parse_args()

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

    cluster_result = get_failing_build_components(config, recent_days=args.recent_days)
    cluster_components = cluster_result['failing']
    retriggered = cluster_result['retriggered']
    running = cluster_result.get('running', {})
    cluster_details = cluster_result.get('details', {})

    missing_in_db = cluster_components - db_components
    extra_in_db = db_components - cluster_components

    if missing_in_db and cluster_details:
        component_metadata = _batch_get_component_metadata(
            sorted(missing_in_db), config.k8s.namespace)
        collected = 0
        for comp in missing_in_db:
            detail = cluster_details.get(comp)
            if not detail:
                continue
            meta = component_metadata.get(comp, {})
            try:
                build_repo.upsert_failure(
                    pr_name=detail['pipelinerun_name'],
                    pr_uid=detail['pipelinerun_uid'],
                    component_name=comp,
                    application=application_name,
                    namespace=config.k8s.namespace,
                    repo_url=meta.get('repository_url', ''),
                    branch=meta.get('branch', ''),
                    status='Failed',
                )
                collected += 1
            except Exception as e:
                logger.debug("Failed to insert stub for %s: %s", comp, e)
        if collected:
            logger.info("Inserted %d missing component(s) into build_failures", collected)

    try:
        tr = TektonResultsClient(namespace=config.k8s.namespace)
    except Exception:
        tr = None

    for comp in extra_in_db:
        should_resolve = False
        if tr:
            try:
                history = tr.query_component_build_history(
                    application=application_name, component=comp, page_size=5)
                if history:
                    newest = max(history,
                                key=lambda p: p.get('metadata', {}).get('creationTimestamp', ''))
                    conds = newest.get('status', {}).get('conditions', [])
                    if conds and conds[-1].get('status') == 'True':
                        pr_name = newest.get('metadata', {}).get('name', 'sync-resolved')
                        annotations = newest.get('metadata', {}).get('annotations', {})
                        commit_sha = (
                            annotations.get('build.appstudio.redhat.com/commit_sha') or
                            annotations.get('pipelinesascode.tekton.dev/sha')
                        )
                        should_resolve = True
                        build_repo.mark_resolved(comp, application_name, config.k8s.namespace, pr_name,
                                                 resolution_commit_sha=commit_sha)
            except Exception:
                pass
        if not should_resolve:
            extra_in_db = extra_in_db - {comp}

    for comp in retriggered:
        build_repo.mark_resolved(comp, application_name, config.k8s.namespace, 'retrigger-resolved')

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
