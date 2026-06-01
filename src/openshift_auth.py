"""OpenShift authentication and KubeArchive API discovery.

Consolidates auth token retrieval, KubeArchive URL discovery, and
authenticated session creation used across all clients and collectors.

Uses the Python kubernetes client library to read kubeconfig instead of
shelling out to the oc CLI.
"""

import os

import requests

from kubernetes import client, config


from cli.config import KUBEARCHIVE_URL as KUBEARCHIVE_FALLBACK_URL

_k8s_loaded = False


def _ensure_k8s_config():
    global _k8s_loaded
    if not _k8s_loaded:
        config.load_kube_config()
        _k8s_loaded = True


def is_logged_in():
    """Check if user has a valid kubeconfig (equivalent to oc whoami)."""
    try:
        _ensure_k8s_config()
        v1 = client.CoreV1Api()
        v1.get_api_resources(_request_timeout=5)
        return True
    except Exception:
        return False


def get_openshift_token():
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
    """Discover KubeArchive API URL from cluster ConfigMap.

    Falls back to a known URL if the ConfigMap is not accessible.
    """
    try:
        _ensure_k8s_config()
        v1 = client.CoreV1Api()
        ka_ns = os.environ.get('KUBEARCHIVE_NAMESPACE', 'product-kubearchive')
        cm = v1.read_namespaced_config_map(
            'kubearchive-api-url', ka_ns,
            _request_timeout=10,
        )
        url = (cm.data or {}).get('URL', '')
        return url if url else fallback_url
    except Exception:
        return fallback_url


def discover_openshift_api_url(fallback_url=""):
    """Discover the OpenShift API server URL from kubeconfig."""
    try:
        _ensure_k8s_config()
        configuration = client.Configuration.get_default_copy()
        return configuration.host or fallback_url
    except Exception:
        return fallback_url


def create_authenticated_session(token):
    """Create an HTTP session with Bearer token authentication."""
    session = requests.Session()
    session.headers.update({
        'Authorization': 'Bearer {}'.format(token),
        'Accept': 'application/json'
    })
    return session
