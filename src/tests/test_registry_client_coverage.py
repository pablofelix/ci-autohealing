"""Comprehensive unit tests for RegistryClient covering all public and internal methods.

Covers: parse_image_ref, list_tags, get_manifest, resolve_per_arch_digest,
fetch_log_artifact, _extract_logs_from_tarball, fetch_sarif_results,
fetch_sarif_batch, _fetch_sarif_with_session, _fetch_single_sarif,
_parse_sarif, check_source_image, check_artifact_health,
check_artifact_health_batch, format_sarif_summary, _get_bearer_token,
_api_get, _auth_headers.

All HTTP calls are mocked -- no real network I/O.
"""

import io
import json
import os
import sys
import tarfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from clients.registry_client import RegistryClient

# Ensure QUAY_TOKEN from the host environment never leaks into tests.
# Individual tests that need credentials set token= explicitly.
_CLEAN_ENV = {'QUAY_TOKEN': ''}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code=200, json_data=None, content=b''):
    """Create a mock HTTP response."""
    resp = MagicMock()
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
    resp.content = content
    return resp


def _make_targz(filename='all-logs.txt', file_content=b'log line 1\nlog line 2'):
    """Create a valid tar.gz byte stream with a single file."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        info = tarfile.TarInfo(name=filename)
        info.size = len(file_content)
        tar.addfile(info, io.BytesIO(file_content))
    return buf.getvalue()


def _client_no_creds(mock_requests):
    """Create a RegistryClient with no credentials and a mocked session.

    Returns (client, session_mock).
    """
    session = MagicMock()
    mock_requests.Session.return_value = session
    with patch.dict(os.environ, _CLEAN_ENV):
        client = RegistryClient(token='')
    return client, session


DIGEST = 'sha256:' + 'a' * 64
DIGEST_HASH = 'a' * 64


# ---------------------------------------------------------------------------
# parse_image_ref (static, no mocking needed)
# ---------------------------------------------------------------------------
class TestParseImageRef:
    """Static method: parse image URLs into (registry, repository, tag_or_digest)."""

    def test_with_tag(self):
        assert RegistryClient.parse_image_ref('quay.io/rh/image:v1.0') == (
            'quay.io', 'rh/image', 'v1.0')

    def test_with_digest(self):
        assert RegistryClient.parse_image_ref(
            'quay.io/rh/image@sha256:abc123def') == (
            'quay.io', 'rh/image', 'sha256:abc123def')

    def test_with_https_prefix(self):
        assert RegistryClient.parse_image_ref(
            'https://quay.io/org/repo:latest') == (
            'quay.io', 'org/repo', 'latest')

    def test_with_http_prefix(self):
        assert RegistryClient.parse_image_ref(
            'http://registry.example.com/myorg/myapp:2.0') == (
            'registry.example.com', 'myorg/myapp', '2.0')

    def test_without_tag_defaults_to_latest(self):
        assert RegistryClient.parse_image_ref('quay.io/rh/image') == (
            'quay.io', 'rh/image', 'latest')

    def test_registry_only_url(self):
        result = RegistryClient.parse_image_ref('quay.io')
        assert result == ('quay.io', '', 'latest')

    def test_deep_repository_path_with_tag(self):
        assert RegistryClient.parse_image_ref(
            'quay.io/redhat-user-workloads/rhoai-tenant/odh-model-controller:tag1') == (
            'quay.io', 'redhat-user-workloads/rhoai-tenant/odh-model-controller', 'tag1')

    def test_deep_repository_path_with_digest(self):
        assert RegistryClient.parse_image_ref(
            'quay.io/a/b/c@sha256:deadbeef') == (
            'quay.io', 'a/b/c', 'sha256:deadbeef')

    def test_digest_with_https(self):
        assert RegistryClient.parse_image_ref(
            'https://quay.io/rh/image@sha256:abc') == (
            'quay.io', 'rh/image', 'sha256:abc')

    def test_tag_with_dots_and_dashes(self):
        assert RegistryClient.parse_image_ref(
            'quay.io/rh/image:v1.2.3-rc.1') == (
            'quay.io', 'rh/image', 'v1.2.3-rc.1')


# ---------------------------------------------------------------------------
# Constructor / env var handling
# ---------------------------------------------------------------------------
class TestConstructor:

    def test_token_from_argument(self):
        client = RegistryClient(token='my-token')
        assert client._basic_creds == 'my-token'

    @patch.dict(os.environ, {'QUAY_TOKEN': 'env-token'})
    def test_token_from_env(self):
        client = RegistryClient()
        assert client._basic_creds == 'env-token'

    @patch.dict(os.environ, _CLEAN_ENV)
    def test_token_empty_when_absent(self):
        client = RegistryClient()
        assert client._basic_creds == ''

    def test_argument_overrides_env(self):
        with patch.dict(os.environ, {'QUAY_TOKEN': 'env-token'}):
            client = RegistryClient(token='arg-token')
            assert client._basic_creds == 'arg-token'

    def test_fresh_caches(self):
        client = RegistryClient(token='t')
        assert client._bearer_cache == {}
        assert client._sarif_cache == {}


# ---------------------------------------------------------------------------
# _get_bearer_token
# ---------------------------------------------------------------------------
class TestGetBearerToken:

    @patch('clients.registry_client.requests')
    def test_returns_token_on_200(self, mock_requests):
        session = MagicMock()
        mock_requests.Session.return_value = session
        session.get.return_value = _make_response(200, {'token': 'bearer-xyz'})

        client = RegistryClient(token='basic-creds')
        token = client._get_bearer_token('quay.io', 'org/repo')

        assert token == 'bearer-xyz'
        session.get.assert_called_once()

    @patch('clients.registry_client.requests')
    def test_caches_token(self, mock_requests):
        session = MagicMock()
        mock_requests.Session.return_value = session
        session.get.return_value = _make_response(200, {'token': 'cached-token'})

        client = RegistryClient(token='creds')
        client._get_bearer_token('quay.io', 'org/repo')
        client._get_bearer_token('quay.io', 'org/repo')

        # Only one actual HTTP call; second should come from cache
        assert session.get.call_count == 1

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_returns_empty_without_creds(self, mock_requests):
        session = MagicMock()
        mock_requests.Session.return_value = session

        client = RegistryClient(token='')
        token = client._get_bearer_token('quay.io', 'org/repo')

        assert token == ''
        session.get.assert_not_called()

    @patch('clients.registry_client.requests')
    def test_returns_empty_on_non_200(self, mock_requests):
        session = MagicMock()
        mock_requests.Session.return_value = session
        session.get.return_value = _make_response(401, {'error': 'unauthorized'})

        client = RegistryClient(token='bad-creds')
        token = client._get_bearer_token('quay.io', 'org/repo')

        assert token == ''

    @patch('clients.registry_client.requests')
    def test_returns_empty_on_exception(self, mock_requests):
        session = MagicMock()
        mock_requests.Session.return_value = session
        session.get.side_effect = ConnectionError("connection refused")

        client = RegistryClient(token='creds')
        token = client._get_bearer_token('quay.io', 'org/repo')

        assert token == ''


# ---------------------------------------------------------------------------
# _api_get
# ---------------------------------------------------------------------------
class TestApiGet:

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_builds_correct_url(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        expected_resp = _make_response(200)
        session.get.return_value = expected_resp

        resp = client._api_get('quay.io', 'org/repo/tags/list')

        session.get.assert_called_once()
        url_arg = session.get.call_args[0][0]
        assert url_arg == 'https://quay.io/v2/org/repo/tags/list'

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_sets_accept_header(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(200)

        client._api_get('quay.io', 'path', accept='application/json')

        headers = session.get.call_args[1]['headers']
        assert headers['Accept'] == 'application/json'

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_uses_custom_session(self, mock_requests):
        client, default_session = _client_no_creds(mock_requests)

        custom_session = MagicMock()
        custom_session.get.return_value = _make_response(200)

        client._api_get('quay.io', 'path', session=custom_session)

        custom_session.get.assert_called_once()
        default_session.get.assert_not_called()


# ---------------------------------------------------------------------------
# _auth_headers
# ---------------------------------------------------------------------------
class TestAuthHeaders:

    @patch('clients.registry_client.requests')
    def test_includes_bearer_when_available(self, mock_requests):
        session = MagicMock()
        mock_requests.Session.return_value = session
        session.get.return_value = _make_response(200, {'token': 'tok123'})

        client = RegistryClient(token='creds')
        headers = client._auth_headers('quay.io', 'org/repo')

        assert 'Authorization' in headers
        assert headers['Authorization'] == 'Bearer tok123'
        assert 'Accept' in headers

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_no_auth_when_no_creds(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        headers = client._auth_headers('quay.io', 'org/repo')

        assert 'Authorization' not in headers
        assert 'Accept' in headers


# ---------------------------------------------------------------------------
# list_tags
# ---------------------------------------------------------------------------
class TestListTags:

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_success_returns_tags(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(200, {'tags': ['v1', 'v2', 'latest']})

        tags = client.list_tags('quay.io', 'org/repo')
        assert tags == ['v1', 'v2', 'latest']

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_empty_tags(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(200, {'tags': []})

        assert client.list_tags('quay.io', 'org/repo') == []

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_non_200_returns_empty(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(404, {'errors': [{'code': 'NOT_FOUND'}]})

        assert client.list_tags('quay.io', 'org/repo') == []

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_401_returns_empty(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(401)

        assert client.list_tags('quay.io', 'org/repo') == []

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_request_exception_returns_empty(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.side_effect = ConnectionError("timeout")

        assert client.list_tags('quay.io', 'org/repo') == []

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_missing_tags_key_returns_empty(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(200, {'name': 'org/repo'})

        assert client.list_tags('quay.io', 'org/repo') == []


# ---------------------------------------------------------------------------
# get_manifest
# ---------------------------------------------------------------------------
class TestGetManifest:

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_success_returns_manifest(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        manifest_data = {
            'schemaVersion': 2,
            'mediaType': 'application/vnd.oci.image.index.v1+json',
            'manifests': [
                {'digest': 'sha256:aaa', 'platform': {'architecture': 'amd64', 'os': 'linux'}},
            ],
        }
        session.get.return_value = _make_response(200, manifest_data)

        result = client.get_manifest('quay.io', 'org/repo', 'v1')
        assert result == manifest_data

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_non_200_returns_none(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(404)

        assert client.get_manifest('quay.io', 'org/repo', 'v1') is None

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_403_returns_none(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(403)

        assert client.get_manifest('quay.io', 'org/repo', 'v1') is None

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_exception_returns_none(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.side_effect = TimeoutError("timed out")

        assert client.get_manifest('quay.io', 'org/repo', 'v1') is None

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_malformed_json_returns_none(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        resp = _make_response(200)
        resp.json.side_effect = ValueError("bad json")
        session.get.return_value = resp

        assert client.get_manifest('quay.io', 'org/repo', 'v1') is None


# ---------------------------------------------------------------------------
# resolve_per_arch_digest
# ---------------------------------------------------------------------------
class TestResolvePerArchDigest:

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_finds_matching_arch(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        target = 'sha256:archdigest111'
        tags_resp = _make_response(200, {'tags': ['v1.0', 'sha256-skip']})
        manifest_resp = _make_response(200, {
            'manifests': [
                {
                    'digest': target,
                    'platform': {'architecture': 'arm64', 'os': 'linux'},
                },
                {
                    'digest': 'sha256:other',
                    'platform': {'architecture': 'amd64', 'os': 'linux'},
                },
            ],
        })
        session.get.side_effect = [tags_resp, manifest_resp]

        result = client.resolve_per_arch_digest('quay.io', 'org/repo', target)

        assert result is not None
        assert result['architecture'] == 'arm64'
        assert result['manifest_list_tag'] == 'v1.0'
        assert result['digest'] == target

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_no_tags_returns_none(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(200, {'tags': []})

        assert client.resolve_per_arch_digest('quay.io', 'org/repo', DIGEST) is None

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_no_matching_digest_returns_none(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        tags_resp = _make_response(200, {'tags': ['v1']})
        manifest_resp = _make_response(200, {
            'manifests': [{'digest': 'sha256:other', 'platform': {'architecture': 'amd64'}}],
        })
        session.get.side_effect = [tags_resp, manifest_resp]

        assert client.resolve_per_arch_digest('quay.io', 'org/repo', DIGEST) is None

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_only_sha256_prefixed_tags_returns_none(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(200, {'tags': ['sha256-aaa', 'sha256-bbb']})

        assert client.resolve_per_arch_digest('quay.io', 'org/repo', DIGEST) is None


# ---------------------------------------------------------------------------
# fetch_log_artifact
# ---------------------------------------------------------------------------
class TestFetchLogArtifact:

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_success_extracts_logs(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        tar_data = _make_targz('all-logs.txt', b'step1: passed\nstep2: failed')

        tags_resp = _make_response(200, {'tags': ['my-pr-run-abc-logs', 'other-tag']})
        manifest_resp = _make_response(200, {
            'layers': [{'digest': 'sha256:layerdigest'}],
        })
        blob_resp = _make_response(200, content=tar_data)

        session.get.side_effect = [tags_resp, manifest_resp, blob_resp]

        result = client.fetch_log_artifact('quay.io', 'org/repo', 'my-pr-run')

        assert 'step1: passed' in result
        assert 'step2: failed' in result

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_no_matching_tags_returns_empty(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(200, {'tags': ['unrelated', 'other']})

        assert client.fetch_log_artifact('quay.io', 'org/repo', 'my-pr') == ''

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_manifest_fetch_failure(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        tags_resp = _make_response(200, {'tags': ['my-pr-logs']})
        manifest_resp = _make_response(500)

        session.get.side_effect = [tags_resp, manifest_resp]

        assert client.fetch_log_artifact('quay.io', 'org/repo', 'my-pr') == ''

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_blob_fetch_failure(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        tags_resp = _make_response(200, {'tags': ['my-pr-logs']})
        manifest_resp = _make_response(200, {
            'layers': [{'digest': 'sha256:layerdigest'}],
        })
        blob_resp = _make_response(404)

        session.get.side_effect = [tags_resp, manifest_resp, blob_resp]

        assert client.fetch_log_artifact('quay.io', 'org/repo', 'my-pr') == ''

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_no_layers_in_manifest(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        tags_resp = _make_response(200, {'tags': ['my-pr-logs']})
        manifest_resp = _make_response(200, {'layers': []})

        session.get.side_effect = [tags_resp, manifest_resp]

        assert client.fetch_log_artifact('quay.io', 'org/repo', 'my-pr') == ''

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_exception_during_fetch(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        tags_resp = _make_response(200, {'tags': ['my-pr-logs']})
        session.get.side_effect = [tags_resp, ConnectionError("network down")]

        assert client.fetch_log_artifact('quay.io', 'org/repo', 'my-pr') == ''

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_selects_latest_log_tag_alphabetically(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        tar_data = _make_targz('all-logs.txt', b'latest log')

        tags_resp = _make_response(200, {
            'tags': ['run-aaa-logs', 'run-zzz-logs', 'run-mmm-logs'],
        })
        manifest_resp = _make_response(200, {
            'layers': [{'digest': 'sha256:layer'}],
        })
        blob_resp = _make_response(200, content=tar_data)

        session.get.side_effect = [tags_resp, manifest_resp, blob_resp]

        result = client.fetch_log_artifact('quay.io', 'org/repo', 'run')
        assert 'latest log' in result


# ---------------------------------------------------------------------------
# _extract_logs_from_tarball (static, no mocking needed)
# ---------------------------------------------------------------------------
class TestExtractLogsFromTarball:

    def test_valid_targz_with_all_logs(self):
        data = _make_targz('all-logs.txt', b'line1\nline2\nline3')
        result = RegistryClient._extract_logs_from_tarball(data)
        assert 'line1' in result
        assert 'line3' in result

    def test_valid_targz_with_txt_file(self):
        data = _make_targz('step-output.txt', b'step output content')
        result = RegistryClient._extract_logs_from_tarball(data)
        assert 'step output content' in result

    def test_prefers_all_logs_over_other_txt(self):
        """When all-logs.txt exists, it should be returned first."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w:gz') as tar:
            content1 = b'all logs content'
            info1 = tarfile.TarInfo(name='all-logs.txt')
            info1.size = len(content1)
            tar.addfile(info1, io.BytesIO(content1))

            content2 = b'other content'
            info2 = tarfile.TarInfo(name='other.txt')
            info2.size = len(content2)
            tar.addfile(info2, io.BytesIO(content2))

        result = RegistryClient._extract_logs_from_tarball(buf.getvalue())
        assert 'all logs content' in result

    def test_invalid_data_returns_empty(self):
        assert RegistryClient._extract_logs_from_tarball(b'not a tarball') == ''

    def test_corrupt_gzip_returns_empty(self):
        """Corrupt gzip data is caught by the except clause and returns empty."""
        assert RegistryClient._extract_logs_from_tarball(b'\x1f\x8b\x08corrupt') == ''

    def test_empty_data_returns_empty(self):
        assert RegistryClient._extract_logs_from_tarball(b'') == ''

    def test_targz_with_no_txt_files(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w:gz') as tar:
            content = b'binary data'
            info = tarfile.TarInfo(name='data.bin')
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))

        result = RegistryClient._extract_logs_from_tarball(buf.getvalue())
        assert result == ''

    def test_handles_utf8_errors_gracefully(self):
        data = _make_targz('all-logs.txt', b'valid text \xff\xfe invalid bytes')
        result = RegistryClient._extract_logs_from_tarball(data)
        assert 'valid text' in result


