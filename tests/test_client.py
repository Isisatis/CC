"""Unit tests for API client."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import requests

from src.api.client import (
    PolymarketClient,
    APIError,
    AuthenticationError,
    RateLimitError,
)


class TestPolymarketClientInit:
    """Tests for PolymarketClient initialization."""

    def test_default_initialization(self):
        """Test client initializes with default values."""
        with patch.object(PolymarketClient, "_init_clob_client"):
            client = PolymarketClient()

            assert client.clob_url == PolymarketClient.CLOB_BASE_URL
            assert client.gamma_url == PolymarketClient.GAMMA_BASE_URL
            assert client.rate_limit == 0.1
            assert client.timeout == 30
            assert client.max_retries == 3

    def test_custom_urls(self):
        """Test client with custom URLs."""
        with patch.object(PolymarketClient, "_init_clob_client"):
            client = PolymarketClient(
                clob_url="http://custom-clob.com",
                gamma_url="http://custom-gamma.com",
            )

            assert client.clob_url == "http://custom-clob.com"
            assert client.gamma_url == "http://custom-gamma.com"

    def test_custom_rate_limit(self):
        """Test client with custom rate limit."""
        with patch.object(PolymarketClient, "_init_clob_client"):
            client = PolymarketClient(rate_limit=0.5)
            assert client.rate_limit == 0.5

    def test_session_headers(self):
        """Test session has correct headers."""
        with patch.object(PolymarketClient, "_init_clob_client"):
            client = PolymarketClient()

            assert client.session.headers["Accept"] == "application/json"
            assert client.session.headers["Content-Type"] == "application/json"


class TestPolymarketClientRequest:
    """Tests for PolymarketClient._request method."""

    @pytest.fixture
    def client(self):
        """Create a client for testing."""
        with patch.object(PolymarketClient, "_init_clob_client"):
            return PolymarketClient(rate_limit=0)

    def test_successful_request(self, client):
        """Test successful HTTP request."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'{"data": "test"}'
        mock_response.json.return_value = {"data": "test"}
        mock_response.raise_for_status = Mock()

        with patch.object(client.session, "request", return_value=mock_response):
            result = client._request("GET", "http://test.com")

            assert result == {"data": "test"}

    def test_rate_limit_response(self, client):
        """Test handling of 429 rate limit response."""
        # First response: 429, second response: 200
        mock_429 = Mock()
        mock_429.status_code = 429
        mock_429.headers = {"Retry-After": "1"}
        mock_429.content = b'{}'

        mock_200 = Mock()
        mock_200.status_code = 200
        mock_200.content = b'{"data": "success"}'
        mock_200.json.return_value = {"data": "success"}
        mock_200.raise_for_status = Mock()

        with patch.object(
            client.session, "request", side_effect=[mock_429, mock_200]
        ):
            with patch("time.sleep"):
                result = client._request("GET", "http://test.com")

            assert result == {"data": "success"}

    def test_authentication_error(self, client):
        """Test handling of 401/403 responses."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.content = b'{"error": "unauthorized"}'

        with patch.object(client.session, "request", return_value=mock_response):
            with pytest.raises(AuthenticationError) as exc_info:
                client._request("GET", "http://test.com")

            assert exc_info.value.status_code == 401

    def test_http_error(self, client):
        """Test handling of HTTP errors."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.content = b'{"error": "server error"}'
        mock_response.text = '{"error": "server error"}'
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            response=mock_response
        )

        with patch.object(client.session, "request", return_value=mock_response):
            with pytest.raises(APIError) as exc_info:
                client._request("GET", "http://test.com")

            assert exc_info.value.status_code == 500

    def test_timeout_retry(self, client):
        """Test retry on timeout."""
        mock_success = Mock()
        mock_success.status_code = 200
        mock_success.content = b'{"data": "success"}'
        mock_success.json.return_value = {"data": "success"}
        mock_success.raise_for_status = Mock()

        with patch.object(
            client.session,
            "request",
            side_effect=[
                requests.Timeout(),
                mock_success,
            ],
        ):
            with patch("time.sleep"):
                result = client._request("GET", "http://test.com")

            assert result == {"data": "success"}

    def test_max_retries_exhausted(self, client):
        """Test that exception is raised after max retries."""
        client.max_retries = 2

        with patch.object(
            client.session,
            "request",
            side_effect=requests.ConnectionError("Connection failed"),
        ):
            with patch("time.sleep"):
                with pytest.raises(requests.ConnectionError):
                    client._request("GET", "http://test.com")


