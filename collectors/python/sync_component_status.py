#!/usr/bin/env python3
"""Sync component status - mark resolved failures and detect new successes.

This script:
1. Checks which components are currently failing
2. Marks as resolved components that were failing but now succeed
3. Records successful builds for historical tracking
4. Updates component health status

Usage:
    python3 sync_component_status.py [--components-file FILE]
"""

import sys
import time
import subprocess
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Set
from dataclasses import replace

from config import CollectorConfig
from repositories import DatabaseConnection, BuildFailureRepository
from models import Component, BuildStatus
from openshift_auth import get_openshift_token, discover_kubearchive_api_url, create_authenticated_session
from logger import setup_logger

logger = setup_logger(__name__)


class StatusSynchronizer:
    """Synchronizes component status between Kubernetes and database."""

    def __init__(self, config):
        # type: (CollectorConfig,) -> None
        self.config = config
        db = DatabaseConnection(config.db)
        self.build_repo = BuildFailureRepository(db)

    def get_component_metadata(self, component_name):
        # type: (str,) -> Optional[Component]
        try:
            result = subprocess.run(
                ['oc', 'get', 'component', component_name,
                 '-n', self.config.k8s.namespace, '-o', 'json'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, check=True, timeout=15
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
        except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
            return None

    def get_current_status(self, component_name):
        # type: (str,) -> Optional[Dict[str, Any]]
        """Get current push build status from live cluster + KubeArchive."""
        latest = None

        def check_pr(pr):
            nonlocal latest
            labels = pr.get('metadata', {}).get('labels', {})
            event_type = labels.get('pipelinesascode.tekton.dev/event-type', '')
            if event_type != 'push':
                return
            ts = pr.get('metadata', {}).get('creationTimestamp', '')
            conditions = pr.get('status', {}).get('conditions', [])
            if conditions:
                name = pr.get('metadata', {}).get('name')
                uid = pr.get('metadata', {}).get('uid')
                reason = conditions[-1].get('reason', '')
                if not latest or ts > latest[0]:
                    latest = (ts, name, uid, reason)

        # Source 1: Live cluster
        try:
            result = subprocess.run(
                ['oc', 'get', 'pipelinerun', '-n', self.config.k8s.namespace,
                 '-l', 'appstudio.openshift.io/component={},pipelines.appstudio.openshift.io/type=build'.format(component_name),
                 '-o', 'json'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=15
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                for pr in data.get('items', []):
                    check_pr(pr)
        except Exception:
            pass

        # Source 2: KubeArchive
        try:
            kubearchive_url = discover_kubearchive_api_url()
            token = get_openshift_token()
            if kubearchive_url and token:
                session = create_authenticated_session(token)
                url = "{}/apis/tekton.dev/v1/namespaces/{}/pipelineruns".format(
                    kubearchive_url, self.config.k8s.namespace
                )
                params = {
                    'labelSelector': 'appstudio.openshift.io/component={},pipelines.appstudio.openshift.io/type=build'.format(component_name),
                    'limit': 50
                }
                resp = session.get(url, params=params, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    for pr in data.get('items', []):
                        check_pr(pr)
        except Exception:
            pass

        if not latest:
            return None

        ts, name, uid, reason = latest

        if reason in ('Succeeded', 'Completed'):
            status = BuildStatus.SUCCEEDED
        elif reason == 'Failed':
            status = BuildStatus.FAILED
        elif reason in ('PipelineRunTimeout',):
            status = BuildStatus.FAILED
        elif reason == 'Running':
            status = BuildStatus.RUNNING
        else:
            status = BuildStatus.PENDING

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
            logger.info("Current status: {} (from DB, not in K8s)".format(result['current_status']))
            return result

        result['current_status'] = current['status'].value

        if result['was_failing'] and current['status'] == BuildStatus.SUCCEEDED:
            if self.build_repo.mark_resolved(component.name, app, ns, current['name']):
                result['now_resolved'] = True
                logger.info("Marked as resolved (last build: {})".format(current['name']))

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

            logger.info("Found {} components with unresolved failures".format(len(unresolved_names)))

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
            logger.info("[{}/{}] {}".format(i, len(components), component.name))

            if not component.repository_url:
                logger.warning("Component not found in cluster")
                continue

            result = self.sync_component(component)
            components_synced += 1

            if result['now_resolved']:
                total_resolved += 1
            if result['success_recorded']:
                total_successes += 1

            if not result['now_resolved'] and not result['success_recorded']:
                status = result['current_status']
                was_failing = "was failing, " if result['was_failing'] else ""
                logger.info("{}current status: {}".format(was_failing, status))

        duration = time.time() - start_time

        logger.info("=" * 70)
        logger.info("Synchronization Complete")
        logger.info("=" * 70)
        logger.info("Components synced: {}".format(components_synced))
        logger.info("Failures resolved: {}".format(total_resolved))
        logger.info("Successes recorded: {}".format(total_successes))
        logger.info("Duration: {:.1f}s".format(duration))

        return {
            'components_synced': components_synced,
            'resolved': total_resolved,
            'successes': total_successes,
            'duration': duration
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Synchronize component status - mark resolved and track successes'
    )
    parser.add_argument(
        '--components-file', type=Path,
        help='File with component names (one per line)'
    )
    args = parser.parse_args()

    config = CollectorConfig.from_env()
    if args.components_file:
        config = replace(config, components_file=args.components_file)

    synchronizer = StatusSynchronizer(config)
    result = synchronizer.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
