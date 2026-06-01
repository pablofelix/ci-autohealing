"""OCI Registry client for fetching log artifacts and SARIF scan results.

Uses the OCI Distribution API to:
- Fetch export-pipeline-logs artifacts (tar.gz with all-logs.txt)
- Fetch SARIF vulnerability scan results via the referrers API
- Read-only: never pushes or modifies registry content
"""

import io
import json
import os
import tarfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from logger import setup_logger

logger = setup_logger(__name__)

TIMEOUT = 10
SARIF_WORKERS = 8


class RegistryClient:
    """Read-only OCI registry client for Quay/registry API."""

    def __init__(self, token=None):
        self._basic_creds = token or os.environ.get('QUAY_TOKEN', '')
        self._session = requests.Session()
        self._bearer_cache = {}
        self._sarif_cache = {}

    @staticmethod
    def parse_image_ref(image_url):
        """Parse an image URL into (registry, repository, tag_or_digest).

        Examples:
            quay.io/rh/image:tag -> ('quay.io', 'rh/image', 'tag')
            quay.io/rh/image@sha256:abc -> ('quay.io', 'rh/image', 'sha256:abc')
        """
        url = image_url
        for prefix in ('https://', 'http://'):
            if url.startswith(prefix):
                url = url[len(prefix):]

        if '@' in url:
            repo_part, digest = url.rsplit('@', 1)
            parts = repo_part.split('/', 1)
            return (parts[0], parts[1] if len(parts) > 1 else '', digest)

        if ':' in url.rsplit('/', 1)[-1]:
            repo_part, tag = url.rsplit(':', 1)
            parts = repo_part.split('/', 1)
            return (parts[0], parts[1] if len(parts) > 1 else '', tag)

        parts = url.split('/', 1)
        return (parts[0], parts[1] if len(parts) > 1 else '', 'latest')

    def _get_bearer_token(self, registry, repository):
        cache_key = '{}/{}'.format(registry, repository)
        if cache_key in self._bearer_cache:
            return self._bearer_cache[cache_key]

        if not self._basic_creds:
            return ''

        try:
            resp = self._session.get(
                'https://{}/v2/auth'.format(registry),
                params={'service': registry, 'scope': 'repository:{}:pull'.format(repository)},
                headers={'Authorization': 'Basic {}'.format(self._basic_creds)},
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                token = resp.json().get('token', '')
                self._bearer_cache[cache_key] = token
                return token
        except Exception as e:
            logger.debug("Token exchange failed for %s/%s: %s", registry, repository, e)
        return ''

    def _api_get(self, registry, path, accept=None, repository=None, session=None):
        token = self._get_bearer_token(registry, repository or '')

        url = 'https://{}/v2/{}'.format(registry, path)
        headers = {}
        if accept:
            headers['Accept'] = accept
        if token:
            headers['Authorization'] = 'Bearer {}'.format(token)
        s = session or self._session
        return s.get(url, headers=headers, timeout=TIMEOUT)

    def list_tags(self, registry, repository):
        try:
            resp = self._api_get(registry, '{}/tags/list'.format(repository),
                                 repository=repository)
            if resp.status_code != 200:
                return []
            return resp.json().get('tags', [])
        except Exception as e:
            logger.debug("Failed to list tags for %s/%s: %s", registry, repository, e)
            return []

    def fetch_log_artifact(self, registry, repository, pr_name):
        """Fetch export-pipeline-logs artifact for a PipelineRun.

        Looks for tags matching <pr-name>-*-logs, fetches the tar.gz layer,
        and extracts all-logs.txt content.
        """
        tags = self.list_tags(registry, repository)
        log_tags = [t for t in tags if t.startswith(pr_name) and t.endswith('-logs')]
        if not log_tags:
            return ''

        log_tags.sort(reverse=True)
        tag = log_tags[0]
        logger.info("Found log artifact tag: %s", tag)

        try:
            manifest_resp = self._api_get(
                registry,
                '{}/manifests/{}'.format(repository, tag),
                accept='application/vnd.oci.image.manifest.v1+json',
                repository=repository,
            )
            if manifest_resp.status_code != 200:
                logger.debug("Failed to fetch manifest for %s:%s: %d",
                             repository, tag, manifest_resp.status_code)
                return ''

            manifest = manifest_resp.json()
            layers = manifest.get('layers', [])
            if not layers:
                return ''

            layer = layers[0]
            digest = layer.get('digest', '')
            blob_resp = self._api_get(registry, '{}/blobs/{}'.format(repository, digest),
                                      repository=repository)
            if blob_resp.status_code != 200:
                return ''

            return self._extract_logs_from_tarball(blob_resp.content)
        except Exception as e:
            logger.warning("Failed to fetch log artifact %s:%s: %s", repository, tag, e)
            return ''

    @staticmethod
    def _extract_logs_from_tarball(data):
        try:
            buf = io.BytesIO(data)
            with tarfile.open(fileobj=buf, mode='r:gz') as tar:
                for member in tar.getmembers():
                    if member.name.endswith('all-logs.txt') or member.name.endswith('.txt'):
                        f = tar.extractfile(member)
                        if f:
                            return f.read().decode('utf-8', errors='replace')
        except (tarfile.TarError, OSError) as e:
            logger.debug("Failed to extract tarball: %s", e)
        return ''

    def fetch_sarif_results(self, registry, repository, digest):
        """Fetch SARIF scan results via the OCI referrers API.

        Returns a list of vulnerability dicts:
        [{'ruleId': 'CVE-...', 'level': 'error', 'message': '...', 'package': '...'}]
        """
        cache_key = '{}/{}@{}'.format(registry, repository, digest)
        if cache_key in self._sarif_cache:
            return self._sarif_cache[cache_key]

        try:
            resp = self._api_get(
                registry,
                '{}/referrers/{}'.format(repository, digest),
                accept='application/vnd.oci.image.index.v1+json',
                repository=repository,
            )
            if resp.status_code != 200:
                self._sarif_cache[cache_key] = []
                return []

            index = resp.json()
            manifests = index.get('manifests', [])

            sarif_refs = [m for m in manifests
                          if 'sarif' in m.get('artifactType', '').lower()]
            if not sarif_refs:
                self._sarif_cache[cache_key] = []
                return []

            all_results = []
            for ref in sarif_refs[:3]:
                results = self._fetch_single_sarif(registry, repository, ref['digest'])
                all_results.extend(results)

            self._sarif_cache[cache_key] = all_results
            return all_results
        except Exception as e:
            logger.debug("Failed to fetch SARIF referrers for %s/%s@%s: %s",
                         registry, repository, digest[:16], e)
            self._sarif_cache[cache_key] = []
            return []

    def fetch_sarif_batch(self, components, timeout=60):
        """Fetch SARIF results for multiple components in parallel.

        Args:
            components: list of dicts with 'name' and 'containerImage' keys
            timeout: max seconds for the entire batch

        Returns:
            dict mapping component name to list of SARIF result dicts
        """
        targets = []
        for comp in components:
            name = comp.get('name', '')
            image = comp.get('containerImage', '')
            if not image:
                continue
            registry, repository, tag_or_digest = self.parse_image_ref(image)
            if tag_or_digest.startswith('sha256:'):
                targets.append((name, registry, repository, tag_or_digest))

        import threading
        _local = threading.local()

        def _fetch_one(reg, repo, dig):
            if not hasattr(_local, 'session'):
                _local.session = requests.Session()
            return self._fetch_sarif_with_session(reg, repo, dig, _local.session)

        results = {}
        with ThreadPoolExecutor(max_workers=SARIF_WORKERS) as pool:
            futures = {
                pool.submit(_fetch_one, reg, repo, dig): name
                for name, reg, repo, dig in targets
            }
            for future in as_completed(futures, timeout=timeout):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception:
                    results[name] = []
        return results

    def _fetch_sarif_with_session(self, registry, repository, digest, session):
        """Thread-safe SARIF fetch using a dedicated session."""
        cache_key = '{}/{}@{}'.format(registry, repository, digest)
        if cache_key in self._sarif_cache:
            return self._sarif_cache[cache_key]

        try:
            resp = self._api_get(
                registry,
                '{}/referrers/{}'.format(repository, digest),
                accept='application/vnd.oci.image.index.v1+json',
                repository=repository,
                session=session,
            )
            if resp.status_code != 200:
                self._sarif_cache[cache_key] = []
                return []

            index = resp.json()
            manifests = index.get('manifests', [])
            sarif_refs = [m for m in manifests
                          if 'sarif' in m.get('artifactType', '').lower()]
            if not sarif_refs:
                self._sarif_cache[cache_key] = []
                return []

            all_results = []
            for ref in sarif_refs[:3]:
                results = self._fetch_single_sarif(
                    registry, repository, ref['digest'], session=session)
                all_results.extend(results)

            self._sarif_cache[cache_key] = all_results
            return all_results
        except Exception as e:
            logger.debug("Failed to fetch SARIF referrers for %s/%s@%s: %s",
                         registry, repository, digest[:16], e)
            self._sarif_cache[cache_key] = []
            return []

    def _fetch_single_sarif(self, registry, repository, sarif_digest, session=None):
        try:
            manifest_resp = self._api_get(
                registry,
                '{}/manifests/{}'.format(repository, sarif_digest),
                accept='application/vnd.oci.image.manifest.v1+json',
                repository=repository,
                session=session,
            )
            if manifest_resp.status_code != 200:
                return []

            manifest = manifest_resp.json()
            layers = manifest.get('layers', [])
            if not layers:
                return []

            blob_resp = self._api_get(
                registry,
                '{}/blobs/{}'.format(repository, layers[0]['digest']),
                repository=repository,
                session=session,
            )
            if blob_resp.status_code != 200:
                return []

            return self._parse_sarif(blob_resp.content)
        except Exception as e:
            logger.debug("Failed to fetch SARIF blob: %s", e)
            return []

    @staticmethod
    def _parse_sarif(data):
        try:
            sarif = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return []

        results = []
        for run in sarif.get('runs', []):
            rules = {}
            for rule in run.get('tool', {}).get('driver', {}).get('rules', []):
                rules[rule.get('id', '')] = rule

            for result in run.get('results', []):
                rule_id = result.get('ruleId', '')
                level = result.get('level', 'warning')
                msg = result.get('message', {}).get('text', '')[:200]

                rule_meta = rules.get(rule_id, {})
                package = ''
                fix_version = ''
                for prop_key in ('package', 'affected_package'):
                    if prop_key in rule_meta.get('properties', {}):
                        package = rule_meta['properties'][prop_key]
                        break
                for prop_key in ('fixed_version', 'fixedVersion'):
                    if prop_key in rule_meta.get('properties', {}):
                        fix_version = rule_meta['properties'][prop_key]
                        break

                results.append({
                    'ruleId': rule_id,
                    'level': level,
                    'message': msg,
                    'package': package,
                    'fix_version': fix_version,
                })
        return results

    @staticmethod
    def format_sarif_summary(results, max_chars=2000):
        if not results:
            return ''

        severity_map = {'error': 'critical', 'warning': 'high', 'note': 'medium'}
        counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
        for r in results:
            sev = severity_map.get(r.get('level', ''), 'low')
            counts[sev] += 1

        lines = [
            '=== Structured Scan Results (SARIF) ===',
            'Critical: {}, High: {}, Medium: {}, Low: {}'.format(
                counts['critical'], counts['high'], counts['medium'], counts['low']),
        ]

        sorted_results = sorted(results, key=lambda r: (
            {'error': 0, 'warning': 1, 'note': 2}.get(r.get('level', ''), 3),
            r.get('ruleId', '')))

        for r in sorted_results[:15]:
            sev = severity_map.get(r.get('level', ''), 'LOW').upper()
            line = '- {} ({}): {}'.format(r['ruleId'], sev, r['message'])
            if r.get('package'):
                line += ' [{}]'.format(r['package'])
            if r.get('fix_version'):
                line += ' fix: {}'.format(r['fix_version'])
            lines.append(line)

        if len(results) > 15:
            lines.append('... and {} more vulnerabilities'.format(len(results) - 15))

        text = '\n'.join(lines)
        if len(text) > max_chars:
            text = text[:max_chars] + '\n... (truncated)'
        return text
