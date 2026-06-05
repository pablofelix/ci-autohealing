"""Tests for proactive.ref_cache — TTL disk cache for Git ref SHAs."""

import pytest

from proactive.ref_cache import CacheConfig, RefCache


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / 'cache'


@pytest.fixture
def cache(cache_dir):
    return RefCache(CacheConfig(cache_dir=cache_dir, ttl_seconds=60))


def test_put_and_get_returns_cached_value(cache):
    cache.put('org/repo/main', 'abc123')
    assert cache.get('org/repo/main') == 'abc123'


def test_get_returns_none_for_missing_key(cache):
    assert cache.get('org/repo/main') is None


def test_get_returns_none_for_expired_entry(cache_dir):
    cache = RefCache(CacheConfig(cache_dir=cache_dir, ttl_seconds=0))
    cache.put('org/repo/main', 'abc123')
    assert cache.get('org/repo/main') is None


def test_get_batch_returns_mix_of_hits_and_misses(cache):
    cache.put('org/a/main', 'sha1')
    cache.put('org/b/main', 'sha2')
    result = cache.get_batch(['org/a/main', 'org/b/main', 'org/c/main'])
    assert result == {
        'org/a/main': 'sha1',
        'org/b/main': 'sha2',
        'org/c/main': None,
    }


def test_put_batch_writes_multiple_entries(cache):
    cache.put_batch({'org/a/main': 'sha1', 'org/b/dev': 'sha2'})
    assert cache.get('org/a/main') == 'sha1'
    assert cache.get('org/b/dev') == 'sha2'


def test_stats_tracks_hits_and_misses(cache):
    cache.put('org/repo/main', 'abc123')
    cache.get('org/repo/main')
    cache.get('org/missing/main')
    s = cache.stats()
    assert s['hits'] == 1
    assert s['misses'] == 1


def test_clear_removes_all_entries(cache):
    cache.put('org/repo/main', 'abc123')
    cache.clear()
    assert cache.get('org/repo/main') is None


def test_cache_file_created_on_first_put(cache, cache_dir):
    assert not (cache_dir / 'ref-shas.json').exists()
    cache.put('org/repo/main', 'abc123')
    assert (cache_dir / 'ref-shas.json').exists()


def test_corrupted_cache_file_handled_gracefully(cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / 'ref-shas.json').write_text('not valid json{{{')
    cache = RefCache(CacheConfig(cache_dir=cache_dir, ttl_seconds=60))
    assert cache.get('org/repo/main') is None
    cache.put('org/repo/main', 'abc123')
    assert cache.get('org/repo/main') == 'abc123'


def test_cache_persists_across_instances(cache_dir):
    cache1 = RefCache(CacheConfig(cache_dir=cache_dir, ttl_seconds=60))
    cache1.put('org/repo/main', 'abc123')

    cache2 = RefCache(CacheConfig(cache_dir=cache_dir, ttl_seconds=60))
    assert cache2.get('org/repo/main') == 'abc123'


def test_expired_entries_counted_in_stats(cache_dir):
    cache = RefCache(CacheConfig(cache_dir=cache_dir, ttl_seconds=0))
    cache.put('org/repo/main', 'abc123')
    cache.get('org/repo/main')
    s = cache.stats()
    assert s['expired'] == 1
    assert s['misses'] == 1
