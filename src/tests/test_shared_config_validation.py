"""Tests for shared_config validation helpers."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_require_namespace_returns_error_when_empty():
    with patch('shared_config.NAMESPACE', ''):
        from shared_config import require_namespace

        @require_namespace
        def dummy():
            return {'ok': True}

        result = dummy()
        assert 'error' in result
        assert 'NAMESPACE' in result['error']


def test_require_namespace_passes_through_when_set():
    with patch('shared_config.NAMESPACE', 'rhoai-tenant'):
        from shared_config import require_namespace

        @require_namespace
        def dummy(x):
            return {'value': x}

        result = dummy(42)
        assert result == {'value': 42}


def test_require_namespace_preserves_function_name():
    with patch('shared_config.NAMESPACE', 'rhoai-tenant'):
        from shared_config import require_namespace

        @require_namespace
        def my_function():
            pass

        assert my_function.__name__ == 'my_function'


def test_validate_config_namespace_set_returns_ok():
    with patch('shared_config.NAMESPACE', 'rhoai-tenant'), \
         patch('shared_config.RELENG_NAMESPACE', 'rhtap-releng-tenant'), \
         patch('shared_config.APPLICATION_NAME', 'rhoai-v3-5'), \
         patch('shared_config.KONFLUX_UI_BASE', 'https://konflux-ui.example.com'):
        from shared_config import validate_config
        checks = validate_config()
        assert checks['namespace']['status'] == 'ok'
        assert checks['releng_namespace']['status'] == 'ok'


def test_validate_config_namespace_empty_returns_fail():
    with patch('shared_config.NAMESPACE', ''), \
         patch('shared_config.RELENG_NAMESPACE', ''), \
         patch('shared_config.APPLICATION_NAME', ''), \
         patch('shared_config.KONFLUX_UI_BASE', ''):
        from shared_config import validate_config
        checks = validate_config()
        assert checks['namespace']['status'] == 'fail'
        assert 'hint' in checks['namespace']
