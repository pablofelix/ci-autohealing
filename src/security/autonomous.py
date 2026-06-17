"""AUTONOMOUS_MODE activation controls.

Ensures autonomous mode (auto-fix PRs, auto-execute skills) can only
be activated deliberately, with audit logging.
"""

import logging
import os
import time

logger = logging.getLogger(__name__)

_ACTIVATION_LOG_KEY = 'autonomous_activated_at'
_TIMEOUT_HOURS = 24


def is_autonomous_enabled():
    """Check if autonomous mode is active AND not expired."""
    if os.environ.get('AUTONOMOUS_MODE', '').lower() != 'true':
        return False

    try:
        from cli.ic_config import load
        cfg = load()
        activated_at = cfg.get(_ACTIVATION_LOG_KEY, 0)
        if activated_at:
            elapsed_hours = (time.time() - activated_at) / 3600
            if elapsed_hours > _TIMEOUT_HOURS:
                logger.warning("AUTONOMOUS_MODE expired after %dh — deactivating", _TIMEOUT_HOURS)
                return False
    except Exception:
        pass

    return True


def activate_autonomous(confirmed_by='cli'):
    """Activate autonomous mode with audit trail."""
    from cli.ic_config import load, _save
    cfg = load()
    cfg[_ACTIVATION_LOG_KEY] = time.time()
    cfg['autonomous_confirmed_by'] = confirmed_by
    _save(cfg)
    os.environ['AUTONOMOUS_MODE'] = 'true'
    logger.warning("AUTONOMOUS_MODE activated by %s (expires in %dh)",
                   confirmed_by, _TIMEOUT_HOURS)


def deactivate_autonomous():
    """Deactivate autonomous mode."""
    from cli.ic_config import load, _save
    cfg = load()
    cfg.pop(_ACTIVATION_LOG_KEY, None)
    cfg.pop('autonomous_confirmed_by', None)
    _save(cfg)
    os.environ.pop('AUTONOMOUS_MODE', None)
    logger.info("AUTONOMOUS_MODE deactivated")
