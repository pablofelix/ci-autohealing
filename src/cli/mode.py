"""CLI mode routing — API vs bash dispatch.

Provides helpers to check if an API is available (local or remote),
eliminating the repeated mode-check boilerplate across commands.

Modes:
- cluster: API on remote URL (e.g. OpenShift)
- local: API on localhost:8000 (user runs 'task serve')
- legacy: no API, use bash fallback + direct DB (when serve not running)
"""

from cli import ic_config


def is_cluster():
    """Check if CLI is in cluster mode (remote API)."""
    return ic_config.get_mode() == 'cluster'


def has_api():
    """Check if ANY API is available (local or remote).

    In local mode, only check localhost:8000 — a configured remote URL
    doesn't count because the CLI should fall through to bash for the
    richer display when the local API server isn't running.
    """
    mode = ic_config.get_mode()
    if mode == 'cluster':
        return bool(ic_config.get_api_url())
    import requests
    try:
        r = requests.get('http://localhost:8000/health', timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def ensure_cluster():
    """Check API connectivity. Returns True if API is ready.

    Works for both cluster and local mode (localhost:8000).
    Falls through to bash fallback if no API available.
    """
    if ic_config.get_mode() == 'cluster':
        from cli.data import require_data
        return require_data()
    if has_api():
        return True
    return False
