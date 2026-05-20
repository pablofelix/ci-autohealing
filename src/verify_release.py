#!/usr/bin/env python3.11
"""Verify snapshot image availability in target registry via Pyxis.

Fetches the Release CR and Snapshot from the cluster, then checks each
component image against Pyxis to determine if it exists in the target
registry. Outputs JSON to stdout for consumption by the ic shell script.
"""

import json
import logging
import sys
import argparse

from kubernetes import client

from clients.pyxis_client import PyxisClient
from config import CollectorConfig
from logger import setup_logger
from openshift_auth import _ensure_k8s_config

for name in list(logging.Logger.manager.loggerDict) + ['root']:
    lg = logging.getLogger(name) if name != 'root' else logging.getLogger()
    for h in lg.handlers:
        if hasattr(h, 'stream') and h.stream is sys.stdout:
            h.stream = sys.stderr

logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

logger = setup_logger(__name__)
for h in logger.handlers:
    if hasattr(h, 'stream'):
        h.stream = sys.stderr


_CRD_PLURALS = {
    'release': 'releases',
    'snapshot': 'snapshots',
}


def _k8s_get_json(kind, name, namespace):
    """Fetch a Kubernetes CRD resource as a dict via the Python API."""
    plural = _CRD_PLURALS.get(kind)
    if not plural:
        logger.error("Unknown CRD kind: %s", kind)
        return None
    try:
        _ensure_k8s_config()
        api = client.CustomObjectsApi()
        return api.get_namespaced_custom_object(
            group='appstudio.redhat.com', version='v1alpha1',
            namespace=namespace, plural=plural, name=name,
            _request_timeout=30,
        )
    except Exception as e:
        logger.error("k8s get %s %s error: %s", kind, name, e)
        return None


def _parse_image(container_image):
    """Parse quay.io/acme/image-rhel9@sha256:xxx into Pyxis query parts.

    Returns (repository, digest) or (None, None) if unparseable.
    The registry for Pyxis is always 'registry.access.redhat.com'.
    """
    if '@' not in container_image:
        return None, None
    ref, digest = container_image.rsplit('@', 1)
    # ref = quay.io/acme/image-rhel9 -> repository = rhoai/image-rhel9
    parts = ref.split('/', 1)
    if len(parts) < 2:
        return None, None
    repository = parts[1]  # everything after the registry host
    return repository, digest


def main():
    parser = argparse.ArgumentParser(description='Verify release images in Pyxis')
    parser.add_argument('--release', required=True, help='Release CR name')
    parser.add_argument('--namespace', help='Namespace (default: from config)')
    args = parser.parse_args()

    try:
        config = CollectorConfig.from_env()
        ns = args.namespace or config.k8s.namespace

        # 1. Get Release CR
        logger.info("Fetching Release CR: %s", args.release)
        release_cr = _k8s_get_json('release', args.release, ns)
        if not release_cr:
            raise RuntimeError("Release CR not found: {}".format(args.release))

        spec = release_cr.get('spec', {})
        snapshot_name = spec.get('snapshot', '')
        release_plan = spec.get('releasePlan', '')

        # 2. Determine target
        target = 'prod' if 'prod' in release_plan else 'stage'
        logger.info("Target: %s (from ReleasePlan: %s)", target, release_plan)

        # 3. Get Snapshot
        if not snapshot_name:
            raise RuntimeError("No snapshot in Release spec")

        logger.info("Fetching Snapshot: %s", snapshot_name)
        snapshot_cr = _k8s_get_json('snapshot', snapshot_name, ns)
        if not snapshot_cr:
            raise RuntimeError("Snapshot not found: {}".format(snapshot_name))

        components = snapshot_cr.get('spec', {}).get('components', [])
        logger.info("  %d components in snapshot", len(components))

        # 4. Build image list for Pyxis
        pyxis_registry = 'registry.access.redhat.com'
        images = []
        skipped = 0
        for comp in components:
            name = comp.get('name', '')
            container_image = comp.get('containerImage', '')
            repository, digest = _parse_image(container_image)
            if not repository or not digest:
                skipped += 1
                continue
            images.append({
                'name': name,
                'registry': pyxis_registry,
                'repository': repository,
                'digest': digest,
            })

        if skipped:
            logger.warning("Skipped %d components with unparseable images", skipped)

        # 5. Check against Pyxis
        logger.info("Checking %d images against Pyxis (%s)...", len(images), target)
        client = PyxisClient(target=target)
        results = client.check_images_batch(images)

        found = sum(1 for r in results if r['status'] == 'found')
        missing = sum(1 for r in results if r['status'] == 'missing')
        errors = sum(1 for r in results if r['status'] == 'error')

        output = {
            'release_name': args.release,
            'snapshot': snapshot_name,
            'target': target,
            'registry': pyxis_registry,
            'total': len(results),
            'found': found,
            'missing': missing,
            'errors': errors,
            'images': results,
        }

        print(json.dumps(output, indent=2, default=str))
        sys.exit(0)

    except Exception as e:
        logger.error("Verify failed: %s", e, exc_info=True)
        error_result = {
            'status': 'error',
            'error': str(e),
            'release_name': args.release,
        }
        print(json.dumps(error_result, indent=2))
        sys.exit(1)


if __name__ == '__main__':
    main()