class TestPolymarketClientMethods:
    """Tests for PolymarketClient API methods."""

    @pytest.fixture
    def client(self):
        """Create a client with mocked request."""
        with patch.object(PolymarketClient, "_init_clob_client"):
            return PolymarketClient(rate_limit=0)

    def test_get_markets(self, client):
        """Test get_markets method."""
        expected = [{"condition_id": "test123"}]

        with patch.object(client, "_request", return_value=expected):
            result = client.get_markets(limit=10)

            assert result == expected
            client._request.assert_called_once()
            call_args = client._request.call_args
            assert "markets" in call_args[0][1]  # URL

    def test_get_all_markets_pagination(self, client):
        """Test get_all_markets handles pagination."""
        page1 = [{"id": "1"}, {"id": "2"}]
        page2 = [{"id": "3"}]
        page3 = []

        with patch.object(
            client, "get_markets", side_effect=[page1, page2, page3]
        ):
            result = client.get_all_markets()

            assert len(result) == 3
            assert client.get_markets.call_count == 3

    def test_get_order_book(self, client):
        """Test get_order_book method."""
        token_id = "0x" + "a" * 64
        expected = {"bids": [], "asks": []}

        with patch.object(client, "_request", return_value=expected):
            result = client.get_order_book(token_id)

            assert result == expected
            client._request.assert_called_once()

    def test_get_order_books_partial_failure(self, client):
        """Test get_order_books handles partial failures."""
        tokens = ["token1", "token2", "token3"]

        def mock_get_book(token_id):
            if token_id == "token2":
                raise APIError("Failed")
            return {"bids": [], "asks": []}

        with patch.object(client, "get_order_book", side_effect=mock_get_book):
            result = client.get_order_books(tokens)

            assert len(result) == 3
            assert "error" in result[1]
            assert "error" not in result[0]

    def test_get_trades(self, client):
        """Test get_trades method."""
        expected = [{"id": "trade1"}]

        with patch.object(client, "_request", return_value=expected):
            result = client.get_trades(limit=50)

            assert result == expected

    def test_get_trades_with_filters(self, client):
        """Test get_trades with token_id filter."""
        token_id = "0x" + "a" * 64

        with patch.object(client, "_request", return_value=[]):
            client.get_trades(token_id=token_id, limit=10)

            call_args = client._request.call_args
            params = call_args[1]["params"]
            assert params["token_id"] == token_id

    def test_health_check_success(self, client):
        """Test health_check returns True when APIs are accessible."""
        with patch.object(client, "get_server_time", return_value={"time": 123}):
            with patch.object(client, "get_markets", return_value=[]):
                assert client.health_check() is True

    def test_health_check_failure(self, client):
        """Test health_check returns False on error."""
        with patch.object(
            client, "get_server_time", side_effect=APIError("Failed")
        ):
            assert client.health_check() is False


class TestPolymarketClientTrading:
    """Tests for trading methods (require auth)."""

    @pytest.fixture
    def client(self):
        """Create a client without clob_client."""
        with patch.object(PolymarketClient, "_init_clob_client"):
            return PolymarketClient(rate_limit=0)

    def test_place_order_no_clob_client(self, client):
        """Test place_order raises error without clob_client."""
        client._clob_client = None

        with pytest.raises(RuntimeError) as exc_info:
            client.place_order("token", "BUY", 0.5, 100)

        assert "py-clob-client" in str(exc_info.value)

    def test_place_order_no_api_key(self, client):
        """Test place_order raises error without API key."""
        client._clob_client = Mock()
        client.api_key = None

        with pytest.raises(AuthenticationError):
            client.place_order("token", "BUY", 0.5, 100)

    def test_cancel_order_no_clob_client(self, client):
        """Test cancel_order raises error without clob_client."""
        client._clob_client = None

        with pytest.raises(RuntimeError):
            client.cancel_order("order123")

    def test_get_my_orders_no_api_key(self, client):
        """Test get_my_orders raises error without API key."""
        client._clob_client = Mock()
        client.api_key = None

        with pytest.raises(AuthenticationError):
            client.get_my_orders()
