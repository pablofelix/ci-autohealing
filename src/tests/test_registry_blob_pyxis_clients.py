"""Comprehensive unit tests for RegistryClient, BlobStore, and PyxisClient.

Covers OCI registry interactions (manifests, referrers, SARIF, source images,
artifact health), local blob storage CRUD, and Pyxis REST API queries.
All external calls are mocked — no real APIs are contacted.
"""

import io
import json
import os
import subprocess
import sys
import tarfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from clients.blob_store import (
    BLOB_THRESHOLD,
    BlobStore,
    _LocalBlobBackend,
    get_blob_store,
    make_blob_key,
    resolve_blob_fields,
    should_offload,
)
from clients.pyxis_client import PyxisClient
from clients.registry_client import RegistryClient


# ---------------------------------------------------------------------------
# RegistryClient — parse_image_ref
# ---------------------------------------------------------------------------
class TestParseImageRef:
    """Static method: parse image URLs into (registry, repository, tag_or_digest)."""

    def test_image_with_tag(self):
        assert RegistryClient.parse_image_ref('quay.io/rh/image:tag') == (
            'quay.io', 'rh/image', 'tag')

    def test_image_with_digest(self):
        assert RegistryClient.parse_image_ref(
            'quay.io/rh/image@sha256:abc123') == (
            'quay.io', 'rh/image', 'sha256:abc123')

    def test_strips_https_prefix(self):
        assert RegistryClient.parse_image_ref(
            'https://quay.io/rh/image:v1') == (
            'quay.io', 'rh/image', 'v1')

    def test_strips_http_prefix(self):
        assert RegistryClient.parse_image_ref(
            'http://quay.io/rh/image:v1') == (
            'quay.io', 'rh/image', 'v1')

    def test_no_tag_defaults_to_latest(self):
        assert RegistryClient.parse_image_ref('quay.io/rh/image') == (
            'quay.io', 'rh/image', 'latest')

    def test_registry_only_no_repo(self):
        assert RegistryClient.parse_image_ref('quay.io') == (
            'quay.io', '', 'latest')

    def test_deep_repository_path(self):
        assert RegistryClient.parse_image_ref(
            'quay.io/org/sub/deep/image:beta') == (
            'quay.io', 'org/sub/deep/image', 'beta')


# ---------------------------------------------------------------------------
# RegistryClient — _get_bearer_token
# ---------------------------------------------------------------------------
class TestGetBearerToken:
    """Token exchange and caching."""

    def test_successful_token_exchange_and_cache(self):
        client = RegistryClient(token='my-creds')
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {'token': 'bearer-xyz'}
        client._session.get = MagicMock(return_value=mock_resp)

        token = client._get_bearer_token('quay.io', 'rh/image')
        assert token == 'bearer-xyz'
        # Second call should use cache, not hit the session again
        token2 = client._get_bearer_token('quay.io', 'rh/image')
        assert token2 == 'bearer-xyz'
        assert client._session.get.call_count == 1

    def test_no_credentials_returns_empty(self):
        client = RegistryClient(token='')
        client._basic_creds = ''
        assert client._get_bearer_token('quay.io', 'repo') == ''

    def test_token_exchange_failure_returns_empty(self):
        client = RegistryClient(token='my-creds')
        client._session.get = MagicMock(side_effect=ConnectionError('refused'))
        assert client._get_bearer_token('quay.io', 'repo') == ''


# ---------------------------------------------------------------------------
# RegistryClient — list_tags
# ---------------------------------------------------------------------------
class TestListTags:
    """Tag listing from registry."""

    def test_returns_tag_list(self):
        client = RegistryClient(token='')
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {'tags': ['v1', 'v2', 'latest']}
        client._session.get = MagicMock(return_value=mock_resp)

        tags = client.list_tags('quay.io', 'org/repo')
        assert tags == ['v1', 'v2', 'latest']

    def test_error_returns_empty_list(self):
        client = RegistryClient(token='')
        client._session.get = MagicMock(side_effect=ConnectionError('timeout'))
        assert client.list_tags('quay.io', 'org/repo') == []

    def test_non_200_returns_empty_list(self):
        client = RegistryClient(token='')
        mock_resp = MagicMock(status_code=401)
        client._session.get = MagicMock(return_value=mock_resp)
        assert client.list_tags('quay.io', 'org/repo') == []


