"""HTTP client for the IC REST API server.

Used by cli/data.py in cluster mode to fetch data from the remote API
instead of querying the local database.
"""

import sys
import warnings

import requests

warnings.filterwarnings('ignore', message='Unverified HTTPS request')


class APIError(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API {status_code}: {detail}")


class APIClient:
    def __init__(self, base_url, api_key=None, verify_tls=True):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.verify = verify_tls
        self.session.headers['Accept'] = 'application/json'
        if api_key:
            self.session.headers['Authorization'] = f'Bearer {api_key}'

    def get(self, path, params=None):
        return self._request('GET', path, params=params)

    def post(self, path, data=None):
        return self._request('POST', path, json=data)

    def put(self, path, data=None):
        return self._request('PUT', path, json=data)

    def delete(self, path):
        return self._request('DELETE', path)

    def _request(self, method, path, **kwargs):
        url = f"{self.base_url}{path}"
        try:
            r = self.session.request(method, url, timeout=30, **kwargs)
        except requests.ConnectionError:
            print(f"Error: cannot connect to {self.base_url}", file=sys.stderr)
            print("Check VPN and that the API server is running.", file=sys.stderr)
            raise SystemExit(1)

        if r.status_code == 401:
            print("Error: authentication required. Set API key:", file=sys.stderr)
            print("  ic config use-cluster URL --api-key YOUR_KEY", file=sys.stderr)
            raise SystemExit(1)

        if r.status_code == 403:
            raise APIError(403, "Invalid API key")

        if r.status_code >= 400:
            detail = r.text[:200]
            suggestion = None
            try:
                body = r.json()
                detail = body.get('detail', detail)
                suggestion = body.get('suggestion')
            except Exception:
                pass

            if r.status_code == 404:
                print("Error: {}".format(detail), file=sys.stderr)
                if suggestion:
                    print("  {}".format(suggestion), file=sys.stderr)
                return None

            print("Error: {}".format(detail), file=sys.stderr)
            if suggestion:
                print("  {}".format(suggestion), file=sys.stderr)
            raise APIError(r.status_code, detail)

        return r.json()


_client = None


def get_client():
    global _client
    if _client is None:
        from cli.ic_config import get_api_key, get_api_url, get_mode, load
        mode = get_mode()
        if mode == 'local':
            import requests
            try:
                r = requests.get('http://localhost:8000/health', timeout=2)
                if r.status_code == 200:
                    _client = APIClient('http://localhost:8000')
                    return _client
            except Exception:
                pass
        url = get_api_url()
        if not url:
            print("Error: no cluster API URL configured.", file=sys.stderr)
            print("  ic config use-cluster https://your-api-url", file=sys.stderr)
            raise SystemExit(1)
        verify_tls = load().get('cluster', {}).get('verify_tls', True)
        _client = APIClient(url, get_api_key(), verify_tls=verify_tls)
    return _client
