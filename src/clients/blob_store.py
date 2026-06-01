"""Blob storage client — MinIO/S3 or local filesystem.

Write-once blob storage for large data (build logs, commit context, violation
details). PostgreSQL stores a reference key; the blob lives here.

Backend selection via BLOB_STORE env var:
  - "minio" → MinIO/S3 (requires minio Python package)
  - "local" → local filesystem (~/.ic/blobs/) — default
"""

import os

from logger import setup_logger

logger = setup_logger(__name__)

BLOB_THRESHOLD = int(os.environ.get('BLOB_THRESHOLD', '51200'))  # 50 KB


def _default_local_root():
    return os.path.join(os.path.expanduser('~'), '.ic', 'blobs')


class BlobStore:
    """Unified blob storage with MinIO and local filesystem backends."""

    def __init__(self, backend=None, **kwargs):
        self._backend_name = backend or os.environ.get('BLOB_STORE', 'local')
        if self._backend_name == 'minio':
            self._backend = _MinioBlobBackend(**kwargs)
        else:
            local_root = kwargs.get('local_root') or os.environ.get(
                'BLOB_LOCAL_ROOT', _default_local_root())
            self._backend = _LocalBlobBackend(local_root)

    @property
    def backend_name(self):
        return self._backend_name

    def put(self, key, data, content_type='application/octet-stream'):
        """Store blob. Returns the key."""
        if isinstance(data, str):
            data = data.encode('utf-8')
        self._backend.put(key, data, content_type)
        return key

    def get(self, key):
        """Retrieve blob as string. Returns None if not found."""
        data = self._backend.get(key)
        if data is None:
            return None
        if isinstance(data, bytes):
            return data.decode('utf-8')
        return data

    def get_bytes(self, key):
        """Retrieve blob as bytes. Returns None if not found."""
        return self._backend.get(key)

    def exists(self, key):
        """Check if blob exists."""
        return self._backend.exists(key)

    def delete(self, key):
        """Delete a blob."""
        self._backend.delete(key)


class _LocalBlobBackend:
    """Filesystem-backed blob storage."""

    def __init__(self, root):
        self._root = root

    def _path(self, key):
        return os.path.join(self._root, key)

    def put(self, key, data, content_type='application/octet-stream'):
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)

    def get(self, key):
        path = self._path(key)
        if not os.path.exists(path):
            return None
        with open(path, 'rb') as f:
            return f.read()

    def exists(self, key):
        return os.path.exists(self._path(key))

    def delete(self, key):
        path = self._path(key)
        if os.path.exists(path):
            os.remove(path)


class _MinioBlobBackend:
    """MinIO/S3-backed blob storage."""

    def __init__(self, **kwargs):
        self._endpoint = kwargs.get('endpoint') or os.environ.get('MINIO_ENDPOINT', 'localhost:9000')
        self._access_key = kwargs.get('access_key') or os.environ.get('MINIO_ACCESS_KEY', '')
        self._secret_key = kwargs.get('secret_key') or os.environ.get('MINIO_SECRET_KEY', '')
        self._bucket = kwargs.get('bucket') or os.environ.get('MINIO_BUCKET', 'ic-data')
        self._secure = kwargs.get('secure')
        if self._secure is None:
            self._secure = os.environ.get('MINIO_SECURE', 'false').lower() == 'true'
        self._client = None

    def _get_client(self):
        if self._client is None:
            from minio import Minio
            self._client = Minio(
                self._endpoint,
                access_key=self._access_key,
                secret_key=self._secret_key,
                secure=self._secure,
            )
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                logger.info("Created MinIO bucket: %s", self._bucket)
        return self._client

    def put(self, key, data, content_type='application/octet-stream'):
        import io
        client = self._get_client()
        stream = io.BytesIO(data)
        client.put_object(self._bucket, key, stream, len(data),
                          content_type=content_type)

    def get(self, key):
        try:
            client = self._get_client()
            response = client.get_object(self._bucket, key)
            return response.read()
        except Exception:
            return None
        finally:
            try:
                response.close()
                response.release_conn()
            except Exception:
                pass

    def exists(self, key):
        try:
            client = self._get_client()
            client.stat_object(self._bucket, key)
            return True
        except Exception:
            return False

    def delete(self, key):
        try:
            client = self._get_client()
            client.remove_object(self._bucket, key)
        except Exception:
            pass


def make_blob_key(table, component, pipelinerun, field, ext='txt'):
    """Generate a storage key for a blob.

    Example: build-failures/odh-vllm-v3-5/odh-vllm-on-push-abc12/build_logs.txt
    """
    return '{}/{}/{}/{}.{}'.format(table, component, pipelinerun, field, ext)


def get_blob_store():
    """Get a singleton BlobStore instance."""
    if not hasattr(get_blob_store, '_instance'):
        get_blob_store._instance = BlobStore()
    return get_blob_store._instance


def should_offload(data):
    """Return True if data exceeds the blob threshold and should be stored externally."""
    if data is None:
        return False
    size = len(data.encode('utf-8')) if isinstance(data, str) else len(data)
    return size > BLOB_THRESHOLD


def resolve_blob_fields(row, fields=('build_logs', 'commit_context')):
    """Fetch blob data for any field that was offloaded to external storage.

    Checks blob_refs dict in row; if a field is NULL but has a blob ref,
    fetches the data from the blob store. Modifies row in-place.
    """
    refs = row.get('blob_refs') or {}
    if not refs:
        return row
    store = get_blob_store()
    for field in fields:
        if row.get(field) is None and field in refs:
            row[field] = store.get(refs[field])
    return row
