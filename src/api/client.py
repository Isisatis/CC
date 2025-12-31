"""Polymarket API client wrapper using py-clob-client."""

import os
import time
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
import requests
from requests.exceptions import RequestException, Timeout, HTTPError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class APIError(Exception):
    """Base exception for API errors."""

    def __init__(self, message: str, status_code: int = None, response: dict = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class RateLimitError(APIError):
    """Raised when rate limit is exceeded."""
    pass


class AuthenticationError(APIError):
    """Raised when authentication fails."""
    pass


class PolymarketClient:
    """
    Client for interacting with Polymarket APIs.

    Uses the official py-clob-client for CLOB operations and direct
    HTTP requests for Gamma API (market metadata).
    """

    CLOB_BASE_URL = "https://clob.polymarket.com"
    GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

    def __init__(
        self,
        clob_url: str = None,
        gamma_url: str = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        api_passphrase: Optional[str] = None,
        rate_limit: float = 0.1,
        timeout: int = 30,
        max_retries: int = 3,
    ):
        """
        Initialize Polymarket client.

        Args:
            clob_url: CLOB API base URL
            gamma_url: Gamma API base URL
            api_key: API key for authenticated requests
            api_secret: API secret for signing
            api_passphrase: API passphrase
            rate_limit: Minimum seconds between requests
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts for transient errors
        """
        self.clob_url = clob_url or self.CLOB_BASE_URL
        self.gamma_url = gamma_url or self.GAMMA_BASE_URL
        self.api_key = api_key or os.getenv("POLYMARKET_API_KEY")
        self.api_secret = api_secret or os.getenv("POLYMARKET_API_SECRET")
        self.api_passphrase = api_passphrase or os.getenv("POLYMARKET_API_PASSPHRASE")
        self.rate_limit = rate_limit
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_request_time = 0

        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

        logger.info(
            f"PolymarketClient initialized | clob_url={self.clob_url} | "
            f"gamma_url={self.gamma_url} | rate_limit={rate_limit}s"
        )

        self._clob_client = None
        self._init_clob_client()

    def _init_clob_client(self):
        """Initialize the official py-clob-client if credentials are available."""
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds

            self._clob_client = ClobClient(
                host=self.clob_url,
                chain_id=137,
            )

            if self.api_key and self.api_secret and self.api_passphrase:
                creds = ApiCreds(
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    api_passphrase=self.api_passphrase,
                )
                self._clob_client.set_api_creds(creds)
                logger.info("py-clob-client initialized with API credentials")
            else:
                logger.info("py-clob-client initialized (read-only, no credentials)")

        except ImportError:
            logger.warning(
                "py-clob-client not installed. Trading operations unavailable. "
                "Install with: pip install py-clob-client"
            )
            self._clob_client = None
        except Exception as e:
            logger.warning(f"Failed to initialize py-clob-client: {e}")
            self._clob_client = None

    def _rate_limit_wait(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit:
            sleep_time = self.rate_limit - elapsed
            logger.debug(f"Rate limiting: sleeping {sleep_time:.3f}s")
            time.sleep(sleep_time)
        self._last_request_time = time.time()

    def _request(
        self,
        method: str,
        url: str,
        params: Dict = None,
        data: Dict = None,
        timeout: int = None,
    ) -> Dict[str, Any]:
        """
        Make an HTTP request with rate limiting and retry logic.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Full URL to request
            params: Query parameters
            data: Request body data
            timeout: Request timeout in seconds

        Returns:
            Response JSON data

        Raises:
            APIError: For API-level errors
            RateLimitError: When rate limited
            RequestException: For network errors
        """
        timeout = timeout or self.timeout
        last_exception = None

        for attempt in range(self.max_retries):
            self._rate_limit_wait()

            try:
                logger.debug(
                    f"HTTP {method} {url} | params={params} | attempt={attempt + 1}"
                )

                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=data,
                    timeout=timeout,
                )

                logger.debug(
                    f"Response: status={response.status_code} | "
                    f"size={len(response.content)} bytes"
                )

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 5))
                    logger.warning(
                        f"Rate limited. Retry after {retry_after}s | attempt={attempt + 1}"
                    )
                    time.sleep(retry_after)
                    continue

                if response.status_code in (401, 403):
                    logger.error(f"Authentication error: {response.status_code}")
                    raise AuthenticationError(
                        f"Authentication failed: {response.status_code}",
                        status_code=response.status_code,
                    )

                response.raise_for_status()
                return response.json()

            except Timeout as e:
                logger.warning(f"Request timeout | url={url} | attempt={attempt + 1}")
                last_exception = e
                time.sleep(2 ** attempt)

            except HTTPError as e:
                logger.error(
                    f"HTTP error | url={url} | status={e.response.status_code} | "
                    f"response={e.response.text[:200]}"
                )
                raise APIError(
                    str(e),
                    status_code=e.response.status_code,
                    response={"text": e.response.text},
                )

            except RequestException as e:
                logger.warning(
                    f"Request failed | url={url} | error={type(e).__name__}: {e} | "
                    f"attempt={attempt + 1}"
                )
                last_exception = e
                time.sleep(2 ** attempt)

        logger.error(f"All {self.max_retries} retries exhausted for {url}")
        raise last_exception or APIError(f"Request failed after {self.max_retries} attempts")

    # ==================== Gamma API Methods ====================

    def get_markets(
        self,
        active: bool = True,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Fetch available markets from Gamma API."""
        logger.info(f"Fetching markets | active={active} | limit={limit} | offset={offset}")

        params = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
        }

        url = f"{self.gamma_url}/markets"
        result = self._request("GET", url, params=params)

        logger.info(f"Retrieved {len(result) if isinstance(result, list) else 0} markets")
        return result

    def get_all_markets(self, active: bool = True) -> List[Dict[str, Any]]:
        """Fetch all markets using pagination."""
        logger.info(f"Fetching all markets | active={active}")

        all_markets = []
        offset = 0
        limit = 100

        while True:
            markets = self.get_markets(active=active, limit=limit, offset=offset)
            if not markets:
                break
            all_markets.extend(markets)
            offset += limit

            logger.debug(f"Pagination progress: fetched {len(all_markets)} markets")

            if offset > 10000:
                logger.warning("Safety limit reached (10000 markets)")
                break

        logger.info(f"Total markets fetched: {len(all_markets)}")
        return all_markets

    def get_market_details(self, condition_id: str) -> Dict[str, Any]:
        """Get detailed information about a specific market."""
        logger.debug(f"Fetching market details | condition_id={condition_id[:16]}...")
        url = f"{self.gamma_url}/markets/{condition_id}"
        return self._request("GET", url)

    def get_events(
        self,
        active: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Fetch events from Gamma API."""
        logger.debug(f"Fetching events | active={active} | limit={limit}")

        params = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
        }

        url = f"{self.gamma_url}/events"
        return self._request("GET", url, params=params)

    # ==================== CLOB API Methods ====================

    def get_order_book(self, token_id: str) -> Dict[str, Any]:
        """Get the order book for a specific token."""
        logger.debug(f"Fetching order book | token_id={token_id[:16]}...")
        url = f"{self.clob_url}/book"
        params = {"token_id": token_id}
        return self._request("GET", url, params=params)

    def get_order_books(self, token_ids: List[str]) -> List[Dict[str, Any]]:
        """Get order books for multiple tokens."""
        logger.info(f"Fetching {len(token_ids)} order books")

        books = []
        for i, token_id in enumerate(token_ids):
            try:
                book = self.get_order_book(token_id)
                books.append({"token_id": token_id, **book})
            except Exception as e:
                logger.warning(f"Failed to fetch order book for {token_id[:16]}: {e}")
                books.append({"token_id": token_id, "error": str(e)})

            if (i + 1) % 10 == 0:
                logger.debug(f"Progress: {i + 1}/{len(token_ids)} order books fetched")

        return books

    def get_price(self, token_id: str) -> Dict[str, Any]:
        """Get the current price for a token."""
        url = f"{self.clob_url}/price"
        params = {"token_id": token_id}
        return self._request("GET", url, params=params)

    def get_prices(self, token_ids: List[str]) -> List[Dict[str, Any]]:
        """Get prices for multiple tokens."""
        url = f"{self.clob_url}/prices"
        params = {"token_ids": ",".join(token_ids)}
        return self._request("GET", url, params=params)

    def get_midpoint(self, token_id: str) -> Dict[str, Any]:
        """Get the midpoint price for a token."""
        url = f"{self.clob_url}/midpoint"
        params = {"token_id": token_id}
        return self._request("GET", url, params=params)

    def get_spread(self, token_id: str) -> Dict[str, Any]:
        """Get the bid-ask spread for a token."""
        url = f"{self.clob_url}/spread"
        params = {"token_id": token_id}
        return self._request("GET", url, params=params)

    def get_trades(
        self,
        token_id: str = None,
        maker: str = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get recent trades from CLOB API."""
        logger.debug(f"Fetching trades | token_id={token_id} | limit={limit}")

        url = f"{self.clob_url}/trades"
        params = {"limit": limit}

        if token_id:
            params["token_id"] = token_id
        if maker:
            params["maker"] = maker

        return self._request("GET", url, params=params)

    def get_last_trade_price(self, token_id: str) -> Dict[str, Any]:
        """Get the last trade price for a token."""
        url = f"{self.clob_url}/last-trade-price"
        params = {"token_id": token_id}
        return self._request("GET", url, params=params)

    # ==================== Trading Methods (Require Auth) ====================

    def place_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        order_type: str = "GTC",
    ) -> Dict[str, Any]:
        """Place an order on Polymarket. Requires API credentials."""
        logger.info(
            f"Placing order | token_id={token_id[:16]}... | "
            f"side={side} | price={price} | size={size}"
        )

        if not self._clob_client:
            raise RuntimeError(
                "py-clob-client not available. Install with: pip install py-clob-client"
            )

        if not self.api_key:
            raise AuthenticationError(
                "API credentials required for trading. Set POLYMARKET_API_KEY, "
                "POLYMARKET_API_SECRET, and POLYMARKET_API_PASSPHRASE."
            )

        from py_clob_client.clob_types import OrderArgs
        from py_clob_client.order_builder.constants import BUY, SELL

        side_enum = BUY if side.upper() == "BUY" else SELL

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side_enum,
        )

        result = self._clob_client.create_and_post_order(order_args)
        logger.info(f"Order placed successfully | result={result}")
        return result

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order. Requires API credentials."""
        logger.info(f"Cancelling order | order_id={order_id}")

        if not self._clob_client:
            raise RuntimeError("py-clob-client not available")

        if not self.api_key:
            raise AuthenticationError("API credentials required for trading")

        result = self._clob_client.cancel(order_id)
        success = result.get("success", False)

        if success:
            logger.info(f"Order cancelled successfully | order_id={order_id}")
        else:
            logger.warning(f"Order cancellation failed | order_id={order_id}")

        return success

    def get_my_orders(
        self,
        market: str = None,
        asset_id: str = None,
    ) -> List[Dict[str, Any]]:
        """Get user's open orders. Requires API credentials."""
        logger.debug(f"Fetching user orders | market={market} | asset_id={asset_id}")

        if not self._clob_client:
            raise RuntimeError("py-clob-client not available")

        if not self.api_key:
            raise AuthenticationError("API credentials required")

        params = {}
        if market:
            params["market"] = market
        if asset_id:
            params["asset_id"] = asset_id

        return self._clob_client.get_orders(**params)

    # ==================== Utility Methods ====================

    def get_server_time(self) -> Dict[str, Any]:
        """Get the CLOB server time."""
        url = f"{self.clob_url}/time"
        return self._request("GET", url)

    def health_check(self) -> bool:
        """Check if the APIs are accessible."""
        logger.info("Performing health check...")

        try:
            self.get_server_time()
            logger.debug("CLOB API: OK")

            self.get_markets(limit=1)
            logger.debug("Gamma API: OK")

            logger.info("Health check passed")
            return True

        except Exception as e:
            logger.error(f"Health check failed: {type(e).__name__}: {e}")
            return False

    def get_market_summary(self, condition_id: str) -> Dict[str, Any]:
        """Get a comprehensive summary of a market including order books."""
        logger.info(f"Fetching market summary | condition_id={condition_id[:16]}...")

        market = self.get_market_details(condition_id)
        tokens = market.get("tokens", [])
        order_books = []

        for token in tokens:
            token_id = token.get("token_id")
            if token_id:
                try:
                    book = self.get_order_book(token_id)
                    spread = self.get_spread(token_id)
                    order_books.append({
                        "token_id": token_id,
                        "outcome": token.get("outcome"),
                        "order_book": book,
                        "spread": spread,
                    })
                except Exception as e:
                    logger.warning(f"Failed to fetch data for token {token_id[:16]}: {e}")
                    order_books.append({
                        "token_id": token_id,
                        "outcome": token.get("outcome"),
                        "error": str(e),
                    })

        return {
            "market": market,
            "order_books": order_books,
            "fetched_at": datetime.utcnow().isoformat(),
        }