# ---------------------------------------------------------------------------
# fetch_sarif_results
# ---------------------------------------------------------------------------
class TestFetchSarifResults:

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_success_with_sarif_referrers(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        sarif_data = json.dumps({
            'runs': [{
                'tool': {'driver': {'rules': [
                    {'id': 'CVE-2024-1234', 'properties': {'package': 'openssl'}},
                ]}},
                'results': [{
                    'ruleId': 'CVE-2024-1234',
                    'level': 'error',
                    'message': {'text': 'Critical vuln'},
                }],
            }],
        }).encode()

        referrers_resp = _make_response(200, {
            'manifests': [
                {'digest': 'sha256:sarif1', 'artifactType': 'application/sarif+json'},
            ],
        })
        sarif_manifest_resp = _make_response(200, {
            'layers': [{'digest': 'sha256:sarif_blob'}],
        })
        sarif_blob_resp = _make_response(200, content=sarif_data)

        session.get.side_effect = [referrers_resp, sarif_manifest_resp, sarif_blob_resp]

        results = client.fetch_sarif_results('quay.io', 'org/repo', DIGEST)

        assert len(results) == 1
        assert results[0]['ruleId'] == 'CVE-2024-1234'
        assert results[0]['level'] == 'error'
        assert results[0]['package'] == 'openssl'

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_no_sarif_referrers(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        referrers_resp = _make_response(200, {
            'manifests': [
                {'digest': 'sha256:sig', 'artifactType': 'application/cosign'},
            ],
        })
        session.get.return_value = referrers_resp

        assert client.fetch_sarif_results('quay.io', 'org/repo', DIGEST) == []

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_non_200_referrers(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(404)

        assert client.fetch_sarif_results('quay.io', 'org/repo', DIGEST) == []

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_cache_hit(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        cache_key = 'quay.io/org/repo@{}'.format(DIGEST)
        cached_results = [{'ruleId': 'CVE-cached', 'level': 'warning'}]
        client._sarif_cache[cache_key] = cached_results

        results = client.fetch_sarif_results('quay.io', 'org/repo', DIGEST)

        assert results == cached_results
        session.get.assert_not_called()

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_exception_returns_empty_and_caches(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.side_effect = ConnectionError("network error")

        results = client.fetch_sarif_results('quay.io', 'org/repo', DIGEST)

        assert results == []
        cache_key = 'quay.io/org/repo@{}'.format(DIGEST)
        assert cache_key in client._sarif_cache

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_empty_manifests_list(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(200, {'manifests': []})

        assert client.fetch_sarif_results('quay.io', 'org/repo', DIGEST) == []


# ---------------------------------------------------------------------------
# _fetch_single_sarif
# ---------------------------------------------------------------------------
class TestFetchSingleSarif:

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_manifest_non_200(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(500)

        assert client._fetch_single_sarif('quay.io', 'org/repo', 'sha256:sarif') == []

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_no_layers(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(200, {'layers': []})

        assert client._fetch_single_sarif('quay.io', 'org/repo', 'sha256:sarif') == []

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_blob_non_200(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        manifest_resp = _make_response(200, {
            'layers': [{'digest': 'sha256:blob'}],
        })
        blob_resp = _make_response(404)
        session.get.side_effect = [manifest_resp, blob_resp]

        assert client._fetch_single_sarif('quay.io', 'org/repo', 'sha256:sarif') == []

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_exception_returns_empty(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.side_effect = Exception("unexpected error")

        assert client._fetch_single_sarif('quay.io', 'org/repo', 'sha256:sarif') == []


# ---------------------------------------------------------------------------
# _parse_sarif (static, no mocking needed)
# ---------------------------------------------------------------------------
class TestParseSarif:

    def test_valid_sarif_with_results(self):
        sarif = json.dumps({
            'runs': [{
                'tool': {'driver': {'rules': [
                    {
                        'id': 'CVE-2024-0001',
                        'properties': {
                            'package': 'libssl',
                            'fixed_version': '3.0.14',
                        },
                    },
                ]}},
                'results': [{
                    'ruleId': 'CVE-2024-0001',
                    'level': 'error',
                    'message': {'text': 'OpenSSL vulnerability'},
                }],
            }],
        }).encode()

        results = RegistryClient._parse_sarif(sarif)
        assert len(results) == 1
        assert results[0]['ruleId'] == 'CVE-2024-0001'
        assert results[0]['level'] == 'error'
        assert results[0]['package'] == 'libssl'
        assert results[0]['fix_version'] == '3.0.14'

    def test_empty_runs(self):
        sarif = json.dumps({'runs': []}).encode()
        assert RegistryClient._parse_sarif(sarif) == []

    def test_malformed_json(self):
        assert RegistryClient._parse_sarif(b'not json at all') == []

    def test_sarif_with_affected_package_property(self):
        sarif = json.dumps({
            'runs': [{
                'tool': {'driver': {'rules': [
                    {
                        'id': 'CVE-2024-0002',
                        'properties': {'affected_package': 'curl'},
                    },
                ]}},
                'results': [{
                    'ruleId': 'CVE-2024-0002',
                    'level': 'warning',
                    'message': {'text': 'curl vuln'},
                }],
            }],
        }).encode()

        results = RegistryClient._parse_sarif(sarif)
        assert results[0]['package'] == 'curl'

    def test_sarif_with_fixedVersion_property(self):
        sarif = json.dumps({
            'runs': [{
                'tool': {'driver': {'rules': [
                    {
                        'id': 'CVE-2024-0003',
                        'properties': {'fixedVersion': '2.0.0'},
                    },
                ]}},
                'results': [{
                    'ruleId': 'CVE-2024-0003',
                    'level': 'note',
                    'message': {'text': 'minor vuln'},
                }],
            }],
        }).encode()

        results = RegistryClient._parse_sarif(sarif)
        assert results[0]['fix_version'] == '2.0.0'

    def test_result_without_matching_rule(self):
        sarif = json.dumps({
            'runs': [{
                'tool': {'driver': {'rules': []}},
                'results': [{
                    'ruleId': 'CVE-orphan',
                    'level': 'warning',
                    'message': {'text': 'no rule metadata'},
                }],
            }],
        }).encode()

        results = RegistryClient._parse_sarif(sarif)
        assert len(results) == 1
        assert results[0]['ruleId'] == 'CVE-orphan'
        assert results[0]['package'] == ''
        assert results[0]['fix_version'] == ''

    def test_message_text_truncated_at_200_chars(self):
        long_msg = 'x' * 300
        sarif = json.dumps({
            'runs': [{
                'tool': {'driver': {'rules': []}},
                'results': [{
                    'ruleId': 'CVE-long',
                    'level': 'error',
                    'message': {'text': long_msg},
                }],
            }],
        }).encode()

        results = RegistryClient._parse_sarif(sarif)
        assert len(results[0]['message']) == 200

    def test_multiple_runs_and_results(self):
        sarif = json.dumps({
            'runs': [
                {
                    'tool': {'driver': {'rules': []}},
                    'results': [
                        {'ruleId': 'CVE-1', 'level': 'error', 'message': {'text': 'a'}},
                        {'ruleId': 'CVE-2', 'level': 'warning', 'message': {'text': 'b'}},
                    ],
                },
                {
                    'tool': {'driver': {'rules': []}},
                    'results': [
                        {'ruleId': 'CVE-3', 'level': 'note', 'message': {'text': 'c'}},
                    ],
                },
            ],
        }).encode()

        results = RegistryClient._parse_sarif(sarif)
        assert len(results) == 3

    def test_unicode_decode_error(self):
        assert RegistryClient._parse_sarif(b'\xff\xfe\xfd') == []

    def test_missing_message_key(self):
        sarif = json.dumps({
            'runs': [{
                'tool': {'driver': {'rules': []}},
                'results': [{'ruleId': 'CVE-nomsg', 'level': 'warning'}],
            }],
        }).encode()

        results = RegistryClient._parse_sarif(sarif)
        assert results[0]['message'] == ''

    def test_default_level_is_warning(self):
        sarif = json.dumps({
            'runs': [{
                'tool': {'driver': {'rules': []}},
                'results': [{'ruleId': 'CVE-nolevel', 'message': {'text': 'hi'}}],
            }],
        }).encode()

        results = RegistryClient._parse_sarif(sarif)
        assert results[0]['level'] == 'warning'


# ---------------------------------------------------------------------------
# check_source_image
# ---------------------------------------------------------------------------
class TestCheckSourceImage:

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_found_via_referrers(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        referrers_resp = _make_response(200, {
            'manifests': [
                {'artifactType': 'application/vnd.oci.source.image.v1'},
            ],
        })
        session.get.return_value = referrers_resp

        result = client.check_source_image('quay.io', 'org/repo', DIGEST)

        assert result['exists'] is True
        assert result['method'] == 'referrers'

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_found_via_src_tag(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        # First call: referrers (empty manifests)
        # Second call: src tag check (200)
        referrers_resp = _make_response(200, {'manifests': []})
        src_tag_resp = _make_response(200)

        session.get.side_effect = [referrers_resp, src_tag_resp]

        result = client.check_source_image('quay.io', 'org/repo', DIGEST)

        assert result['exists'] is True
        assert result['method'] == 'src_tag'

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_found_via_head_request(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        referrers_resp = _make_response(200, {'manifests': []})
        src_tag_resp = _make_response(404)
        head_resp = _make_response(200)

        session.get.side_effect = [referrers_resp, src_tag_resp]
        session.head.return_value = head_resp

        result = client.check_source_image('quay.io', 'org/repo', DIGEST)

        assert result['exists'] is True
        assert result['method'] == 'head_request'

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_not_found_anywhere(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        referrers_resp = _make_response(200, {'manifests': []})
        src_tag_resp = _make_response(404)
        head_resp = _make_response(404)

        session.get.side_effect = [referrers_resp, src_tag_resp]
        session.head.return_value = head_resp

        result = client.check_source_image('quay.io', 'org/repo', DIGEST)

        assert result['exists'] is False
        assert result['method'] == 'referrers+src_tag'

    @patch.dict(os.environ, _CLEAN_ENV)
    def test_invalid_digest_format(self):
        client = RegistryClient(token='')
        result = client.check_source_image('quay.io', 'org/repo', 'not-a-digest')

        assert result['exists'] is None
        assert result['method'] == 'skipped'

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_referrers_exception_falls_through_to_src_tag(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        # referrers raises, then src_tag succeeds
        session.get.side_effect = [
            ConnectionError("referrers failed"),
            _make_response(200),  # src_tag check succeeds
        ]

        result = client.check_source_image('quay.io', 'org/repo', DIGEST)

        assert result['exists'] is True
        assert result['method'] == 'src_tag'

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_src_artifact_type_keyword(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        referrers_resp = _make_response(200, {
            'manifests': [
                {'artifactType': 'application/vnd.oci.src-container'},
            ],
        })
        session.get.return_value = referrers_resp

        result = client.check_source_image('quay.io', 'org/repo', DIGEST)

        assert result['exists'] is True
        assert result['method'] == 'referrers'


# ---------------------------------------------------------------------------
# check_artifact_health
# ---------------------------------------------------------------------------
class TestCheckArtifactHealth:

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_all_artifacts_via_referrers(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        referrers_resp = _make_response(200, {
            'manifests': [
                {'artifactType': 'application/vnd.oci.source.image'},
                {'artifactType': 'application/vnd.in-toto.attestation'},
                {'artifactType': 'application/spdx+json'},
                {'artifactType': 'application/vnd.dev.cosign.simplesigning.v1'},
            ],
        })
        session.get.return_value = referrers_resp

        result = client.check_artifact_health('quay.io', 'org/repo', DIGEST)

        assert result['healthy'] is True
        assert result['missing'] == []
        assert result['digest'] == DIGEST
        for art_type in ('sig', 'src', 'att', 'sbom'):
            assert result['artifacts'][art_type]['exists'] is True
            assert result['artifacts'][art_type]['method'] == 'referrers'

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_some_via_tag_fallback(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        # Referrers only has signature
        referrers_resp = _make_response(200, {
            'manifests': [
                {'artifactType': 'application/cosign'},
            ],
        })
        # Tag fallback HEAD calls: src=200, att=200, sbom=404
        head_src = _make_response(200)
        head_att = _make_response(200)
        head_sbom = _make_response(404)

        session.get.return_value = referrers_resp
        session.head.side_effect = [head_src, head_att, head_sbom]

        result = client.check_artifact_health('quay.io', 'org/repo', DIGEST)

        assert result['artifacts']['sig']['exists'] is True
        assert result['artifacts']['sig']['method'] == 'referrers'
        assert result['artifacts']['src']['exists'] is True
        assert result['artifacts']['src']['method'] == 'tag'
        assert result['artifacts']['att']['exists'] is True
        assert result['artifacts']['att']['method'] == 'tag'
        assert result['artifacts']['sbom']['exists'] is False
        assert result['healthy'] is False
        assert 'sbom' in result['missing']

    @patch.dict(os.environ, _CLEAN_ENV)
    def test_invalid_digest_returns_unhealthy(self):
        client = RegistryClient(token='')
        result = client.check_artifact_health('quay.io', 'org/repo', 'invalid-digest')

        assert result['healthy'] is False
        assert set(result['missing']) == {'sig', 'src', 'att', 'sbom'}
        assert result['artifacts'] == {}

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_referrers_failure_tag_fallback(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        session.get.side_effect = ConnectionError("referrers down")
        # All tags found
        session.head.return_value = _make_response(200)

        result = client.check_artifact_health('quay.io', 'org/repo', DIGEST)

        assert result['artifacts']['sig']['exists'] is True
        assert result['artifacts']['sig']['method'] == 'tag'

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_tag_head_exception_gives_none_exists(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        referrers_resp = _make_response(200, {'manifests': []})
        session.get.return_value = referrers_resp
        session.head.side_effect = TimeoutError("timed out")

        result = client.check_artifact_health('quay.io', 'org/repo', DIGEST)

        for art_type in ('sig', 'src', 'att', 'sbom'):
            assert result['artifacts'][art_type]['exists'] is None

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_mixed_referrer_types(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        referrers_resp = _make_response(200, {
            'manifests': [
                {'artifactType': 'application/vnd.oci.source.image'},
                {'artifactType': 'application/vnd.cyclonedx+json'},
            ],
        })
        # Tag fallback for sig and att
        session.get.return_value = referrers_resp
        session.head.side_effect = [_make_response(200), _make_response(404)]

        result = client.check_artifact_health('quay.io', 'org/repo', DIGEST)

        assert result['artifacts']['src']['exists'] is True
        assert result['artifacts']['sbom']['exists'] is True
        assert result['artifacts']['sig']['exists'] is True
        assert result['artifacts']['att']['exists'] is False
        assert result['healthy'] is False
        assert 'att' in result['missing']


# ---------------------------------------------------------------------------
# check_artifact_health_batch
# ---------------------------------------------------------------------------
class TestCheckArtifactHealthBatch:

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_batch_processes_components(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        # Make referrers return all artifacts found
        session.get.return_value = _make_response(200, {
            'manifests': [
                {'artifactType': 'application/cosign'},
                {'artifactType': 'application/vnd.oci.source'},
                {'artifactType': 'application/in-toto'},
                {'artifactType': 'application/spdx+json'},
            ],
        })

        components = [
            {'name': 'comp-a', 'containerImage': 'quay.io/org/a@' + DIGEST},
            {'name': 'comp-b', 'containerImage': 'quay.io/org/b@' + DIGEST},
        ]

        results = client.check_artifact_health_batch(components, timeout=10)

        assert 'comp-a' in results
        assert 'comp-b' in results

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_skips_components_without_image(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        components = [
            {'name': 'no-image'},
            {'name': 'empty-image', 'containerImage': ''},
        ]

        results = client.check_artifact_health_batch(components, timeout=5)
        assert results == {}

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_skips_tag_references(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        components = [
            {'name': 'tagged', 'containerImage': 'quay.io/org/repo:v1'},
        ]

        results = client.check_artifact_health_batch(components, timeout=5)
        assert results == {}


# ---------------------------------------------------------------------------
# fetch_sarif_batch
# ---------------------------------------------------------------------------
class TestFetchSarifBatch:

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_batch_returns_results_per_component(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        # Return empty referrers for simplicity
        session.get.return_value = _make_response(200, {'manifests': []})

        components = [
            {'name': 'comp-x', 'containerImage': 'quay.io/org/x@' + DIGEST},
        ]

        results = client.fetch_sarif_batch(components, timeout=10)
        assert 'comp-x' in results

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_skips_non_digest_images(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        components = [
            {'name': 'tagged', 'containerImage': 'quay.io/org/repo:latest'},
        ]

        results = client.fetch_sarif_batch(components, timeout=5)
        assert results == {}

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_skips_empty_container_image(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        components = [
            {'name': 'empty', 'containerImage': ''},
            {'name': 'missing'},
        ]

        results = client.fetch_sarif_batch(components, timeout=5)
        assert results == {}


# ---------------------------------------------------------------------------
# _fetch_sarif_with_session
# ---------------------------------------------------------------------------
class TestFetchSarifWithSession:

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_uses_provided_session(self, mock_requests):
        client, _default_session = _client_no_creds(mock_requests)

        custom_session = MagicMock()
        custom_session.get.return_value = _make_response(200, {'manifests': []})

        client._fetch_sarif_with_session('quay.io', 'org/repo', DIGEST, custom_session)

        custom_session.get.assert_called()

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_cache_hit_skips_http(self, mock_requests):
        client, _default_session = _client_no_creds(mock_requests)

        custom_session = MagicMock()

        cache_key = 'quay.io/org/repo@{}'.format(DIGEST)
        client._sarif_cache[cache_key] = [{'ruleId': 'cached'}]

        result = client._fetch_sarif_with_session('quay.io', 'org/repo', DIGEST, custom_session)
        assert result == [{'ruleId': 'cached'}]
        custom_session.get.assert_not_called()


# ---------------------------------------------------------------------------
# format_sarif_summary (static, no mocking needed)
# ---------------------------------------------------------------------------
class TestFormatSarifSummary:

    def test_with_results(self):
        results = [
            {'ruleId': 'CVE-001', 'level': 'error', 'message': 'critical vuln',
             'package': 'openssl', 'fix_version': '3.0.14'},
            {'ruleId': 'CVE-002', 'level': 'warning', 'message': 'high vuln',
             'package': 'curl', 'fix_version': ''},
            {'ruleId': 'CVE-003', 'level': 'note', 'message': 'medium vuln',
             'package': '', 'fix_version': ''},
        ]

        summary = RegistryClient.format_sarif_summary(results)

        assert '=== Structured Scan Results (SARIF) ===' in summary
        assert 'Critical: 1' in summary
        assert 'High: 1' in summary
        assert 'Medium: 1' in summary
        assert 'CVE-001' in summary
        assert '[openssl]' in summary
        assert 'fix: 3.0.14' in summary
        assert 'CVE-002' in summary
        assert '[curl]' in summary

    def test_empty_results(self):
        assert RegistryClient.format_sarif_summary([]) == ''

    def test_truncation_at_max_chars(self):
        results = [
            {'ruleId': 'CVE-{:04d}'.format(i), 'level': 'error',
             'message': 'description ' * 10, 'package': 'pkg', 'fix_version': ''}
            for i in range(20)
        ]

        summary = RegistryClient.format_sarif_summary(results, max_chars=500)
        assert len(summary) <= 520  # 500 + "... (truncated)" suffix
        assert '(truncated)' in summary

    def test_more_than_15_results_shows_count(self):
        results = [
            {'ruleId': 'CVE-{:04d}'.format(i), 'level': 'warning',
             'message': 'vuln', 'package': '', 'fix_version': ''}
            for i in range(20)
        ]

        summary = RegistryClient.format_sarif_summary(results, max_chars=10000)
        assert '... and 5 more vulnerabilities' in summary

    def test_exactly_15_results_no_more_line(self):
        results = [
            {'ruleId': 'CVE-{:04d}'.format(i), 'level': 'warning',
             'message': 'vuln', 'package': '', 'fix_version': ''}
            for i in range(15)
        ]

        summary = RegistryClient.format_sarif_summary(results, max_chars=10000)
        assert '... and' not in summary

    def test_severity_ordering(self):
        results = [
            {'ruleId': 'CVE-NOTE', 'level': 'note', 'message': 'low',
             'package': '', 'fix_version': ''},
            {'ruleId': 'CVE-ERR', 'level': 'error', 'message': 'critical',
             'package': '', 'fix_version': ''},
            {'ruleId': 'CVE-WARN', 'level': 'warning', 'message': 'high',
             'package': '', 'fix_version': ''},
        ]

        summary = RegistryClient.format_sarif_summary(results)
        err_pos = summary.index('CVE-ERR')
        warn_pos = summary.index('CVE-WARN')
        note_pos = summary.index('CVE-NOTE')
        assert err_pos < warn_pos < note_pos

    def test_unknown_level_counts_as_low(self):
        results = [
            {'ruleId': 'CVE-UNK', 'level': 'unknown', 'message': 'odd level',
             'package': '', 'fix_version': ''},
        ]

        summary = RegistryClient.format_sarif_summary(results)
        assert 'Low: 1' in summary

    def test_no_package_or_fix_omits_brackets(self):
        results = [
            {'ruleId': 'CVE-BARE', 'level': 'warning', 'message': 'bare vuln',
             'package': '', 'fix_version': ''},
        ]

        summary = RegistryClient.format_sarif_summary(results)
        assert '[]' not in summary
        assert 'fix:' not in summary


# ---------------------------------------------------------------------------
# HTTP status codes: 401, 403, 404
# ---------------------------------------------------------------------------
class TestHttpStatusCodes:

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_list_tags_401(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(401)

        assert client.list_tags('quay.io', 'org/repo') == []

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_list_tags_403(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(403)

        assert client.list_tags('quay.io', 'org/repo') == []

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_get_manifest_401(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(401)

        assert client.get_manifest('quay.io', 'org/repo', 'v1') is None

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_fetch_sarif_results_403(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.return_value = _make_response(403)

        assert client.fetch_sarif_results('quay.io', 'org/repo', DIGEST) == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
class TestEdgeCases:

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_timeout_on_list_tags(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        import requests as real_requests
        session.get.side_effect = real_requests.exceptions.ReadTimeout("read timed out")

        assert client.list_tags('quay.io', 'org/repo') == []

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_none_json_response_in_tags(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        resp = _make_response(200)
        resp.json.return_value = None
        session.get.return_value = resp

        # None.get('tags', []) raises AttributeError, caught by except block
        result = client.list_tags('quay.io', 'org/repo')
        assert result == []

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_malformed_referrers_response(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        # JSON response without 'manifests' key
        session.get.return_value = _make_response(200, {'unexpected': 'data'})

        result = client.fetch_sarif_results('quay.io', 'org/repo', DIGEST)
        assert result == []

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_check_source_image_all_strategies_fail(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        # referrers exception, src_tag exception, HEAD exception
        session.get.side_effect = [
            ConnectionError("ref fail"),
            ConnectionError("tag fail"),
        ]
        session.head.side_effect = ConnectionError("head fail")

        result = client.check_source_image('quay.io', 'org/repo', DIGEST)
        assert result['exists'] is False

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_resolve_per_arch_no_manifests_key(self, mock_requests):
        client, session = _client_no_creds(mock_requests)

        tags_resp = _make_response(200, {'tags': ['v1']})
        manifest_resp = _make_response(200, {'config': {}})
        session.get.side_effect = [tags_resp, manifest_resp]

        assert client.resolve_per_arch_digest('quay.io', 'org/repo', DIGEST) is None

    @patch('clients.registry_client.requests')
    @patch.dict(os.environ, _CLEAN_ENV)
    def test_fetch_log_artifact_tags_listing_fails(self, mock_requests):
        client, session = _client_no_creds(mock_requests)
        session.get.side_effect = ConnectionError("can't list tags")

        assert client.fetch_log_artifact('quay.io', 'org/repo', 'my-pr') == ''
