"""OpenShift authentication and KubeArchive API discovery.

Consolidates auth token retrieval, KubeArchive URL discovery, and
authenticated session creation used across all clients and collectors.

Uses the Python kubernetes client library to read kubeconfig instead of
shelling out to the oc CLI.
"""

import requests

from kubernetes import client, config


KUBEARCHIVE_FALLBACK_URL = (
    "https://kubearchive-api-server-product-kubearchive"
    ".apps.CLUSTER_DOMAIN"
)

_k8s_loaded = False


def _ensure_k8s_config():
    # type: () -> None
    global _k8s_loaded
    if not _k8s_loaded:
        config.load_kube_config()
        _k8s_loaded = True


def is_logged_in():
    # type: () -> bool
    """Check if user has a valid kubeconfig (equivalent to oc whoami)."""
    try:
        _ensure_k8s_config()
        v1 = client.CoreV1Api()
        v1.get_api_resources(_request_timeout=5)
        return True
    except Exception:
        return False


def get_openshift_token():
    # type: () -> Optional[str]
    """Get the Bearer token from kubeconfig."""
    try:
        _ensure_k8s_config()
        configuration = client.Configuration.get_default_copy()
        token = configuration.api_key.get('authorization', '')
        if token.startswith('Bearer '):
            return token[7:]
        return token or None
    except Exception:
        return None


def discover_kubearchive_api_url(fallback_url=KUBEARCHIVE_FALLBACK_URL):
    # type: (str) -> str
    """Discover KubeArchive API URL from cluster ConfigMap.

    Falls back to a known URL if the ConfigMap is not accessible.
    """
    try:
        _ensure_k8s_config()
        v1 = client.CoreV1Api()
        cm = v1.read_namespaced_config_map(
            'kubearchive-api-url', 'product-kubearchive',
            _request_timeout=10,
        )
        url = (cm.data or {}).get('URL', '')
        return url if url else fallback_url
    except Exception:
        return fallback_url


def discover_openshift_api_url(fallback_url="https://api.CLUSTER_DOMAIN:6443"):
    # type: (str) -> str
    """Discover the OpenShift API server URL from kubeconfig."""
    try:
        _ensure_k8s_config()
        configuration = client.Configuration.get_default_copy()
        return configuration.host or fallback_url
    except Exception:
        return fallback_url


def create_authenticated_session(token):
    # type: (str) -> requests.Session
    """Create an HTTP session with Bearer token authentication."""
    session = requests.Session()
    session.headers.update({
        'Authorization': 'Bearer {}'.format(token),
        'Accept': 'application/json'
    })
    return session
