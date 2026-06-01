"""Unit tests for blob storage client."""

import shutil
import tempfile
import unittest

from clients.blob_store import (
    BlobStore, make_blob_key, should_offload, resolve_blob_fields,
    get_blob_store, BLOB_THRESHOLD,
)


class TestLocalBlobBackend(unittest.TestCase):
    """Test local filesystem blob backend."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = BlobStore(backend='local', local_root=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_put_and_get(self):
        key = 'test/component/pr-abc/build_logs.txt'
        self.store.put(key, 'hello world')
        self.assertEqual(self.store.get(key), 'hello world')

    def test_put_bytes(self):
        key = 'test/data.bin'
        self.store.put(key, b'\x00\x01\x02')
        self.assertEqual(self.store.get_bytes(key), b'\x00\x01\x02')

    def test_put_string_encoded_as_utf8(self):
        key = 'test/unicode.txt'
        self.store.put(key, 'café ☕')
        self.assertEqual(self.store.get(key), 'café ☕')

    def test_get_nonexistent_returns_none(self):
        self.assertIsNone(self.store.get('does/not/exist.txt'))

    def test_get_bytes_nonexistent_returns_none(self):
        self.assertIsNone(self.store.get_bytes('does/not/exist.txt'))

    def test_exists(self):
        key = 'test/exists.txt'
        self.assertFalse(self.store.exists(key))
        self.store.put(key, 'data')
        self.assertTrue(self.store.exists(key))

    def test_delete(self):
        key = 'test/delete-me.txt'
        self.store.put(key, 'data')
        self.assertTrue(self.store.exists(key))
        self.store.delete(key)
        self.assertFalse(self.store.exists(key))

    def test_delete_nonexistent_is_noop(self):
        self.store.delete('does/not/exist.txt')

    def test_put_creates_subdirectories(self):
        key = 'deep/nested/path/file.txt'
        self.store.put(key, 'nested data')
        self.assertEqual(self.store.get(key), 'nested data')

    def test_put_returns_key(self):
        key = 'test/return-key.txt'
        result = self.store.put(key, 'data')
        self.assertEqual(result, key)

    def test_backend_name(self):
        self.assertEqual(self.store.backend_name, 'local')


class TestMakeBlobKey(unittest.TestCase):
    """Test blob key generation."""

    def test_basic_key(self):
        key = make_blob_key('build-failures', 'my-comp', 'my-comp-on-push-abc', 'build_logs')
        self.assertEqual(key, 'build-failures/my-comp/my-comp-on-push-abc/build_logs.txt')

    def test_json_extension(self):
        key = make_blob_key('conforma', 'my-comp', 'my-comp-on-push-abc',
                            'violation_details', 'json')
        self.assertEqual(key, 'conforma/my-comp/my-comp-on-push-abc/violation_details.json')

    def test_custom_extension(self):
        key = make_blob_key('build-failures', 'c', 'pr', 'data', 'yaml')
        self.assertEqual(key, 'build-failures/c/pr/data.yaml')


class TestShouldOffload(unittest.TestCase):
    """Test threshold-based offloading decisions."""

    def test_none_returns_false(self):
        self.assertFalse(should_offload(None))

    def test_small_string_returns_false(self):
        self.assertFalse(should_offload('small data'))

    def test_exact_threshold_returns_false(self):
        data = 'x' * BLOB_THRESHOLD
        self.assertFalse(should_offload(data))

    def test_above_threshold_returns_true(self):
        data = 'x' * (BLOB_THRESHOLD + 1)
        self.assertTrue(should_offload(data))

    def test_bytes_above_threshold(self):
        data = b'x' * (BLOB_THRESHOLD + 1)
        self.assertTrue(should_offload(data))

    def test_empty_string_returns_false(self):
        self.assertFalse(should_offload(''))

    def test_multibyte_chars_use_byte_length(self):
        data = '☕' * (BLOB_THRESHOLD // 3 + 1)
        self.assertTrue(should_offload(data))


class TestResolveBlobFields(unittest.TestCase):
    """Test transparent blob field resolution."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = BlobStore(backend='local', local_root=self.tmpdir)
        get_blob_store._instance = self.store

    def tearDown(self):
        shutil.rmtree(self.tmpdir)
        if hasattr(get_blob_store, '_instance'):
            del get_blob_store._instance

    def test_no_blob_refs_returns_unchanged(self):
        row = {'build_logs': 'inline data', 'blob_refs': None}
        resolve_blob_fields(row)
        self.assertEqual(row['build_logs'], 'inline data')

    def test_empty_blob_refs_returns_unchanged(self):
        row = {'build_logs': 'inline data', 'blob_refs': {}}
        resolve_blob_fields(row)
        self.assertEqual(row['build_logs'], 'inline data')

    def test_resolves_offloaded_field(self):
        key = 'test/comp/pr/build_logs.txt'
        self.store.put(key, 'fetched from blob store')
        row = {'build_logs': None, 'blob_refs': {'build_logs': key}}
        resolve_blob_fields(row)
        self.assertEqual(row['build_logs'], 'fetched from blob store')

    def test_does_not_override_inline_data(self):
        key = 'test/comp/pr/build_logs.txt'
        self.store.put(key, 'blob data')
        row = {'build_logs': 'inline data', 'blob_refs': {'build_logs': key}}
        resolve_blob_fields(row)
        self.assertEqual(row['build_logs'], 'inline data')

    def test_resolves_specific_fields_only(self):
        key = 'test/comp/pr/violation_details.json'
        self.store.put(key, '{"rules": []}')
        row = {
            'violation_details': None,
            'build_logs': None,
            'blob_refs': {'violation_details': key},
        }
        resolve_blob_fields(row, fields=('violation_details',))
        self.assertEqual(row['violation_details'], '{"rules": []}')
        self.assertIsNone(row['build_logs'])

    def test_missing_blob_returns_none(self):
        row = {'build_logs': None, 'blob_refs': {'build_logs': 'nonexistent/key.txt'}}
        resolve_blob_fields(row)
        self.assertIsNone(row['build_logs'])

    def test_no_blob_refs_key_in_row(self):
        row = {'build_logs': 'data'}
        resolve_blob_fields(row)
        self.assertEqual(row['build_logs'], 'data')


class TestGetBlobStoreSingleton(unittest.TestCase):
    """Test singleton factory."""

    def setUp(self):
        if hasattr(get_blob_store, '_instance'):
            del get_blob_store._instance

    def tearDown(self):
        if hasattr(get_blob_store, '_instance'):
            del get_blob_store._instance

    def test_returns_same_instance(self):
        a = get_blob_store()
        b = get_blob_store()
        self.assertIs(a, b)

    def test_default_backend_is_local(self):
        store = get_blob_store()
        self.assertEqual(store.backend_name, 'local')


if __name__ == '__main__':
    unittest.main()
