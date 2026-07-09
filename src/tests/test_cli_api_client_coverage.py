"""Comprehensive tests for cli/api_client.py — APIError, APIClient, get_client."""

from unittest.mock import MagicMock, patch

import pytest
import requests

import cli.api_client as api_client_module
from cli.api_client import APIClient, APIError, get_client

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the module-level _client singleton before each test."""
    api_client_module._client = None
    yield
    api_client_module._client = None


@pytest.fixture
def client():
    """Default APIClient with TLS verification disabled (no real network)."""
    return APIClient("https://api.example.com/", api_key="test-key", verify_tls=False)


@pytest.fixture
def mock_response():
    """Factory for creating mock response objects."""
    def _make(status_code=200, json_data=None, text="", json_raises=False):
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = text
        if json_raises:
            resp.json.side_effect = ValueError("No JSON")
        else:
            resp.json.return_value = json_data if json_data is not None else {}
        return resp
    return _make


# ===========================================================================
# 1. APIError
# ===========================================================================

class TestAPIError:
    def test_creation_and_attributes(self):
        err = APIError(500, "Internal Server Error")
        assert err.status_code == 500
        assert err.detail == "Internal Server Error"

    def test_string_representation(self):
        err = APIError(403, "Forbidden")
        assert str(err) == "API 403: Forbidden"

    def test_is_exception(self):
        err = APIError(400, "Bad Request")
        assert isinstance(err, Exception)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(APIError) as exc_info:
            raise APIError(422, "Unprocessable")
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail == "Unprocessable"


# ===========================================================================
# 2. APIClient.__init__
# ===========================================================================

class TestAPIClientInit:
    def test_trailing_slash_stripped(self):
        c = APIClient("https://api.example.com/")
        assert c.base_url == "https://api.example.com"

    def test_no_trailing_slash_unchanged(self):
        c = APIClient("https://api.example.com")
        assert c.base_url == "https://api.example.com"

    def test_multiple_trailing_slashes_stripped(self):
        c = APIClient("https://api.example.com///")
        assert c.base_url == "https://api.example.com"

    def test_verify_tls_true_by_default(self):
        c = APIClient("https://api.example.com")
        assert c.session.verify is True

    def test_verify_tls_false(self):
        c = APIClient("https://api.example.com", verify_tls=False)
        assert c.session.verify is False

    def test_accept_header_set(self):
        c = APIClient("https://api.example.com")
        assert c.session.headers['Accept'] == 'application/json'

    def test_api_key_sets_authorization_header(self):
        c = APIClient("https://api.example.com", api_key="my-secret")
        assert c.session.headers['Authorization'] == "Bearer my-secret"

    def test_no_api_key_omits_authorization_header(self):
        c = APIClient("https://api.example.com")
        assert 'Authorization' not in c.session.headers


# ===========================================================================
# 3. HTTP method delegation
# ===========================================================================

class TestHTTPMethods:
    def test_get_delegates_to_request(self, client):
        with patch.object(client, '_request', return_value={"ok": True}) as mock_req:
            result = client.get("/items", params={"page": 1})
            mock_req.assert_called_once_with('GET', '/items', params={"page": 1})
            assert result == {"ok": True}

    def test_post_delegates_to_request(self, client):
        with patch.object(client, '_request', return_value={"id": 1}) as mock_req:
            result = client.post("/items", data={"name": "x"})
            mock_req.assert_called_once_with('POST', '/items', json={"name": "x"})
            assert result == {"id": 1}

    def test_put_delegates_to_request(self, client):
        with patch.object(client, '_request', return_value={"updated": True}) as mock_req:
            result = client.put("/items/1", data={"name": "y"})
            mock_req.assert_called_once_with('PUT', '/items/1', json={"name": "y"})
            assert result == {"updated": True}

    def test_delete_delegates_to_request(self, client):
        with patch.object(client, '_request', return_value=None) as mock_req:
            result = client.delete("/items/1")
            mock_req.assert_called_once_with('DELETE', '/items/1')
            assert result is None

    def test_get_with_no_params(self, client):
        with patch.object(client, '_request', return_value=[]) as mock_req:
            client.get("/items")
            mock_req.assert_called_once_with('GET', '/items', params=None)

    def test_post_with_no_data(self, client):
        with patch.object(client, '_request', return_value={}) as mock_req:
            client.post("/trigger")
            mock_req.assert_called_once_with('POST', '/trigger', json=None)


# ===========================================================================
# 4. APIClient._request
# ===========================================================================

class TestRequest:
    """Tests for the internal _request method."""

    def test_success_returns_json(self, client, mock_response):
        resp = mock_response(200, json_data={"key": "value"})
        with patch.object(client.session, 'request', return_value=resp):
            result = client._request('GET', '/data')
        assert result == {"key": "value"}

    def test_url_constructed_correctly(self, client, mock_response):
        resp = mock_response(200, json_data={})
        with patch.object(client.session, 'request', return_value=resp) as mock_req:
            client._request('GET', '/some/path')
            mock_req.assert_called_once_with(
                'GET', 'https://api.example.com/some/path', timeout=30
            )

    def test_kwargs_forwarded(self, client, mock_response):
        resp = mock_response(200, json_data={})
        with patch.object(client.session, 'request', return_value=resp) as mock_req:
            client._request('POST', '/x', json={"a": 1}, params={"b": 2})
            mock_req.assert_called_once_with(
                'POST', 'https://api.example.com/x',
                timeout=30, json={"a": 1}, params={"b": 2}
            )

    # --- ConnectionError ---

    def test_connection_error_exits(self, client, capsys):
        with patch.object(client.session, 'request', side_effect=requests.ConnectionError):
            with pytest.raises(SystemExit) as exc_info:
                client._request('GET', '/fail')
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "cannot connect" in err
        assert "VPN" in err

    # --- 401 ---

    def test_401_exits(self, client, mock_response, capsys):
        resp = mock_response(401)
        with patch.object(client.session, 'request', return_value=resp):
            with pytest.raises(SystemExit) as exc_info:
                client._request('GET', '/secure')
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "authentication required" in err
        assert "ic config use-cluster" in err

    # --- 403 ---

    def test_403_raises_api_error(self, client, mock_response):
        resp = mock_response(403)
        with patch.object(client.session, 'request', return_value=resp):
            with pytest.raises(APIError) as exc_info:
                client._request('GET', '/forbidden')
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Invalid API key"

    # --- 404 ---

    def test_404_returns_none(self, client, mock_response, capsys):
        resp = mock_response(404, json_data={"detail": "Component not found"})
        with patch.object(client.session, 'request', return_value=resp):
            result = client._request('GET', '/missing')
        assert result is None
        err = capsys.readouterr().err
        assert "Component not found" in err

    def test_404_with_suggestion(self, client, mock_response, capsys):
        resp = mock_response(
            404,
            json_data={"detail": "Not found", "suggestion": "Try /api/v2/items"}
        )
        with patch.object(client.session, 'request', return_value=resp):
            result = client._request('GET', '/old')
        assert result is None
        err = capsys.readouterr().err
        assert "Not found" in err
        assert "Try /api/v2/items" in err

    def test_404_non_json_body(self, client, mock_response, capsys):
        resp = mock_response(404, text="<html>Not Found</html>", json_raises=True)
        with patch.object(client.session, 'request', return_value=resp):
            result = client._request('GET', '/old')
        assert result is None
        err = capsys.readouterr().err
        assert "<html>Not Found</html>" in err

    # --- 500 and other errors ---

    def test_500_raises_api_error_with_json_detail(self, client, mock_response, capsys):
        resp = mock_response(500, json_data={"detail": "DB crashed"})
        with patch.object(client.session, 'request', return_value=resp):
            with pytest.raises(APIError) as exc_info:
                client._request('GET', '/boom')
        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "DB crashed"
        err = capsys.readouterr().err
        assert "DB crashed" in err

    def test_500_with_suggestion(self, client, mock_response, capsys):
        resp = mock_response(
            500,
            json_data={"detail": "Overloaded", "suggestion": "Retry in 30s"}
        )
        with patch.object(client.session, 'request', return_value=resp):
            with pytest.raises(APIError):
                client._request('GET', '/overloaded')
        err = capsys.readouterr().err
        assert "Overloaded" in err
        assert "Retry in 30s" in err

    def test_500_non_json_uses_text(self, client, mock_response, capsys):
        long_text = "X" * 300
        resp = mock_response(500, text=long_text, json_raises=True)
        with patch.object(client.session, 'request', return_value=resp):
            with pytest.raises(APIError) as exc_info:
                client._request('POST', '/explode')
        # detail should be truncated to first 200 chars
        assert len(exc_info.value.detail) == 200
        assert exc_info.value.detail == long_text[:200]

    def test_422_raises_api_error(self, client, mock_response):
        resp = mock_response(422, json_data={"detail": "Validation error"})
        with patch.object(client.session, 'request', return_value=resp):
            with pytest.raises(APIError) as exc_info:
                client._request('POST', '/validate')
        assert exc_info.value.status_code == 422

    def test_400_no_suggestion_no_extra_print(self, client, mock_response, capsys):
        resp = mock_response(400, json_data={"detail": "Bad input"})
        with patch.object(client.session, 'request', return_value=resp):
            with pytest.raises(APIError):
                client._request('POST', '/bad')
        err = capsys.readouterr().err
        assert "Bad input" in err
        # No suggestion line printed
        assert "  " not in err or "Bad input" in err


# ===========================================================================
# 5. get_client()
# ===========================================================================

class TestGetClient:
    """Tests for the module-level get_client() singleton."""

    @patch("cli.api_client.get_client.__module__", "cli.api_client")
    def test_no_url_configured_exits(self, capsys):
        with patch.dict("sys.modules", {
            "cli.ic_config": MagicMock(
                get_api_url=MagicMock(return_value=None),
                get_api_key=MagicMock(return_value=None),
                load=MagicMock(return_value={}),
            )
        }):
            with pytest.raises(SystemExit) as exc_info:
                get_client()
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "no cluster API URL configured" in err

    def test_empty_string_url_exits(self, capsys):
        with patch.dict("sys.modules", {
            "cli.ic_config": MagicMock(
                get_api_url=MagicMock(return_value=""),
                get_api_key=MagicMock(return_value=None),
                load=MagicMock(return_value={}),
            )
        }):
            with pytest.raises(SystemExit) as exc_info:
                get_client()
        assert exc_info.value.code == 1

    def test_returns_client_with_correct_config(self):
        mock_config = MagicMock(
            get_api_url=MagicMock(return_value="https://cluster.example.com"),
            get_api_key=MagicMock(return_value="secret-key"),
            load=MagicMock(return_value={"cluster": {"verify_tls": False}}),
        )
        with patch.dict("sys.modules", {"cli.ic_config": mock_config}):
            result = get_client()

        assert isinstance(result, APIClient)
        assert result.base_url == "https://cluster.example.com"
        assert result.session.headers['Authorization'] == "Bearer secret-key"
        assert result.session.verify is False

    def test_verify_tls_defaults_to_true(self):
        mock_config = MagicMock(
            get_api_url=MagicMock(return_value="https://cluster.example.com"),
            get_api_key=MagicMock(return_value=None),
            load=MagicMock(return_value={}),
        )
        with patch.dict("sys.modules", {"cli.ic_config": mock_config}):
            result = get_client()

        assert result.session.verify is True

    def test_verify_tls_defaults_true_with_empty_cluster_section(self):
        mock_config = MagicMock(
            get_api_url=MagicMock(return_value="https://cluster.example.com"),
            get_api_key=MagicMock(return_value=None),
            load=MagicMock(return_value={"cluster": {}}),
        )
        with patch.dict("sys.modules", {"cli.ic_config": mock_config}):
            result = get_client()

        assert result.session.verify is True

    def test_singleton_returns_cached_client(self):
        mock_config = MagicMock(
            get_api_url=MagicMock(return_value="https://cluster.example.com"),
            get_api_key=MagicMock(return_value="key"),
            load=MagicMock(return_value={}),
        )
        with patch.dict("sys.modules", {"cli.ic_config": mock_config}):
            first = get_client()
            second = get_client()

        assert first is second
        # ic_config functions should only be called once (on first call)
        mock_config.get_api_url.assert_called_once()

    def test_no_api_key_omits_auth_header(self):
        mock_config = MagicMock(
            get_api_url=MagicMock(return_value="https://cluster.example.com"),
            get_api_key=MagicMock(return_value=None),
            load=MagicMock(return_value={}),
        )
        with patch.dict("sys.modules", {"cli.ic_config": mock_config}):
            result = get_client()

        assert 'Authorization' not in result.session.headers