# ---------------------------------------------------------------------------
# RegistryClient — get_manifest
# ---------------------------------------------------------------------------
class TestGetManifest:
    """Manifest fetching."""

    def test_returns_manifest_json(self):
        client = RegistryClient(token='')
        manifest = {'schemaVersion': 2, 'manifests': []}
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = manifest
        client._session.get = MagicMock(return_value=mock_resp)

        result = client.get_manifest('quay.io', 'org/repo', 'sha256:abc')
        assert result == manifest

    def test_not_found_returns_none(self):
        client = RegistryClient(token='')
        mock_resp = MagicMock(status_code=404)
        client._session.get = MagicMock(return_value=mock_resp)
        assert client.get_manifest('quay.io', 'org/repo', 'sha256:abc') is None

    def test_exception_returns_none(self):
        client = RegistryClient(token='')
        client._session.get = MagicMock(side_effect=Exception('network'))
        assert client.get_manifest('quay.io', 'org/repo', 'sha256:abc') is None


# ---------------------------------------------------------------------------
# RegistryClient — resolve_per_arch_digest
# ---------------------------------------------------------------------------
class TestResolvePerArchDigest:
    """Multi-arch manifest list resolution."""

    def test_found_in_manifest_list(self):
        client = RegistryClient(token='')
        target = 'sha256:arch123'
        manifest = {
            'manifests': [{
                'digest': target,
                'platform': {'architecture': 'amd64', 'os': 'linux'},
            }]
        }
        with (patch.object(client, 'list_tags', return_value=['latest']),
              patch.object(client, 'get_manifest', return_value=manifest)):
            result = client.resolve_per_arch_digest('quay.io', 'repo', target)
        assert result['architecture'] == 'amd64'
        assert result['digest'] == target
        assert result['manifest_list_tag'] == 'latest'

    def test_not_found_returns_none(self):
        client = RegistryClient(token='')
        manifest = {
            'manifests': [{
                'digest': 'sha256:other',
                'platform': {'architecture': 'arm64', 'os': 'linux'},
            }]
        }
        with (patch.object(client, 'list_tags', return_value=['latest']),
              patch.object(client, 'get_manifest', return_value=manifest)):
            result = client.resolve_per_arch_digest(
                'quay.io', 'repo', 'sha256:notfound')
        assert result is None

    def test_no_tags_returns_none(self):
        client = RegistryClient(token='')
        with patch.object(client, 'list_tags', return_value=[]):
            result = client.resolve_per_arch_digest(
                'quay.io', 'repo', 'sha256:abc')
        assert result is None


