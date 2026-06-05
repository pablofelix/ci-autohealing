"""TTL-based disk cache for Git ref SHAs.

Stores branch HEAD SHAs in a JSON file so repeated `ic stale` runs
don't burn GitHub API quota on unchanged refs. Each entry expires
after a configurable TTL (default 5 minutes).
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from logger import setup_logger

logger = setup_logger(__name__)


@dataclass(frozen=True)
class CacheConfig:
    cache_dir: Path
    ttl_seconds: int = 300

    @classmethod
    def from_env(cls):
        import os
        return cls(
            cache_dir=Path(os.environ.get('IC_CACHE_DIR',
                                          os.path.expanduser('~/.ic/cache'))),
            ttl_seconds=int(os.environ.get('REF_CACHE_TTL', '300')),
        )


class RefCache:
    """Disk-backed cache for Git ref SHAs with TTL expiry."""

    def __init__(self, config: CacheConfig):
        self._config = config
        self._file = config.cache_dir / 'ref-shas.json'
        self._data: Dict[str, dict] = {}
        self._hits = 0
        self._misses = 0
        self._expired = 0
        self._load()

    def _load(self):
        if not self._file.exists():
            return
        try:
            self._data = json.loads(self._file.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Cache file corrupted, starting fresh: %s", exc)
            self._data = {}

    def _save(self):
        self._config.cache_dir.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(self._data))

    def get(self, key: str) -> Optional[str]:
        entry = self._data.get(key)
        if entry is None:
            self._misses += 1
            return None
        if time.time() - entry.get('cached_at', 0) > self._config.ttl_seconds:
            self._expired += 1
            self._misses += 1
            return None
        self._hits += 1
        return entry.get('sha')

    def put(self, key: str, value: str):
        self._data[key] = {'sha': value, 'cached_at': time.time()}
        self._save()

    def get_batch(self, keys: List[str]) -> Dict[str, Optional[str]]:
        return {k: self.get(k) for k in keys}

    def put_batch(self, entries: Dict[str, str]):
        now = time.time()
        for key, sha in entries.items():
            self._data[key] = {'sha': sha, 'cached_at': now}
        self._compact()
        self._save()

    def _compact(self):
        """Remove expired entries on write to keep the file small."""
        now = time.time()
        self._data = {
            k: v for k, v in self._data.items()
            if now - v.get('cached_at', 0) <= self._config.ttl_seconds * 2
        }

    def stats(self) -> Dict[str, int]:
        return {
            'hits': self._hits,
            'misses': self._misses,
            'expired': self._expired,
            'total_entries': len(self._data),
        }

    def clear(self):
        self._data = {}
        if self._file.exists():
            self._file.unlink()
