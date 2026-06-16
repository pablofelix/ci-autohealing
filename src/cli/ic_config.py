"""Persistent IC configuration — ~/.ic/config.json.

Manages mode switching between local (direct DB) and cluster (REST API).
Environment variables IC_MODE, IC_API_URL, IC_API_KEY override the config file.
"""

import json
import os
from pathlib import Path

_CONFIG_DIR = Path.home() / '.ic'
_CONFIG_FILE = _CONFIG_DIR / 'config.json'
_DEFAULTS = {'mode': 'local', 'cluster': {'api_url': '', 'api_key': ''}}


def _ensure_dir():
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load():
    if _CONFIG_FILE.exists():
        try:
            with open(_CONFIG_FILE) as f:
                cfg = json.load(f)
            for k, v in _DEFAULTS.items():
                cfg.setdefault(k, v)
            if 'cluster' not in cfg:
                cfg['cluster'] = dict(_DEFAULTS['cluster'])
            return cfg
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULTS, cluster=dict(_DEFAULTS['cluster']))


def _save(cfg):
    _ensure_dir()
    with open(_CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)
        f.write('\n')


def get_mode():
    return os.environ.get('IC_MODE') or load()['mode']


def get_api_url():
    return os.environ.get('IC_API_URL') or load()['cluster'].get('api_url') or None


def get_api_key():
    return os.environ.get('IC_API_KEY') or load()['cluster'].get('api_key') or None


def set_mode(mode):
    cfg = load()
    cfg['mode'] = mode
    _save(cfg)


def set_cluster(api_url, api_key=None):
    cfg = load()
    cfg['mode'] = 'cluster'
    cfg['cluster']['api_url'] = api_url
    if api_key:
        cfg['cluster']['api_key'] = api_key
    _save(cfg)
