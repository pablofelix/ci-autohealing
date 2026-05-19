"""OpenShift authentication and KubeArchive API discovery.

Consolidates auth token retrieval, KubeArchive URL discovery, and
authenticated session creation used across all clients and collectors.
"""

import subprocess

import requests


KUBEARCHIVE_FALLBACK_URL = (
    "https://kubearchive-api-server-product-kubearchive"
    ".apps.CLUSTER_DOMAIN"
)


def is_logged_in():
    # type: () -> bool
    """Check if user is logged into OpenShift."""
    try:
        result = subprocess.run(
            ['oc', 'whoami'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def get_openshift_token():
    # type: () -> Optional[str]
    """Get OpenShift authentication token via `oc whoami -t`."""
    try:
        result = subprocess.run(
            ['oc', 'whoami', '-t'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def discover_kubearchive_api_url(fallback_url=KUBEARCHIVE_FALLBACK_URL):
    # type: (str) -> str
    """Discover KubeArchive API URL from cluster ConfigMap.

    Falls back to a known URL if the ConfigMap is not accessible.
    """
    try:
        result = subprocess.run(
            ['oc', 'get', 'cm', '-n', 'product-kubearchive', 'kubearchive-api-url',
             '-o', 'jsonpath={.data.URL}'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return fallback_url


def discover_openshift_api_url(fallback_url="https://api.CLUSTER_DOMAIN:6443"):
    # type: (str) -> str
    """Discover the OpenShift API server URL."""
    try:
        result = subprocess.run(
            ['oc', 'whoami', '--show-server'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
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
