"""Shared PipelineRun query across KubeArchive and live cluster.

Consolidates the two-source fetch pattern used by all collectors and
status checkers. Deduplicates results by UID so callers never see the
same PipelineRun twice even when it appears in both sources.
"""

from datetime import datetime, timedelta

from logger import setup_logger

logger = setup_logger(__name__)


def _query_live_cluster(namespace, label_selector):
    """Query live cluster for PipelineRuns via Kubernetes Python API."""
    try:
        from kubernetes import client

        from openshift_auth import _ensure_k8s_config
        _ensure_k8s_config()
        api = client.CustomObjectsApi()
        result = api.list_namespaced_custom_object(
            group='tekton.dev', version='v1', namespace=namespace,
            plural='pipelineruns',
            label_selector=label_selector,
        )
        return result.get('items', [])
    except Exception as e:
        logger.warning("K8s API query failed: %s", e)
        return []


def query_pipelineruns(namespace, label_selector,
                       kubearchive_url=None, session=None,
                       max_pages=3, page_size=100,
                       kubearchive_timeout=30, recent_days=None):
    """Fetch PipelineRuns from Kubernetes API + KubeArchive, deduplicated by UID.

    Queries live cluster first (fast, ~1s), then pages through KubeArchive
    for archived data. Stops paging when no new components are discovered
    or max_pages is reached.

    Args:
        namespace: Kubernetes namespace.
        label_selector: Kubernetes label selector string.
        kubearchive_url: KubeArchive API base URL. Discovered if not provided.
        session: Authenticated requests.Session. Created if not provided.
        max_pages: Maximum KubeArchive pagination pages (default 5).
        page_size: Items per KubeArchive page (default 100, KubeArchive max).
        kubearchive_timeout: Timeout in seconds per KubeArchive page request.
        recent_days: If set, only fetch PipelineRuns created in the last N days.

    Returns:
        Combined list of PipelineRun dicts, deduplicated by UID.
    """
    by_uid = {}

    for pr in _query_live_cluster(namespace, label_selector):
        uid = pr.get('metadata', {}).get('uid')
        if uid:
            by_uid[uid] = pr

    if by_uid:
        logger.info("K8s API returned %d live PipelineRun(s)", len(by_uid))

    if not kubearchive_url or not session:
        from openshift_auth import (
            create_authenticated_session,
            discover_kubearchive_api_url,
            get_openshift_token,
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
            if recent_days:
                since = (datetime.utcnow() - timedelta(days=recent_days)).strftime('%Y-%m-%dT%H:%M:%SZ')
                params['creationTimeAfter'] = since
            prev_component_count = _count_components(by_uid)

            import time as _time
            for page in range(max_pages):
                t0 = _time.time()
                resp = session.get(url, params=params, timeout=kubearchive_timeout)
                page_elapsed = _time.time() - t0
                if resp.status_code != 200:
                    logger.warning("KubeArchive page %d: HTTP %d (%.1fs)",
                                   page + 1, resp.status_code, page_elapsed)
                    break
                data = resp.json()
                items = data.get('items', [])
                for pr in items:
                    uid = pr.get('metadata', {}).get('uid')
                    if uid:
                        by_uid[uid] = pr

                new_component_count = _count_components(by_uid)
                new_components = new_component_count - prev_component_count
                logger.info("KubeArchive page %d: %d items, %d new components (%.1fs)",
                            page + 1, len(items), new_components, page_elapsed)

                cont = data.get('metadata', {}).get('continue')
                if not cont:
                    break
                if page > 0 and new_components <= 2:
                    break
                prev_component_count = new_component_count
                params['continue'] = cont

            logger.info("KubeArchive: %d total PipelineRuns, %d components",
                        len(by_uid), _count_components(by_uid))
        except Exception as e:
            logger.warning("KubeArchive query failed: %s", e)

    return list(by_uid.values())


def _count_components(by_uid):
    comps = set()
    for pr in by_uid.values():
        c = pr.get('metadata', {}).get('labels', {}).get(
            'appstudio.openshift.io/component', '')
        if c:
            comps.add(c)
    return len(comps)
