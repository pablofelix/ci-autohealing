"""Pyxis REST API client for checking container image availability.

Queries Red Hat's Pyxis registry metadata service to verify whether
container images exist in the target registry (stage or prod).
Uses Kerberos authentication via curl --negotiate (requires valid kinit).
"""

import json
import os
import subprocess
from urllib.parse import urlencode

from logger import setup_logger

logger = setup_logger(__name__)


class PyxisClient:
    """Read-only Pyxis REST API client.

    Checks image availability by manifest list digest and queries advisories.
    Uses curl with Kerberos negotiate auth to avoid Python SSL/krb5 issues.
    """

    STAGE_URL = os.environ.get('PYXIS_STAGE_URL', '')
    PROD_URL = os.environ.get('PYXIS_PROD_URL', '')

    def __init__(self, target='prod'):
        self._base_url = self.STAGE_URL if target == 'stage' else self.PROD_URL
        self._target = target

    def _get(self, path, params=None):
        url = '{}{}'.format(self._base_url, path)
        if params:
            url = '{}?{}'.format(url, urlencode(params))
        cmd = ['curl', '-s', '--negotiate', '-u', ':', '-H', 'Accept: application/json', url]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.warning("Pyxis curl %s failed: %s", path, result.stderr.strip()[:150])
                return None
            data = json.loads(result.stdout)
            if isinstance(data, dict) and data.get('status') == 404:
                logger.debug("Pyxis %s: Not Found (404)", path)
                return None
            return data
        except subprocess.TimeoutExpired:
            logger.warning("Pyxis %s: timeout", path)
            return None
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning("Pyxis %s: %s", path, str(e)[:150])
            return None

    def check_image(self, registry, repository, digest):
        """Check if an image exists in Pyxis by repository and manifest list digest.

        Snapshot digests are manifest list (multi-arch) digests, so we search
        via the filter API rather than the per-arch lookup endpoint.
        """
        filter_str = 'repositories.manifest_list_digest=={};repositories.repository=={}'.format(
            digest, repository
        )
        data = self._get('/images', params={'filter': filter_str, 'page_size': '1'})
        if not data:
            return None
        total = data.get('total', 0)
        if total > 0:
            return data['data'][0]
        return None

    def check_images_batch(self, images):
        """Check multiple images against Pyxis.

        Args:
            images: list of dicts with keys: name, registry, repository, digest

        Returns:
            list of dicts with keys: name, repository, digest, status (found|missing|error)
        """
        results = []
        for img in images:
            try:
                data = self.check_image(img['registry'], img['repository'], img['digest'])
                status = 'found' if data else 'missing'
            except Exception as e:
                logger.warning("Error checking %s: %s", img.get('name', '?'), e)
                status = 'error'
            results.append({
                'name': img['name'],
                'repository': img['repository'],
                'digest': img['digest'],
                'status': status,
            })
        return results

    def get_advisories(self, filter_str=None, page_size=10):
        """Query Red Hat advisories from Pyxis.

        Args:
            filter_str: RSQL filter (e.g., 'errata_id==RHSA-2024:1234')
            page_size: Number of results per page

        Returns:
            List of advisory dicts, or empty list on error.
        """
        params = {'page_size': str(page_size)}
        if filter_str:
            params['filter'] = filter_str
        data = self._get('/advisories/redhat', params=params)
        if not data:
            return []
        return data.get('data', data) if isinstance(data, dict) else data
