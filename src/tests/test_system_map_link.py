"""Tests for Phase 8 — System Map deep-link from IC CLI."""

from unittest.mock import patch


class TestMakeSystemMapUrl:

    def test_returns_url_with_component(self):
        with patch('shared_config.SYSTEM_MAP_URL', 'http://localhost:3001'):
            from shared_config import make_system_map_url
            url = make_system_map_url('odh-dashboard-v3-5')
            assert url == 'http://localhost:3001/?highlight=comp-odh-dashboard-v3-5'

    def test_strips_trailing_slash(self):
        with patch('shared_config.SYSTEM_MAP_URL', 'http://localhost:3001/'):
            from shared_config import make_system_map_url
            url = make_system_map_url('test-comp')
            assert url == 'http://localhost:3001/?highlight=comp-test-comp'

    def test_empty_component_returns_empty(self):
        from shared_config import make_system_map_url
        assert make_system_map_url('') == ''

    def test_none_component_returns_empty(self):
        from shared_config import make_system_map_url
        assert make_system_map_url(None) == ''

    def test_no_url_configured_returns_empty(self):
        with patch('shared_config.SYSTEM_MAP_URL', ''):
            from shared_config import make_system_map_url
            assert make_system_map_url('some-comp') == ''

    def test_custom_url(self):
        with patch('shared_config.SYSTEM_MAP_URL', 'https://map.example.com'):
            from shared_config import make_system_map_url
            url = make_system_map_url('my-comp')
            assert url == 'https://map.example.com/?highlight=comp-my-comp'
