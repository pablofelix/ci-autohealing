"""CLI mode routing — cluster vs local dispatch.

Provides helpers to check mode and ensure API connectivity,
eliminating the repeated ic_config boilerplate across commands.
"""

from cli import ic_config


def is_cluster():
    """Check if CLI is in cluster mode."""
    return ic_config.get_mode() == 'cluster'


def ensure_cluster():
    """Check cluster mode and API connectivity. Returns True if ready.

    Usage in CLI commands:
        if ensure_cluster():
            data = get_failures()
            render(data)
            return
        _bash_fallback(args)
    """
    if not is_cluster():
        return False
    from cli.data import require_data
    return require_data()
