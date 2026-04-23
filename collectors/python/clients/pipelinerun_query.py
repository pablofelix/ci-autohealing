"""Shared PipelineRun query across KubeArchive and live cluster.

Consolidates the two-source fetch pattern used by all collectors and
status checkers. Deduplicates results by UID so callers never see the
same PipelineRun twice even when it appears in both sources.
"""

import subprocess
import json
from typing import List, Dict, Any, Optional

from logger import setup_logger

logger = setup_logger(__name__)


def query_pipelineruns(namespace, label_selector,
                       kubearchive_url=None, session=None,
                       max_pages=3, page_size=500):
    # type: (str, str, Optional[str], Optional[Any], int, int) -> List[Dict[str, Any]]
    """Fetch PipelineRuns from KubeArchive + live cluster, deduplicated by UID.

    Args:
        namespace: Kubernetes namespace.
        label_selector: Kubernetes label selector string.
        kubearchive_url: KubeArchive API base URL. Discovered if not provided.
        session: Authenticated requests.Session. Created if not provided.
        max_pages: Maximum KubeArchive pagination pages.
        page_size: Items per KubeArchive page.

    Returns:
        Combined list of PipelineRun dicts, deduplicated by UID.
    """
    by_uid = {}

    if not kubearchive_url or not session:
        from openshift_auth import (
            get_openshift_token,
            discover_kubearchive_api_url,
            create_authenticated_session,
        )
        if not kubearchive_url:
            kubearchive_url = discover_kubearchive_api_url()
        if not session:
            token = get_openshift_token()
            if token:
                session = create_authenticated_session(token)

    if kubearchive_url and session:
        try:
            url = "{}/apis/tekton.dev/v1/namespaces/{}/pipelineruns".format(
                kubearchive_url, namespace
            )
            params = {'labelSelector': label_selector, 'limit': page_size}

            for _ in range(max_pages):
                resp = session.get(url, params=params, timeout=30)
                if resp.status_code != 200:
                    break
                data = resp.json()
                for pr in data.get('items', []):
                    uid = pr.get('metadata', {}).get('uid')
                    if uid:
                        by_uid[uid] = pr
                cont = data.get('metadata', {}).get('continue')
                if cont:
                    params['continue'] = cont
                else:
                    break
        except Exception as e:
            logger.warning("KubeArchive query failed: %s", e)

    try:
        result = subprocess.run(
            ['oc', 'get', 'pipelinerun', '-n', namespace,
             '-l', label_selector, '-o', 'json'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=15
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            for pr in data.get('items', []):
                uid = pr.get('metadata', {}).get('uid')
                if uid:
                    by_uid[uid] = pr
    except Exception as e:
        logger.warning("Live cluster query failed: %s", e)

    return list(by_uid.values())
