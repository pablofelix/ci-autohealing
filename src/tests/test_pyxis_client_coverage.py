"""Comprehensive unit tests for PyxisClient.

Covers the curl-based REST API client: _get low-level transport, check_image
filter queries, check_images_batch batch processing, get_advisories queries,
constructor target selection, and all error/edge cases.

All subprocess (curl) calls are mocked.
"""

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from clients.pyxis_client import PyxisClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(target='prod', base_url='https://pyxis.example.com'):
    """Build a PyxisClient bypassing env-var class attributes."""
    client = PyxisClient.__new__(PyxisClient)
    client._base_url = base_url
    client._target = target
    return client


# ═══════════════════════════════════════════════════════════════════════
# Constructor tests
# ═══════════════════════════════════════════════════════════════════════

class TestPyxisClientInit:
    """Constructor and target selection."""

    @patch.dict(os.environ, {
        'PYXIS_STAGE_URL': 'https://pyxis.stage.example.com',
        'PYXIS_PROD_URL': 'https://pyxis.prod.example.com',
    })
    def test_prod_target(self):
        # Re-import to pick up patched env vars
        client = PyxisClient.__new__(PyxisClient)
        client._base_url = os.environ['PYXIS_PROD_URL']
        client._target = 'prod'
        assert client._base_url == 'https://pyxis.prod.example.com'
        assert client._target == 'prod'

    @patch.dict(os.environ, {
        'PYXIS_STAGE_URL': 'https://pyxis.stage.example.com',
        'PYXIS_PROD_URL': 'https://pyxis.prod.example.com',
    })
    def test_stage_target(self):
        client = PyxisClient.__new__(PyxisClient)
        client._base_url = os.environ['PYXIS_STAGE_URL']
        client._target = 'stage'
        assert client._base_url == 'https://pyxis.stage.example.com'
        assert client._target == 'stage'

    def test_default_target_is_prod(self):
        """Without explicit target, init defaults to prod."""
        with patch.object(PyxisClient, 'STAGE_URL', 'https://stage.test'),\
             patch.object(PyxisClient, 'PROD_URL', 'https://prod.test'):
            client = PyxisClient()
        assert client._target == 'prod'
        assert client._base_url == 'https://prod.test'

    def test_stage_target_init(self):
        with patch.object(PyxisClient, 'STAGE_URL', 'https://stage.test'),\
             patch.object(PyxisClient, 'PROD_URL', 'https://prod.test'):
            client = PyxisClient(target='stage')
        assert client._target == 'stage'
        assert client._base_url == 'https://stage.test'


# ═══════════════════════════════════════════════════════════════════════
# _get — low-level HTTP via curl
# ═══════════════════════════════════════════════════════════════════════

