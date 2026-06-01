#!/usr/bin/env python3
"""Fetch and cache documentation pages for known error patterns.

Reads error_patterns rows that have a doc_url but missing or stale doc_context,
fetches the page, extracts plain text, and stores a ~3000-char excerpt back in
the pattern row. This runs as a lightweight cron step after the AI analyzers so
that the next analysis cycle has doc context available in its prompt.

The fetched content is also cached to /tmp/konflux-docs-cache/ so re-runs within
the same day avoid redundant HTTP calls. If the site is unreachable (no VPN, DNS
failure, timeout), the step logs a warning and continues — never blocking analysis.

Usage:
    python3 collect_doc_context.py                # fetch all stale patterns
    python3 collect_doc_context.py --category policy_hermetic_build
    python3 collect_doc_context.py --dry-run      # show what would be fetched
"""

import argparse
import hashlib
import html.parser
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import CollectorConfig
from logger import setup_logger
from repositories.connection import DatabaseConnection
from repositories.error_pattern_repository import ErrorPatternRepository

logger = setup_logger(__name__)

CACHE_DIR = Path('/tmp/konflux-docs-cache')
CACHE_TTL_DAYS = 7
FETCH_TIMEOUT = 15  # seconds


class _TextExtractor(html.parser.HTMLParser):
    """Strips HTML tags and returns plain text, skipping script/style blocks."""

    SKIP_TAGS = {'script', 'style', 'nav', 'header', 'footer'}

    def __init__(self):
        super().__init__()
        self._skip = 0
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            self._parts.append(data)

    def get_text(self):
        raw = ' '.join(self._parts)
        # Collapse whitespace
        raw = re.sub(r'[ \t]+', ' ', raw)
        raw = re.sub(r'\n{3,}', '\n\n', raw)
        return raw.strip()


def _cache_path(url):
    key = hashlib.md5(url.encode()).hexdigest()[:12]
    return CACHE_DIR / f'{key}.txt'


def _read_cache(url):
    path = _cache_path(url)
    if not path.exists():
        return None
    age_days = (time.time() - path.stat().st_mtime) / 86400
    if age_days > CACHE_TTL_DAYS:
        return None
    return path.read_text(encoding='utf-8')


def _write_cache(url, text):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(url).write_text(text, encoding='utf-8')


def fetch_doc_page(url, max_chars=3000):
    """Fetch a doc page and return plain-text excerpt up to max_chars.

    Returns None if the fetch fails for any reason (network, timeout, bad HTML).
    Never raises — callers treat None as "doc unavailable".
    """
    cached = _read_cache(url)
    if cached:
        logger.debug("Doc cache hit: %s", url)
        return cached[:max_chars]

    logger.info("Fetching doc page: %s", url)
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'ci-autohealing-doc-collector/1.0'},
        )
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            raw_html = resp.read().decode('utf-8', errors='replace')
    except (urllib.error.URLError, OSError, Exception) as exc:
        logger.warning("Failed to fetch %s: %s", url, exc)
        return None

    extractor = _TextExtractor()
    try:
        extractor.feed(raw_html)
        text = extractor.get_text()
    except Exception as exc:
        logger.warning("Failed to parse HTML from %s: %s", url, exc)
        return None

    if not text:
        return None

    _write_cache(url, text)
    return text[:max_chars]


def run(category_filter=None, dry_run=False, stale_days=CACHE_TTL_DAYS):
    """Fetch doc pages for patterns that need them. Returns stats dict."""
    config = CollectorConfig.from_env()
    db = DatabaseConnection(config.db)
    pattern_repo = ErrorPatternRepository(db)

    patterns = pattern_repo.get_needing_doc_fetch(stale_days=stale_days)

    if category_filter:
        patterns = [p for p in patterns if p['failure_category'] == category_filter]

    if not patterns:
        logger.info("No patterns need doc fetch")
        return {'fetched': 0, 'failed': 0, 'skipped': 0}

    logger.info("Patterns needing doc fetch: %d", len(patterns))

    fetched = failed = skipped = 0

    for pattern in patterns:
        url = pattern['doc_url']
        name = pattern['pattern_name']
        logger.info("[%s] %s -> %s", pattern['failure_type'], name, url)

        if dry_run:
            logger.info("  (dry-run, skipping)")
            skipped += 1
            continue

        text = fetch_doc_page(url)
        if text:
            pattern_repo.update_doc_context(pattern['id'], text)
            logger.info("  Stored %d chars of doc context", len(text))
            fetched += 1
        else:
            logger.warning("  Could not fetch doc page — skipping (VPN required?)")
            failed += 1

    return {'fetched': fetched, 'failed': failed, 'skipped': skipped}


def main():
    parser = argparse.ArgumentParser(description='Fetch doc context for error patterns')
    parser.add_argument('--category', help='Only fetch for this failure_category')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be fetched without doing it')
    parser.add_argument('--stale-days', type=int, default=CACHE_TTL_DAYS,
                        help='Re-fetch if doc is older than N days (default: %(default)s)')
    args = parser.parse_args()

    try:
        stats = run(
            category_filter=args.category,
            dry_run=args.dry_run,
            stale_days=args.stale_days,
        )
        logger.info("Done — fetched: %d, failed: %d, skipped: %d",
                    stats['fetched'], stats['failed'], stats['skipped'])
        sys.exit(0)
    except Exception as exc:
        logger.error("Doc context collection failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