# ---------------------------------------------------------------------------
# RegistryClient — _extract_logs_from_tarball (static)
# ---------------------------------------------------------------------------
class TestExtractLogsFromTarball:
    """Tarball extraction for log artifacts."""

    @staticmethod
    def _make_tarball(filename, content):
        """Build a gzipped tar archive in memory."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w:gz') as tar:
            data = content.encode('utf-8')
            info = tarfile.TarInfo(name=filename)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def test_valid_tarball_with_txt(self):
        data = self._make_tarball('build/all-logs.txt', 'Hello logs')
        assert RegistryClient._extract_logs_from_tarball(data) == 'Hello logs'

    def test_valid_tarball_any_txt_file(self):
        data = self._make_tarball('output.txt', 'Some output')
        assert RegistryClient._extract_logs_from_tarball(data) == 'Some output'

    def test_invalid_data_returns_empty(self):
        assert RegistryClient._extract_logs_from_tarball(b'not a tarball') == ''


# ---------------------------------------------------------------------------
# RegistryClient — fetch_log_artifact
# ---------------------------------------------------------------------------
class TestFetchLogArtifact:
    """End-to-end log artifact fetching."""

    @staticmethod
    def _make_tarball(filename, content):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w:gz') as tar:
            data = content.encode('utf-8')
            info = tarfile.TarInfo(name=filename)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def test_happy_path(self):
        client = RegistryClient(token='')
        tar_data = self._make_tarball('all-logs.txt', 'build output here')

        manifest_resp = MagicMock(status_code=200)
        manifest_resp.json.return_value = {
            'layers': [{'digest': 'sha256:layer1'}]
        }
        blob_resp = MagicMock(status_code=200, content=tar_data)

        with patch.object(client, 'list_tags',
                          return_value=['pr-abc-build-logs', 'pr-abc-other']):

            def mock_api_get(registry, path, **kwargs):
                if 'manifests' in path:
                    return manifest_resp
                if 'blobs' in path:
                    return blob_resp
                return MagicMock(status_code=404)

            with patch.object(client, '_api_get', side_effect=mock_api_get):
                result = client.fetch_log_artifact('quay.io', 'org/repo', 'pr-abc')
        assert result == 'build output here'

    def test_no_matching_tags(self):
        client = RegistryClient(token='')
        with patch.object(client, 'list_tags', return_value=['unrelated-tag']):
            assert client.fetch_log_artifact('quay.io', 'repo', 'pr-xyz') == ''

    def test_failed_manifest_fetch(self):
        client = RegistryClient(token='')
        manifest_resp = MagicMock(status_code=500)
        with (patch.object(client, 'list_tags', return_value=['pr-abc-logs']),
              patch.object(client, '_api_get', return_value=manifest_resp)):
            assert client.fetch_log_artifact('quay.io', 'repo', 'pr-abc') == ''


# ---------------------------------------------------------------------------
# RegistryClient — fetch_sarif_results & cache
# ---------------------------------------------------------------------------
class TestFetchSarifResults:
    """SARIF vulnerability results via referrers API."""

    def test_happy_path(self):
        client = RegistryClient(token='')
        sarif_data = json.dumps({
            'runs': [{
                'tool': {'driver': {'rules': [
                    {'id': 'CVE-2024-001', 'properties': {
                        'package': 'openssl', 'fixed_version': '1.1.1w'}}
                ]}},
                'results': [{
                    'ruleId': 'CVE-2024-001', 'level': 'error',
                    'message': {'text': 'Critical vuln'},
                }]
            }]
        }).encode()

        referrers_resp = MagicMock(status_code=200)
        referrers_resp.json.return_value = {
            'manifests': [{'digest': 'sha256:sarif1',
                           'artifactType': 'application/sarif+json'}]
        }
        manifest_resp = MagicMock(status_code=200)
        manifest_resp.json.return_value = {
            'layers': [{'digest': 'sha256:blob1'}]
        }
        blob_resp = MagicMock(status_code=200, content=sarif_data)

        def mock_api_get(registry, path, **kwargs):
            if 'referrers' in path:
                return referrers_resp
            if 'manifests' in path:
                return manifest_resp
            if 'blobs' in path:
                return blob_resp
            return MagicMock(status_code=404)

        with patch.object(client, '_api_get', side_effect=mock_api_get):
            results = client.fetch_sarif_results('quay.io', 'repo', 'sha256:img1')

        assert len(results) == 1
        assert results[0]['ruleId'] == 'CVE-2024-001'
        assert results[0]['package'] == 'openssl'
        assert results[0]['fix_version'] == '1.1.1w'
        assert results[0]['level'] == 'error'

    def test_no_sarif_refs_returns_empty_and_caches(self):
        client = RegistryClient(token='')
        referrers_resp = MagicMock(status_code=200)
        referrers_resp.json.return_value = {
            'manifests': [{'digest': 'sha256:sig1',
                           'artifactType': 'application/cosign'}]
        }
        with patch.object(client, '_api_get', return_value=referrers_resp):
            results = client.fetch_sarif_results('quay.io', 'repo', 'sha256:img2')
        assert results == []
        # Second call should hit cache
        results2 = client.fetch_sarif_results('quay.io', 'repo', 'sha256:img2')
        assert results2 == []

    def test_cache_hit_returns_cached(self):
        client = RegistryClient(token='')
        cached = [{'ruleId': 'CVE-cached', 'level': 'warning',
                    'message': 'cached', 'package': '', 'fix_version': ''}]
        client._sarif_cache['quay.io/repo@sha256:cached'] = cached
        results = client.fetch_sarif_results('quay.io', 'repo', 'sha256:cached')
        assert results is cached


# ---------------------------------------------------------------------------
# RegistryClient — _parse_sarif (static)
# ---------------------------------------------------------------------------
class TestParseSarif:
    """SARIF JSON parsing."""

    def test_valid_sarif(self):
        sarif = json.dumps({
            'runs': [{
                'tool': {'driver': {'rules': [
                    {'id': 'CVE-100', 'properties': {
                        'affected_package': 'libcurl',
                        'fixedVersion': '7.88',
                    }},
                ]}},
                'results': [
                    {'ruleId': 'CVE-100', 'level': 'warning',
                     'message': {'text': 'A vulnerability'}},
                    {'ruleId': 'CVE-200', 'level': 'note',
                     'message': {'text': 'Minor issue'}},
                ],
            }]
        }).encode()
        results = RegistryClient._parse_sarif(sarif)
        assert len(results) == 2
        assert results[0]['ruleId'] == 'CVE-100'
        assert results[0]['package'] == 'libcurl'
        assert results[0]['fix_version'] == '7.88'
        # CVE-200 has no matching rule metadata
        assert results[1]['package'] == ''

    def test_invalid_json(self):
        assert RegistryClient._parse_sarif(b'not json') == []

    def test_empty_runs(self):
        assert RegistryClient._parse_sarif(json.dumps({'runs': []}).encode()) == []


# ---------------------------------------------------------------------------
# RegistryClient — check_source_image
# ---------------------------------------------------------------------------
class TestCheckSourceImage:
    """Source container image existence checks."""

    def test_found_via_referrers(self):
        client = RegistryClient(token='')
        referrers_resp = MagicMock(status_code=200)
        referrers_resp.json.return_value = {
            'manifests': [{'artifactType': 'application/source-image'}]
        }
        with patch.object(client, '_api_get', return_value=referrers_resp):
            result = client.check_source_image('quay.io', 'repo', 'sha256:abc123')
        assert result['exists'] is True
        assert result['method'] == 'referrers'

    def test_found_via_src_tag(self):
        client = RegistryClient(token='')
        # Referrers returns no source refs
        referrers_resp = MagicMock(status_code=200)
        referrers_resp.json.return_value = {'manifests': []}
        # .src tag check returns 200
        src_tag_resp = MagicMock(status_code=200)


        def mock_api_get(registry, path, **kwargs):
            if 'referrers' in path:
                return referrers_resp
            if 'manifests' in path:
                return src_tag_resp
            return MagicMock(status_code=404)

        with patch.object(client, '_api_get', side_effect=mock_api_get):
            result = client.check_source_image('quay.io', 'repo', 'sha256:abc123')
        assert result['exists'] is True
        assert result['method'] == 'src_tag'

    def test_not_found(self):
        client = RegistryClient(token='')
        referrers_resp = MagicMock(status_code=200)
        referrers_resp.json.return_value = {'manifests': []}
        src_tag_resp = MagicMock(status_code=404)
        head_resp = MagicMock(status_code=404)

        def mock_api_get(registry, path, **kwargs):
            if 'referrers' in path:
                return referrers_resp
            return src_tag_resp

        with (patch.object(client, '_api_get', side_effect=mock_api_get),
              patch.object(client._session, 'head', return_value=head_resp)):
            result = client.check_source_image(
                'quay.io', 'repo', 'sha256:abc123')
        assert result['exists'] is False

    def test_invalid_digest_format(self):
        client = RegistryClient(token='')
        result = client.check_source_image('quay.io', 'repo', 'not-a-digest')
        assert result['exists'] is None
        assert result['method'] == 'skipped'


# ---------------------------------------------------------------------------
# RegistryClient — check_artifact_health
# ---------------------------------------------------------------------------
class TestCheckArtifactHealth:
    """OCI artifact health checking (sig, src, att, sbom)."""

    def test_all_found_via_referrers(self):
        client = RegistryClient(token='')
        referrers_resp = MagicMock(status_code=200)
        referrers_resp.json.return_value = {
            'manifests': [
                {'artifactType': 'application/cosign-signature'},
                {'artifactType': 'application/source-image'},
                {'artifactType': 'application/in-toto+json'},
                {'artifactType': 'application/spdx+json'},
            ]
        }
        with (patch.object(client, '_api_get', return_value=referrers_resp),
              patch.object(client._session, 'head')):
            result = client.check_artifact_health(
                'quay.io', 'repo', 'sha256:abc123')
        assert result['healthy'] is True
        assert result['missing'] == []
        assert result['artifacts']['sig']['exists'] is True
        assert result['artifacts']['sig']['method'] == 'referrers'

    def test_some_missing_found_via_tag_fallback(self):
        client = RegistryClient(token='')
        # Referrers only finds sig and src
        referrers_resp = MagicMock(status_code=200)
        referrers_resp.json.return_value = {
            'manifests': [
                {'artifactType': 'application/cosign-signature'},
                {'artifactType': 'application/source-container'},
            ]
        }
        # Tag fallback: att found, sbom not found
        def mock_head(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200 if '.att' in url else 404
            return resp

        with (patch.object(client, '_api_get', return_value=referrers_resp),
              patch.object(client._session, 'head', side_effect=mock_head)):
            result = client.check_artifact_health(
                'quay.io', 'repo', 'sha256:abc123')
        assert result['healthy'] is False
        assert 'sbom' in result['missing']
        assert result['artifacts']['att']['exists'] is True
        assert result['artifacts']['att']['method'] == 'tag'

    def test_all_missing(self):
        client = RegistryClient(token='')
        referrers_resp = MagicMock(status_code=200)
        referrers_resp.json.return_value = {'manifests': []}
        head_resp = MagicMock(status_code=404)

        with (patch.object(client, '_api_get', return_value=referrers_resp),
              patch.object(client._session, 'head', return_value=head_resp)):
            result = client.check_artifact_health(
                'quay.io', 'repo', 'sha256:abc123')
        assert result['healthy'] is False
        assert sorted(result['missing']) == ['att', 'sbom', 'sig', 'src']

    def test_invalid_digest(self):
        client = RegistryClient(token='')
        result = client.check_artifact_health('quay.io', 'repo', 'invalid')
        assert result['healthy'] is False
        assert result['missing'] == ['sig', 'src', 'att', 'sbom']
        assert result['artifacts'] == {}


# ---------------------------------------------------------------------------
# RegistryClient — format_sarif_summary (static)
# ---------------------------------------------------------------------------
class TestFormatSarifSummary:
    """SARIF summary formatting."""

    def test_empty_results(self):
        assert RegistryClient.format_sarif_summary([]) == ''

    def test_formats_severity_counts(self):
        results = [
            {'ruleId': 'CVE-1', 'level': 'error', 'message': 'crit',
             'package': '', 'fix_version': ''},
            {'ruleId': 'CVE-2', 'level': 'warning', 'message': 'high',
             'package': '', 'fix_version': ''},
        ]
        text = RegistryClient.format_sarif_summary(results)
        assert 'Critical: 1' in text
        assert 'High: 1' in text

    def test_includes_package_and_fix(self):
        results = [
            {'ruleId': 'CVE-99', 'level': 'error', 'message': 'vuln',
             'package': 'openssl', 'fix_version': '3.0.12'},
        ]
        text = RegistryClient.format_sarif_summary(results)
        assert '[openssl]' in text
        assert 'fix: 3.0.12' in text

    def test_truncation_at_max_chars(self):
        results = [
            {'ruleId': 'CVE-{}'.format(i), 'level': 'error',
             'message': 'x' * 100, 'package': '', 'fix_version': ''}
            for i in range(20)
        ]
        text = RegistryClient.format_sarif_summary(results, max_chars=500)
        assert len(text) <= 520  # 500 + truncation suffix
        assert '(truncated)' in text

    def test_more_than_15_shows_remainder(self):
        results = [
            {'ruleId': 'CVE-{}'.format(i), 'level': 'note',
             'message': 'issue', 'package': '', 'fix_version': ''}
            for i in range(20)
        ]
        text = RegistryClient.format_sarif_summary(results, max_chars=10000)
        assert '... and 5 more vulnerabilities' in text


# ---------------------------------------------------------------------------
# BlobStore — _LocalBlobBackend
# ---------------------------------------------------------------------------
class TestLocalBlobBackendPytest:
    """Local filesystem blob backend using pytest tmp_path."""

    def test_put_and_get_roundtrip(self, tmp_path):
        backend = _LocalBlobBackend(str(tmp_path))
        backend.put('test/file.txt', b'hello world')
        assert backend.get('test/file.txt') == b'hello world'

    def test_get_nonexistent_returns_none(self, tmp_path):
        backend = _LocalBlobBackend(str(tmp_path))
        assert backend.get('missing.txt') is None

    def test_exists_true_false(self, tmp_path):
        backend = _LocalBlobBackend(str(tmp_path))
        assert backend.exists('file.txt') is False
        backend.put('file.txt', b'data')
        assert backend.exists('file.txt') is True

    def test_delete_existing_key(self, tmp_path):
        backend = _LocalBlobBackend(str(tmp_path))
        backend.put('file.txt', b'data')
        assert backend.exists('file.txt') is True
        backend.delete('file.txt')
        assert backend.exists('file.txt') is False

    def test_creates_directories(self, tmp_path):
        backend = _LocalBlobBackend(str(tmp_path))
        backend.put('a/b/c/d/file.txt', b'deep')
        assert backend.get('a/b/c/d/file.txt') == b'deep'


# ---------------------------------------------------------------------------
# BlobStore — high-level API
# ---------------------------------------------------------------------------
class TestBlobStoreHighLevel:
    """BlobStore wraps backends with string/bytes encoding."""

    def test_default_backend_is_local(self, tmp_path):
        with patch.dict(os.environ, {'BLOB_STORE': 'local',
                                     'BLOB_LOCAL_ROOT': str(tmp_path)}):
            store = BlobStore()
        assert store.backend_name == 'local'

    def test_string_data_auto_encoded(self, tmp_path):
        store = BlobStore(backend='local', local_root=str(tmp_path))
        store.put('key.txt', 'string data')
        assert store.get('key.txt') == 'string data'

    def test_get_returns_string(self, tmp_path):
        store = BlobStore(backend='local', local_root=str(tmp_path))
        store.put('key.txt', b'bytes data')
        result = store.get('key.txt')
        assert isinstance(result, str)
        assert result == 'bytes data'

    def test_get_bytes_returns_bytes(self, tmp_path):
        store = BlobStore(backend='local', local_root=str(tmp_path))
        store.put('key.txt', b'\x00\x01')
        result = store.get_bytes('key.txt')
        assert isinstance(result, bytes)
        assert result == b'\x00\x01'

    def test_minio_backend_selection(self):
        with patch('clients.blob_store._MinioBlobBackend') as mock_cls:
            mock_cls.return_value = MagicMock()
            store = BlobStore(backend='minio', endpoint='localhost:9000',
                              access_key='key', secret_key='secret')
            assert store.backend_name == 'minio'
            mock_cls.assert_called_once()


# ---------------------------------------------------------------------------
# BlobStore — helper functions
# ---------------------------------------------------------------------------
class TestBlobHelpers:
    """make_blob_key, should_offload, resolve_blob_fields."""

    def test_make_blob_key_format(self):
        key = make_blob_key('build-failures', 'comp-a', 'comp-a-on-push-xyz',
                            'build_logs')
        assert key == 'build-failures/comp-a/comp-a-on-push-xyz/build_logs.txt'

    def test_make_blob_key_custom_ext(self):
        key = make_blob_key('conforma', 'c', 'pr', 'details', 'json')
        assert key == 'conforma/c/pr/details.json'

    def test_should_offload_true_above_threshold(self):
        assert should_offload('x' * (BLOB_THRESHOLD + 1)) is True

    def test_should_offload_false_below_threshold(self):
        assert should_offload('small') is False

    def test_should_offload_none_is_false(self):
        assert should_offload(None) is False

    def test_resolve_blob_fields_fetches_from_store(self, tmp_path):
        store = BlobStore(backend='local', local_root=str(tmp_path))
        key = 'test/comp/pr/build_logs.txt'
        store.put(key, 'resolved log data')
        get_blob_store._instance = store
        try:
            row = {'build_logs': None, 'blob_refs': {'build_logs': key}}
            resolve_blob_fields(row)
            assert row['build_logs'] == 'resolved log data'
        finally:
            del get_blob_store._instance

    def test_resolve_blob_fields_parses_json(self, tmp_path):
        store = BlobStore(backend='local', local_root=str(tmp_path))
        key = 'test/comp/pr/commit_context.json'
        store.put(key, json.dumps({'files': ['a.py']}))
        get_blob_store._instance = store
        try:
            row = {'commit_context': None, 'blob_refs': {'commit_context': key}}
            resolve_blob_fields(row, fields=('commit_context',))
            assert row['commit_context'] == {'files': ['a.py']}
        finally:
            del get_blob_store._instance


# ---------------------------------------------------------------------------
# PyxisClient — _get
# ---------------------------------------------------------------------------
class TestPyxisGet:
    """Low-level curl-based GET requests."""

    @patch('clients.pyxis_client.subprocess.run')
    def test_success_returns_json(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"data": [1]}', stderr='')
        client = PyxisClient.__new__(PyxisClient)
        client._base_url = 'https://pyxis.example.com'
        client._target = 'prod'
        result = client._get('/test')
        assert result == {'data': [1]}

    @patch('clients.pyxis_client.subprocess.run')
    def test_404_status_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"status": 404, "detail": "Not Found"}',
            stderr='')
        client = PyxisClient.__new__(PyxisClient)
        client._base_url = 'https://pyxis.example.com'
        client._target = 'prod'
        assert client._get('/missing') is None

    @patch('clients.pyxis_client.subprocess.run')
    def test_curl_failure_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=7, stdout='', stderr='Connection refused')
        client = PyxisClient.__new__(PyxisClient)
        client._base_url = 'https://pyxis.example.com'
        client._target = 'prod'
        assert client._get('/fail') is None

    @patch('clients.pyxis_client.subprocess.run')
    def test_timeout_returns_none(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='curl', timeout=30)
        client = PyxisClient.__new__(PyxisClient)
        client._base_url = 'https://pyxis.example.com'
        client._target = 'prod'
        assert client._get('/slow') is None

    @patch('clients.pyxis_client.subprocess.run')
    def test_invalid_json_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='<html>Error</html>', stderr='')
        client = PyxisClient.__new__(PyxisClient)
        client._base_url = 'https://pyxis.example.com'
        client._target = 'prod'
        assert client._get('/bad-json') is None


# ---------------------------------------------------------------------------
# PyxisClient — check_image
# ---------------------------------------------------------------------------
class TestPyxisCheckImage:
    """Image existence checks via Pyxis filter API."""

    def test_found(self):
        client = PyxisClient.__new__(PyxisClient)
        client._base_url = 'https://pyxis.example.com'
        client._target = 'prod'
        image_data = {'_id': 'img1', 'repositories': []}
        with patch.object(client, '_get',
                          return_value={'total': 1, 'data': [image_data]}):
            result = client.check_image('registry.io', 'org/repo', 'sha256:abc')
        assert result == image_data

    def test_not_found(self):
        client = PyxisClient.__new__(PyxisClient)
        client._base_url = 'https://pyxis.example.com'
        client._target = 'prod'
        with patch.object(client, '_get',
                          return_value={'total': 0, 'data': []}):
            result = client.check_image('registry.io', 'org/repo', 'sha256:abc')
        assert result is None

    def test_error(self):
        client = PyxisClient.__new__(PyxisClient)
        client._base_url = 'https://pyxis.example.com'
        client._target = 'prod'
        with patch.object(client, '_get', return_value=None):
            result = client.check_image('registry.io', 'org/repo', 'sha256:abc')
        assert result is None


# ---------------------------------------------------------------------------
# PyxisClient — check_images_batch
# ---------------------------------------------------------------------------
class TestPyxisCheckImagesBatch:
    """Batch image checking."""

    def test_multiple_images(self):
        client = PyxisClient.__new__(PyxisClient)
        client._base_url = 'https://pyxis.example.com'
        client._target = 'prod'
        images = [
            {'name': 'comp-a', 'registry': 'r.io', 'repository': 'org/a',
             'digest': 'sha256:aaa'},
            {'name': 'comp-b', 'registry': 'r.io', 'repository': 'org/b',
             'digest': 'sha256:bbb'},
        ]
        with patch.object(client, 'check_image',
                          side_effect=[{'_id': 'found'}, None]):
            results = client.check_images_batch(images)
        assert len(results) == 2
        assert results[0]['status'] == 'found'
        assert results[1]['status'] == 'missing'

    def test_mix_with_error(self):
        client = PyxisClient.__new__(PyxisClient)
        client._base_url = 'https://pyxis.example.com'
        client._target = 'prod'
        images = [
            {'name': 'comp-a', 'registry': 'r.io', 'repository': 'org/a',
             'digest': 'sha256:aaa'},
            {'name': 'comp-b', 'registry': 'r.io', 'repository': 'org/b',
             'digest': 'sha256:bbb'},
        ]
        with patch.object(client, 'check_image',
                          side_effect=[{'_id': 'ok'}, Exception('boom')]):
            results = client.check_images_batch(images)
        assert results[0]['status'] == 'found'
        assert results[1]['status'] == 'error'


# ---------------------------------------------------------------------------
# PyxisClient — get_advisories
# ---------------------------------------------------------------------------
class TestPyxisGetAdvisories:
    """Advisory queries."""

    def test_returns_advisories(self):
        client = PyxisClient.__new__(PyxisClient)
        client._base_url = 'https://pyxis.example.com'
        client._target = 'prod'
        advisories = [{'errata_id': 'RHSA-2024:001'}]
        with patch.object(client, '_get',
                          return_value={'data': advisories}):
            result = client.get_advisories()
        assert result == advisories

    def test_with_filter(self):
        client = PyxisClient.__new__(PyxisClient)
        client._base_url = 'https://pyxis.example.com'
        client._target = 'prod'
        with patch.object(client, '_get',
                          return_value={'data': []}) as mock_get:
            client.get_advisories(filter_str='errata_id==RHSA-2024:001')
            mock_get.assert_called_once()
            call_args = mock_get.call_args
            # _get is called as _get(path, params=params)
            params = call_args[1].get('params') if call_args[1] else call_args[0][1]
            assert params['filter'] == 'errata_id==RHSA-2024:001'

    def test_error_returns_empty(self):
        client = PyxisClient.__new__(PyxisClient)
        client._base_url = 'https://pyxis.example.com'
        client._target = 'prod'
        with patch.object(client, '_get', return_value=None):
            assert client.get_advisories() == []
