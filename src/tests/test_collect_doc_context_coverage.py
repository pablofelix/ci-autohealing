"""Comprehensive tests for collect_doc_context module.

Covers:
- _TextExtractor: HTML parsing, tag skipping, whitespace collapse
- _cache_path: deterministic hashing
- _read_cache / _write_cache: filesystem caching with TTL
- fetch_doc_page: cache hits, HTTP fetch, error handling, truncation
- run: orchestration with patterns, filters, dry-run, success/failure
- main: CLI entry point argument handling
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import hashlib
import time
from unittest.mock import MagicMock, patch

import pytest

from collect_doc_context import (
    CACHE_DIR,
    _cache_path,
    _read_cache,
    _TextExtractor,
    _write_cache,
    fetch_doc_page,
    main,
    run,
)

# ===================================================================
# _TextExtractor
# ===================================================================

class TestTextExtractor:
    """Tests for HTML-to-text extraction with tag filtering."""

    def test_plain_text_passthrough(self):
        """Plain text without any HTML tags passes through unchanged."""
        ext = _TextExtractor()
        ext.feed("Hello world")
        assert ext.get_text() == "Hello world"

    def test_strips_html_tags(self):
        """Standard HTML tags are stripped, leaving only text content."""
        ext = _TextExtractor()
        ext.feed("<p>Hello</p> <b>world</b>")
        assert "Hello" in ext.get_text()
        assert "world" in ext.get_text()
        assert "<p>" not in ext.get_text()
        assert "<b>" not in ext.get_text()

    def test_skips_script_content(self):
        """Content inside <script> tags is excluded from output."""
        ext = _TextExtractor()
        ext.feed("<p>visible</p><script>var x = 1;</script><p>also visible</p>")
        text = ext.get_text()
        assert "visible" in text
        assert "also visible" in text
        assert "var x" not in text

    def test_skips_style_content(self):
        """Content inside <style> tags is excluded from output."""
        ext = _TextExtractor()
        ext.feed("<p>visible</p><style>.foo { color: red; }</style><p>end</p>")
        text = ext.get_text()
        assert "visible" in text
        assert "end" in text
        assert "color" not in text

    def test_skips_nav_header_footer(self):
        """Content inside nav, header, and footer tags is excluded."""
        ext = _TextExtractor()
        ext.feed(
            "<header>Site Header</header>"
            "<nav>Menu Item</nav>"
            "<main><p>Main content</p></main>"
            "<footer>Copyright 2024</footer>"
        )
        text = ext.get_text()
        assert "Main content" in text
        assert "Site Header" not in text
        assert "Menu Item" not in text
        assert "Copyright" not in text

    def test_nested_tags(self):
        """Nested HTML tags are handled correctly."""
        ext = _TextExtractor()
        ext.feed("<div><p>Hello <span>nested <em>world</em></span></p></div>")
        text = ext.get_text()
        assert "Hello" in text
        assert "nested" in text
        assert "world" in text

    def test_empty_html(self):
        """Empty HTML produces empty text."""
        ext = _TextExtractor()
        ext.feed("")
        assert ext.get_text() == ""

    def test_whitespace_collapse(self):
        """Multiple spaces and tabs collapse to a single space; excessive newlines collapse."""
        ext = _TextExtractor()
        ext.feed("<p>Hello    \t  world</p>\n\n\n\n<p>Next</p>")
        text = ext.get_text()
        # Multiple spaces/tabs should collapse
        assert "Hello world" in text
        # Triple+ newlines should collapse to double
        assert "\n\n\n" not in text


# ===================================================================
# _cache_path
# ===================================================================

class TestCachePath:
    """Tests for cache path generation from URLs."""

    def test_deterministic_for_same_url(self):
        """Same URL always produces the same cache path."""
        url = "https://example.com/docs/page"
        assert _cache_path(url) == _cache_path(url)

    def test_different_urls_give_different_paths(self):
        """Different URLs produce different cache paths."""
        path_a = _cache_path("https://example.com/page-a")
        path_b = _cache_path("https://example.com/page-b")
        assert path_a != path_b

    def test_path_uses_md5_prefix(self):
        """Cache path filename is first 12 chars of MD5 hash + .txt."""
        url = "https://example.com/docs/page"
        expected_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        path = _cache_path(url)
        assert path == CACHE_DIR / f"{expected_hash}.txt"


# ===================================================================
# _read_cache
# ===================================================================

class TestReadCache:
    """Tests for reading cached doc content with TTL enforcement."""

    def test_returns_none_when_file_missing(self, tmp_path):
        """Returns None when cache file does not exist."""
        with patch('collect_doc_context.CACHE_DIR', tmp_path):
            with patch('collect_doc_context._cache_path',
                       return_value=tmp_path / 'nonexistent.txt'):
                result = _read_cache("https://example.com/missing")
                assert result is None

    def test_returns_content_when_fresh(self, tmp_path):
        """Returns file content when cache file exists and is within TTL."""
        cache_file = tmp_path / "fresh.txt"
        cache_file.write_text("Cached documentation content", encoding='utf-8')
        # File was just created, so mtime is now — well within 7-day TTL

        with patch('collect_doc_context._cache_path', return_value=cache_file):
            result = _read_cache("https://example.com/fresh")
            assert result == "Cached documentation content"

    def test_returns_none_when_stale(self, tmp_path):
        """Returns None when cache file is older than CACHE_TTL_DAYS."""
        cache_file = tmp_path / "stale.txt"
        cache_file.write_text("Old content", encoding='utf-8')
        # Set mtime to 8 days ago (beyond the 7-day TTL)
        stale_time = time.time() - (8 * 86400)
        os.utime(cache_file, (stale_time, stale_time))

        with patch('collect_doc_context._cache_path', return_value=cache_file):
            result = _read_cache("https://example.com/stale")
            assert result is None


# ===================================================================
# _write_cache
# ===================================================================

class TestWriteCache:
    """Tests for writing doc content to the filesystem cache."""

    def test_creates_directory_and_writes_file(self, tmp_path):
        """Creates the cache directory if needed and writes content."""
        cache_dir = tmp_path / "subdir" / "cache"
        cache_file = cache_dir / "abc123456789.txt"

        with patch('collect_doc_context.CACHE_DIR', cache_dir):
            with patch('collect_doc_context._cache_path', return_value=cache_file):
                _write_cache("https://example.com/page", "Doc content here")

        assert cache_dir.exists()
        assert cache_file.exists()
        assert cache_file.read_text(encoding='utf-8') == "Doc content here"


# ===================================================================
# fetch_doc_page
# ===================================================================

class TestFetchDocPage:
    """Tests for the main doc-fetching function with caching and HTTP."""

    def test_cache_hit_returns_cached_content(self):
        """When cache has fresh content, returns it without making HTTP request."""
        with patch('collect_doc_context._read_cache',
                   return_value="Cached doc text for this page"):
            result = fetch_doc_page("https://example.com/docs")
            assert result == "Cached doc text for this page"

    def test_cache_hit_truncates_to_max_chars(self):
        """Cached content is truncated to max_chars."""
        long_text = "A" * 5000
        with patch('collect_doc_context._read_cache', return_value=long_text):
            result = fetch_doc_page("https://example.com/docs", max_chars=100)
            assert len(result) == 100

    def test_successful_http_fetch_and_cache_write(self):
        """On cache miss, fetches HTML via HTTP, extracts text, writes cache."""
        html_body = b"<html><body><p>Documentation content</p></body></html>"

        mock_response = MagicMock()
        mock_response.read.return_value = html_body
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch('collect_doc_context._read_cache', return_value=None), \
             patch('collect_doc_context.urllib.request.urlopen',
                   return_value=mock_response), \
             patch('collect_doc_context._write_cache') as mock_write:
            result = fetch_doc_page("https://example.com/docs")

            assert result is not None
            assert "Documentation content" in result
            mock_write.assert_called_once()
            # First arg is URL, second is the extracted text
            assert mock_write.call_args[0][0] == "https://example.com/docs"

    def test_http_error_returns_none(self):
        """Returns None when HTTP request raises URLError."""
        import urllib.error

        with patch('collect_doc_context._read_cache', return_value=None), \
             patch('collect_doc_context.urllib.request.urlopen',
                   side_effect=urllib.error.URLError("Connection refused")):
            result = fetch_doc_page("https://example.com/docs")
            assert result is None

    def test_empty_text_after_parse_returns_none(self):
        """Returns None when HTML parsing yields no text content."""
        html_body = b"<html><head><script>var x = 1;</script></head><body></body></html>"

        mock_response = MagicMock()
        mock_response.read.return_value = html_body
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch('collect_doc_context._read_cache', return_value=None), \
             patch('collect_doc_context.urllib.request.urlopen',
                   return_value=mock_response), \
             patch('collect_doc_context._write_cache') as mock_write:
            result = fetch_doc_page("https://example.com/empty")

            assert result is None
            mock_write.assert_not_called()

    def test_max_chars_truncation_on_http_fetch(self):
        """HTTP-fetched content is truncated to max_chars."""
        long_body = "<p>" + ("X" * 5000) + "</p>"
        html_bytes = long_body.encode('utf-8')

        mock_response = MagicMock()
        mock_response.read.return_value = html_bytes
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch('collect_doc_context._read_cache', return_value=None), \
             patch('collect_doc_context.urllib.request.urlopen',
                   return_value=mock_response), \
             patch('collect_doc_context._write_cache'):
            result = fetch_doc_page("https://example.com/long", max_chars=200)

            assert result is not None
            assert len(result) == 200


# ===================================================================
# run
# ===================================================================

class TestRun:
    """Tests for the orchestration function that processes patterns."""

    def _mock_deps(self):
        """Set up common mocks for config, DB, and pattern repo."""
        mock_config = MagicMock()
        mock_db = MagicMock()
        mock_repo = MagicMock()
        return mock_config, mock_db, mock_repo

    @patch('collect_doc_context.fetch_doc_page')
    @patch('collect_doc_context.ErrorPatternRepository')
    @patch('collect_doc_context.DatabaseConnection')
    @patch('collect_doc_context.CollectorConfig')
    def test_no_patterns_returns_zeros(self, mock_config_cls, mock_db_cls,
                                       mock_repo_cls, mock_fetch):
        """Returns all-zero stats when no patterns need doc fetch."""
        mock_repo_cls.return_value.get_needing_doc_fetch.return_value = []

        result = run()

        assert result == {'fetched': 0, 'failed': 0, 'skipped': 0}
        mock_fetch.assert_not_called()

    @patch('collect_doc_context.fetch_doc_page')
    @patch('collect_doc_context.ErrorPatternRepository')
    @patch('collect_doc_context.DatabaseConnection')
    @patch('collect_doc_context.CollectorConfig')
    def test_category_filter_narrows_patterns(self, mock_config_cls, mock_db_cls,
                                               mock_repo_cls, mock_fetch):
        """Only patterns matching category_filter are processed."""
        mock_repo = mock_repo_cls.return_value
        mock_repo.get_needing_doc_fetch.return_value = [
            {'id': 1, 'doc_url': 'https://a.com', 'pattern_name': 'p1',
             'failure_category': 'policy_hermetic_build', 'failure_type': 'build'},
            {'id': 2, 'doc_url': 'https://b.com', 'pattern_name': 'p2',
             'failure_category': 'dependency_issue', 'failure_type': 'build'},
        ]
        mock_fetch.return_value = "Doc text"

        result = run(category_filter='policy_hermetic_build')

        # Only the matching pattern should be fetched
        assert result['fetched'] == 1
        assert result['failed'] == 0
        mock_fetch.assert_called_once_with('https://a.com')

    @patch('collect_doc_context.fetch_doc_page')
    @patch('collect_doc_context.ErrorPatternRepository')
    @patch('collect_doc_context.DatabaseConnection')
    @patch('collect_doc_context.CollectorConfig')
    def test_dry_run_skips_all(self, mock_config_cls, mock_db_cls,
                                mock_repo_cls, mock_fetch):
        """In dry-run mode, patterns are counted as skipped without fetching."""
        mock_repo = mock_repo_cls.return_value
        mock_repo.get_needing_doc_fetch.return_value = [
            {'id': 1, 'doc_url': 'https://a.com', 'pattern_name': 'p1',
             'failure_category': 'cat1', 'failure_type': 'build'},
            {'id': 2, 'doc_url': 'https://b.com', 'pattern_name': 'p2',
             'failure_category': 'cat2', 'failure_type': 'build'},
        ]

        result = run(dry_run=True)

        assert result == {'fetched': 0, 'failed': 0, 'skipped': 2}
        mock_fetch.assert_not_called()
        mock_repo.update_doc_context.assert_not_called()

    @patch('collect_doc_context.fetch_doc_page')
    @patch('collect_doc_context.ErrorPatternRepository')
    @patch('collect_doc_context.DatabaseConnection')
    @patch('collect_doc_context.CollectorConfig')
    def test_successful_fetch_updates_db(self, mock_config_cls, mock_db_cls,
                                          mock_repo_cls, mock_fetch):
        """Successful fetch calls update_doc_context on the pattern repo."""
        mock_repo = mock_repo_cls.return_value
        mock_repo.get_needing_doc_fetch.return_value = [
            {'id': 42, 'doc_url': 'https://docs.example.com/page',
             'pattern_name': 'hermetic_build_fail',
             'failure_category': 'policy', 'failure_type': 'conforma'},
        ]
        mock_fetch.return_value = "Extracted documentation text"

        result = run()

        assert result == {'fetched': 1, 'failed': 0, 'skipped': 0}
        mock_repo.update_doc_context.assert_called_once_with(
            42, "Extracted documentation text"
        )

    @patch('collect_doc_context.fetch_doc_page')
    @patch('collect_doc_context.ErrorPatternRepository')
    @patch('collect_doc_context.DatabaseConnection')
    @patch('collect_doc_context.CollectorConfig')
    def test_failed_fetch_increments_failed(self, mock_config_cls, mock_db_cls,
                                             mock_repo_cls, mock_fetch):
        """When fetch_doc_page returns None, the failed counter increments."""
        mock_repo = mock_repo_cls.return_value
        mock_repo.get_needing_doc_fetch.return_value = [
            {'id': 1, 'doc_url': 'https://unreachable.example.com',
             'pattern_name': 'broken', 'failure_category': 'cat1',
             'failure_type': 'build'},
        ]
        mock_fetch.return_value = None

        result = run()

        assert result == {'fetched': 0, 'failed': 1, 'skipped': 0}
        mock_repo.update_doc_context.assert_not_called()

    @patch('collect_doc_context.fetch_doc_page')
    @patch('collect_doc_context.ErrorPatternRepository')
    @patch('collect_doc_context.DatabaseConnection')
    @patch('collect_doc_context.CollectorConfig')
    def test_mixed_results(self, mock_config_cls, mock_db_cls,
                            mock_repo_cls, mock_fetch):
        """Multiple patterns with mixed success/failure produce correct stats."""
        mock_repo = mock_repo_cls.return_value
        mock_repo.get_needing_doc_fetch.return_value = [
            {'id': 1, 'doc_url': 'https://a.com', 'pattern_name': 'p1',
             'failure_category': 'cat1', 'failure_type': 'build'},
            {'id': 2, 'doc_url': 'https://b.com', 'pattern_name': 'p2',
             'failure_category': 'cat2', 'failure_type': 'build'},
            {'id': 3, 'doc_url': 'https://c.com', 'pattern_name': 'p3',
             'failure_category': 'cat3', 'failure_type': 'conforma'},
        ]
        # First succeeds, second fails, third succeeds
        mock_fetch.side_effect = ["Text A", None, "Text C"]

        result = run()

        assert result == {'fetched': 2, 'failed': 1, 'skipped': 0}
        assert mock_repo.update_doc_context.call_count == 2


# ===================================================================
# main
# ===================================================================

class TestMain:
    """Tests for the CLI entry point."""

    @patch('collect_doc_context.run')
    @patch('collect_doc_context.argparse.ArgumentParser.parse_args')
    def test_main_calls_run_with_args(self, mock_parse, mock_run):
        """main() parses CLI args and passes them to run()."""
        mock_parse.return_value = MagicMock(
            category='policy_hermetic_build',
            dry_run=True,
            stale_days=14,
        )
        mock_run.return_value = {'fetched': 0, 'failed': 0, 'skipped': 3}

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        mock_run.assert_called_once_with(
            category_filter='policy_hermetic_build',
            dry_run=True,
            stale_days=14,
        )

    @patch('collect_doc_context.run')
    @patch('collect_doc_context.argparse.ArgumentParser.parse_args')
    def test_main_exits_1_on_exception(self, mock_parse, mock_run):
        """main() exits with code 1 when run() raises an exception."""
        mock_parse.return_value = MagicMock(
            category=None,
            dry_run=False,
            stale_days=7,
        )
        mock_run.side_effect = RuntimeError("DB connection failed")

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
