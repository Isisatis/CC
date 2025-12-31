"""Polymarket API client wrapper using py-clob-client"""

import os
import time
from typing import Optional, Dict, Any, List
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()


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
        rate_limit: float = 0.1,  # seconds between requests
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
        """
        self.clob_url = clob_url or self.CLOB_BASE_URL
        self.gamma_url = gamma_url or self.GAMMA_BASE_URL
        self.api_key = api_key or os.getenv("POLYMARKET_API_KEY")
        self.api_secret = api_secret or os.getenv("POLYMARKET_API_SECRET")
        self.api_passphrase = api_passphrase or os.getenv("POLYMARKET_API_PASSPHRASE")
        self.rate_limit = rate_limit
        self._last_request_time = 0

        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

        # Try to initialize py-clob-client for advanced operations
        self._clob_client = None
        self._init_clob_client()

    def _init_clob_client(self):
        """Initialize the official py-clob-client if credentials are available."""
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds

            # For read-only operations, we don't need credentials
            self._clob_client = ClobClient(
                host=self.clob_url,
                chain_id=137,  # Polygon mainnet
            )

            # If we have API credentials, set them up for trading
            if self.api_key and self.api_secret and self.api_passphrase:
                creds = ApiCreds(
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    api_passphrase=self.api_passphrase,
                )
                self._clob_client.set_api_creds(creds)

        except ImportError:
            self._clob_client = None
        except Exception:
            # Fall back to direct HTTP if clob client fails
            self._clob_client = None

    def _rate_limit_wait(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request_time = time.time()

    def _request(
        self,
        method: str,
        url: str,
        params: Dict = None,
        data: Dict = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """
        Make an HTTP request with rate limiting.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Full URL to request
            params: Query parameters
            data: Request body data
            timeout: Request timeout in seconds

        Returns:
            Response JSON data
        """
        self._rate_limit_wait()

        response = self.session.request(
            method=method,
            url=url,
            params=params,
            json=data,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    # ==================== Gamma API Methods ====================

    def get_markets(
        self,
        active: bool = True,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Fetch available markets from Gamma API.

        Args:
            active: If True, only return active markets
            closed: If True, include closed markets
            limit: Maximum number of markets to return
            offset: Pagination offset

        Returns:
            List of market dictionaries
        """
        params = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
        }

        url = f"{self.gamma_url}/markets"
        return self._request("GET", url, params=params)

    def get_all_markets(self, active: bool = True) -> List[Dict[str, Any]]:
        """
        Fetch all markets using pagination.

        Args:
            active: If True, only return active markets

        Returns:
            List of all market dictionaries
        """
        all_markets = []
        offset = 0
        limit = 100

        while True:
            markets = self.get_markets(
                active=active, limit=limit, offset=offset
            )
            if not markets:
                break
            all_markets.extend(markets)
            offset += limit

            # Safety limit
            if offset > 10000:
                break

        return all_markets

    def get_market_details(self, condition_id: str) -> Dict[str, Any]:
        """
        Get detailed information about a specific market from Gamma API.

        Args:
            condition_id: The market condition ID

        Returns:
            Market details dictionary
        """
        url = f"{self.gamma_url}/markets/{condition_id}"
        return self._request("GET", url)

    def get_events(
        self,
        active: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Fetch events from Gamma API.

        Args:
            active: If True, only return active events
            limit: Maximum number of events to return
            offset: Pagination offset

        Returns:
            List of event dictionaries
        """
        params = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
        }

        url = f"{self.gamma_url}/events"
        return self._request("GET", url, params=params)

    # ==================== CLOB API Methods ====================

    def get_order_book(
        self,
        token_id: str,
    ) -> Dict[str, Any]:
        """
        Get the order book for a specific token from CLOB API.

        Args:
            token_id: The token identifier (condition_id + outcome)

        Returns:
            Order book with bids and asks
        """
        url = f"{self.clob_url}/book"
        params = {"token_id": token_id}
        return self._request("GET", url, params=params)

    def get_order_books(
        self,
        token_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Get order books for multiple tokens.

        Args:
            token_ids: List of token identifiers

        Returns:
            List of order books
        """
        books = []
        for token_id in token_ids:
            try:
                book = self.get_order_book(token_id)
                books.append({"token_id": token_id, **book})
            except Exception as e:
                books.append({"token_id": token_id, "error": str(e)})
        return books

    def get_price(self, token_id: str) -> Dict[str, Any]:
        """
        Get the current price for a token.

        Args:
            token_id: The token identifier

        Returns:
            Price information
        """
        url = f"{self.clob_url}/price"
        params = {"token_id": token_id}
        return self._request("GET", url, params=params)

    def get_prices(self, token_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Get prices for multiple tokens.

        Args:
            token_ids: List of token identifiers

        Returns:
            List of price information
        """
        url = f"{self.clob_url}/prices"
        params = {"token_ids": ",".join(token_ids)}
        return self._request("GET", url, params=params)

    def get_midpoint(self, token_id: str) -> Dict[str, Any]:
        """
        Get the midpoint price for a token.

        Args:
            token_id: The token identifier

        Returns:
            Midpoint price information
        """
        url = f"{self.clob_url}/midpoint"
        params = {"token_id": token_id}
        return self._request("GET", url, params=params)

    def get_spread(self, token_id: str) -> Dict[str, Any]:
        """
        Get the bid-ask spread for a token.

        Args:
            token_id: The token identifier

        Returns:
            Spread information
        """
        url = f"{self.clob_url}/spread"
        params = {"token_id": token_id}
        return self._request("GET", url, params=params)

    def get_trades(
        self,
        token_id: str = None,
        maker: str = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get recent trades from CLOB API.

        Args:
            token_id: Optional token filter
            maker: Optional maker address filter
            limit: Maximum number of trades to return

        Returns:
            List of trade dictionaries
        """
        url = f"{self.clob_url}/trades"
        params = {"limit": limit}

        if token_id:
            params["token_id"] = token_id
        if maker:
            params["maker"] = maker

        return self._request("GET", url, params=params)

    def get_last_trade_price(self, token_id: str) -> Dict[str, Any]:
        """
        Get the last trade price for a token.

        Args:
            token_id: The token identifier

        Returns:
            Last trade price information
        """
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
        """
        Place an order on Polymarket.

        NOTE: Requires API credentials to be configured.

        Args:
            token_id: The token identifier
            side: 'BUY' or 'SELL'
            price: Order price (0-1 for binary markets)
            size: Order size in shares
            order_type: Order type ('GTC', 'FOK', 'GTD')

        Returns:
            Order confirmation details
        """
        if not self._clob_client:
            raise RuntimeError(
                "py-clob-client not available. Install with: pip install py-clob-client"
            )

        if not self.api_key:
            raise RuntimeError(
                "API credentials required for trading. Set POLYMARKET_API_KEY, "
                "POLYMARKET_API_SECRET, and POLYMARKET_API_PASSPHRASE environment variables."
            )

        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL

        side_enum = BUY if side.upper() == "BUY" else SELL

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side_enum,
        )

        return self._clob_client.create_and_post_order(order_args)

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an existing order.

        NOTE: Requires API credentials to be configured.

        Args:
            order_id: The order identifier

        Returns:
            True if cancellation successful
        """
        if not self._clob_client:
            raise RuntimeError("py-clob-client not available")

        if not self.api_key:
            raise RuntimeError("API credentials required for trading")

        result = self._clob_client.cancel(order_id)
        return result.get("success", False)

    def get_my_orders(
        self,
        market: str = None,
        asset_id: str = None,
    ) -> List[Dict[str, Any]]:
        """
        Get user's open orders.

        NOTE: Requires API credentials to be configured.

        Args:
            market: Optional market filter (condition_id)
            asset_id: Optional asset filter (token_id)

        Returns:
            List of open orders
        """
        if not self._clob_client:
            raise RuntimeError("py-clob-client not available")

        if not self.api_key:
            raise RuntimeError("API credentials required")

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
        """
        Check if the APIs are accessible.

        Returns:
            True if both APIs are accessible
        """
        try:
            self.get_server_time()
            self.get_markets(limit=1)
            return True
        except Exception:
            return False

    def get_market_summary(self, condition_id: str) -> Dict[str, Any]:
        """
        Get a comprehensive summary of a market including metadata and order books.

        Args:
            condition_id: The market condition ID

        Returns:
            Dictionary with market details and order book data
        """
        # Get market metadata from Gamma
        market = self.get_market_details(condition_id)

        # Get order books for each outcome token
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