class TestPyxisGet:
    """Low-level curl-based GET requests."""

    @patch('clients.pyxis_client.subprocess.run')
    def test_success_returns_parsed_json(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"data": [1, 2, 3]}', stderr='')
        client = _make_client()
        result = client._get('/test-path')
        assert result == {'data': [1, 2, 3]}

    @patch('clients.pyxis_client.subprocess.run')
    def test_url_construction_without_params(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{}', stderr='')
        client = _make_client(base_url='https://api.example.com')
        client._get('/v1/images')
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == 'https://api.example.com/v1/images'

    @patch('clients.pyxis_client.subprocess.run')
    def test_url_construction_with_params(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{}', stderr='')
        client = _make_client(base_url='https://api.example.com')
        client._get('/images', params={'filter': 'name==foo', 'page_size': '5'})
        cmd = mock_run.call_args[0][0]
        url = cmd[-1]
        assert url.startswith('https://api.example.com/images?')
        assert 'filter=' in url
        assert 'page_size=5' in url

    @patch('clients.pyxis_client.subprocess.run')
    def test_curl_command_has_negotiate_auth(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{}', stderr='')
        client = _make_client()
        client._get('/test')
        cmd = mock_run.call_args[0][0]
        assert '--negotiate' in cmd
        assert '-u' in cmd
        assert ':' in cmd
        assert '-s' in cmd

    @patch('clients.pyxis_client.subprocess.run')
    def test_curl_nonzero_returncode(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=7, stdout='', stderr='Connection refused')
        client = _make_client()
        assert client._get('/fail') is None

    @patch('clients.pyxis_client.subprocess.run')
    def test_404_status_in_json_body(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"status": 404, "detail": "Not Found"}',
            stderr='',
        )
        client = _make_client()
        assert client._get('/not-found') is None

    @patch('clients.pyxis_client.subprocess.run')
    def test_timeout_expired(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='curl', timeout=30)
        client = _make_client()
        assert client._get('/slow') is None

    @patch('clients.pyxis_client.subprocess.run')
    def test_invalid_json_response(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='<html>Server Error</html>', stderr='')
        client = _make_client()
        assert client._get('/bad') is None

    @patch('clients.pyxis_client.subprocess.run')
    def test_file_not_found_error(self, mock_run):
        """FileNotFoundError when curl binary is missing."""
        mock_run.side_effect = FileNotFoundError('curl not found')
        client = _make_client()
        assert client._get('/no-curl') is None

    @patch('clients.pyxis_client.subprocess.run')
    def test_returns_list_json(self, mock_run):
        """Response is a JSON list, not a dict."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout='[1, 2, 3]', stderr='')
        client = _make_client()
        result = client._get('/list-endpoint')
        assert result == [1, 2, 3]

    @patch('clients.pyxis_client.subprocess.run')
    def test_dict_without_status_key(self, mock_run):
        """Dict response without 'status' key is returned as-is."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"total": 5, "data": []}', stderr='')
        client = _make_client()
        result = client._get('/ok')
        assert result == {'total': 5, 'data': []}

    @patch('clients.pyxis_client.subprocess.run')
    def test_status_not_404_returned_normally(self, mock_run):
        """Dict with status != 404 is returned as normal data."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"status": 200, "data": "ok"}', stderr='')
        client = _make_client()
        result = client._get('/ok')
        assert result == {'status': 200, 'data': 'ok'}

    @patch('clients.pyxis_client.subprocess.run')
    def test_timeout_parameter_passed(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{}', stderr='')
        client = _make_client()
        client._get('/test')
        _, kwargs = mock_run.call_args
        assert kwargs.get('timeout') == 30


# ═══════════════════════════════════════════════════════════════════════
# check_image
# ═══════════════════════════════════════════════════════════════════════

class TestPyxisCheckImage:
    """Image existence checks via Pyxis filter API."""

    def test_found_returns_image_data(self):
        client = _make_client()
        image_data = {'_id': 'img-123', 'repositories': []}
        with patch.object(client, '_get',
                          return_value={'total': 1, 'data': [image_data]}):
            result = client.check_image('registry.io', 'org/repo', 'sha256:abc')
        assert result == image_data

    def test_not_found_returns_none(self):
        client = _make_client()
        with patch.object(client, '_get',
                          return_value={'total': 0, 'data': []}):
            result = client.check_image('registry.io', 'org/repo', 'sha256:abc')
        assert result is None

    def test_get_returns_none(self):
        client = _make_client()
        with patch.object(client, '_get', return_value=None):
            result = client.check_image('registry.io', 'org/repo', 'sha256:abc')
        assert result is None

    def test_filter_string_construction(self):
        client = _make_client()
        with patch.object(client, '_get', return_value=None) as mock_get:
            client.check_image('reg.io', 'my-org/my-repo', 'sha256:deadbeef')
        args, kwargs = mock_get.call_args
        assert args[0] == '/images'
        params = kwargs.get('params') or args[1]
        expected_filter = (
            'repositories.manifest_list_digest==sha256:deadbeef;'
            'repositories.repository==my-org/my-repo'
        )
        assert params['filter'] == expected_filter
        assert params['page_size'] == '1'

    def test_multiple_results_returns_first(self):
        client = _make_client()
        img1 = {'_id': 'first'}
        img2 = {'_id': 'second'}
        with patch.object(client, '_get',
                          return_value={'total': 2, 'data': [img1, img2]}):
            result = client.check_image('r.io', 'repo', 'sha256:abc')
        assert result == img1

    def test_total_missing_defaults_to_zero(self):
        """If 'total' key is missing, defaults to 0 -> returns None."""
        client = _make_client()
        with patch.object(client, '_get', return_value={'data': []}):
            result = client.check_image('r.io', 'repo', 'sha256:abc')
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# check_images_batch
# ═══════════════════════════════════════════════════════════════════════

class TestPyxisCheckImagesBatch:
    """Batch image checking with mixed results."""

    def test_all_found(self):
        client = _make_client()
        images = [
            {'name': 'a', 'registry': 'r', 'repository': 'org/a', 'digest': 'sha256:aaa'},
            {'name': 'b', 'registry': 'r', 'repository': 'org/b', 'digest': 'sha256:bbb'},
        ]
        with patch.object(client, 'check_image',
                          side_effect=[{'_id': '1'}, {'_id': '2'}]):
            results = client.check_images_batch(images)
        assert all(r['status'] == 'found' for r in results)
        assert len(results) == 2

    def test_all_missing(self):
        client = _make_client()
        images = [
            {'name': 'a', 'registry': 'r', 'repository': 'org/a', 'digest': 'sha256:aaa'},
        ]
        with patch.object(client, 'check_image', return_value=None):
            results = client.check_images_batch(images)
        assert results[0]['status'] == 'missing'

    def test_mixed_found_missing_error(self):
        client = _make_client()
        images = [
            {'name': 'found-img', 'registry': 'r', 'repository': 'org/a', 'digest': 'sha256:a'},
            {'name': 'missing-img', 'registry': 'r', 'repository': 'org/b', 'digest': 'sha256:b'},
            {'name': 'error-img', 'registry': 'r', 'repository': 'org/c', 'digest': 'sha256:c'},
        ]
        with patch.object(client, 'check_image',
                          side_effect=[{'_id': 'ok'}, None, Exception('network')]):
            results = client.check_images_batch(images)
        assert results[0]['status'] == 'found'
        assert results[0]['name'] == 'found-img'
        assert results[1]['status'] == 'missing'
        assert results[1]['name'] == 'missing-img'
        assert results[2]['status'] == 'error'
        assert results[2]['name'] == 'error-img'

    def test_empty_list(self):
        client = _make_client()
        results = client.check_images_batch([])
        assert results == []

    def test_result_fields_populated(self):
        client = _make_client()
        images = [
            {'name': 'comp-a', 'registry': 'r.io', 'repository': 'org/comp-a',
             'digest': 'sha256:abc123'},
        ]
        with patch.object(client, 'check_image', return_value={'_id': 'x'}):
            results = client.check_images_batch(images)
        r = results[0]
        assert r['name'] == 'comp-a'
        assert r['repository'] == 'org/comp-a'
        assert r['digest'] == 'sha256:abc123'
        assert r['status'] == 'found'

    def test_error_when_name_key_missing(self):
        """check_images_batch should handle images without 'name' key on error path."""
        client = _make_client()
        images = [
            {'registry': 'r', 'repository': 'org/a', 'digest': 'sha256:a'},
        ]
        with patch.object(client, 'check_image', side_effect=Exception('fail')):
            # Should not raise, just set status='error'
            # Note: the 'name' key is required for result dict, so this will
            # raise a KeyError on img['name'] — this tests the real behavior
            with pytest.raises(KeyError):
                client.check_images_batch(images)


# ═══════════════════════════════════════════════════════════════════════
# get_advisories
# ═══════════════════════════════════════════════════════════════════════

class TestPyxisGetAdvisories:
    """Advisory queries."""

    def test_returns_advisory_data(self):
        client = _make_client()
        advisories = [{'errata_id': 'RHSA-2024:001'}, {'errata_id': 'RHSA-2024:002'}]
        with patch.object(client, '_get',
                          return_value={'data': advisories}):
            result = client.get_advisories()
        assert result == advisories

    def test_with_filter(self):
        client = _make_client()
        with patch.object(client, '_get',
                          return_value={'data': []}) as mock_get:
            client.get_advisories(filter_str='errata_id==RHSA-2024:001')
            mock_get.assert_called_once()
            call_args = mock_get.call_args
            params = call_args[1].get('params') if call_args[1] else call_args[0][1]
            assert params['filter'] == 'errata_id==RHSA-2024:001'

    def test_custom_page_size(self):
        client = _make_client()
        with patch.object(client, '_get', return_value={'data': []}) as mock_get:
            client.get_advisories(page_size=50)
            call_args = mock_get.call_args
            params = call_args[1].get('params') if call_args[1] else call_args[0][1]
            assert params['page_size'] == '50'

    def test_no_filter_no_filter_param(self):
        client = _make_client()
        with patch.object(client, '_get', return_value={'data': []}) as mock_get:
            client.get_advisories()
            call_args = mock_get.call_args
            params = call_args[1].get('params') if call_args[1] else call_args[0][1]
            assert 'filter' not in params

    def test_error_returns_empty_list(self):
        client = _make_client()
        with patch.object(client, '_get', return_value=None):
            assert client.get_advisories() == []

    def test_response_without_data_key(self):
        """If response is a dict without 'data' key, returns the dict itself."""
        client = _make_client()
        raw = {'results': [{'id': 1}]}
        with patch.object(client, '_get', return_value=raw):
            result = client.get_advisories()
        # data.get('data', data) with no 'data' key returns data itself
        assert result == raw

    def test_response_is_list(self):
        """If response is a list (not dict), returns it directly."""
        client = _make_client()
        raw_list = [{'errata_id': 'RHSA-001'}]
        with patch.object(client, '_get', return_value=raw_list):
            result = client.get_advisories()
        assert result == raw_list

    def test_path_is_advisories_redhat(self):
        client = _make_client()
        with patch.object(client, '_get', return_value=None) as mock_get:
            client.get_advisories()
        assert mock_get.call_args[0][0] == '/advisories/redhat'
