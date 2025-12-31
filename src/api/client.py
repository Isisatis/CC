"""
Polymarket API Client - Full Implementation

Provides complete access to:
- CLOB API (trading, orderbooks, trades)
- Gamma API (market metadata, events, stats)

With built-in:
- Market filtering (active/closed/resolved)
- Liquidity metrics calculation
- Rate limiting and retry logic
"""

import os
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple, Generator
from decimal import Decimal
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

from .models import (
    MarketStatus, MarketFilter, OrderSide,
    GammaMarket, CLOBMarket, CLOBToken,
    OrderBook, OrderBookLevel, Trade, TradeHistory,
    Order, LiquidityMetrics, Event, PaginatedResponse
)

load_dotenv()

logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple rate limiter for API calls"""

    def __init__(self, calls_per_second: float = 10):
        self.min_interval = 1.0 / calls_per_second
        self.last_call = 0.0

    def wait(self):
        """Wait if needed to respect rate limit"""
        now = time.time()
        elapsed = now - self.last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_call = time.time()


class BaseAPIClient:
    """Base class for API clients with common functionality"""

    def __init__(
        self,
        base_url: str,
        timeout: int = 30,
        max_retries: int = 3,
        calls_per_second: float = 10,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.rate_limiter = RateLimiter(calls_per_second)

        self.session = requests.Session()
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        headers: Optional[Dict] = None,
    ) -> Any:
        """Make an HTTP request with rate limiting and error handling"""
        self.rate_limiter.wait()

        url = f"{self.base_url}{endpoint}"
        default_headers = {"Accept": "application/json"}
        if headers:
            default_headers.update(headers)

        try:
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                headers=default_headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json() if response.text else {}
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error for {url}: {e}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for {url}: {e}")
            raise

    def get(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        return self._request("GET", endpoint, params=params)

    def post(self, endpoint: str, data: Optional[Dict] = None) -> Any:
        return self._request("POST", endpoint, json_data=data)


# ============================================================================
# Gamma API Client (Market Metadata & Events)
# ============================================================================

class GammaAPIClient(BaseAPIClient):
    """
    Client for Polymarket Gamma API

    Gamma provides market metadata, event information, and aggregated stats.
    This is the primary source for market discovery and filtering.
    """

    def __init__(
        self,
        base_url: str = "https://gamma-api.polymarket.com",
        **kwargs
    ):
        super().__init__(base_url, **kwargs)

    # -------------------------------------------------------------------------
    # Markets
    # -------------------------------------------------------------------------

    def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: Optional[bool] = None,
        closed: Optional[bool] = None,
        archived: Optional[bool] = None,
        order: str = "volume24hr",
        ascending: bool = False,
        **extra_params
    ) -> List[GammaMarket]:
        """
        Fetch markets from Gamma API

        Args:
            limit: Max results (max 100)
            offset: Pagination offset
            active: Filter by active status
            closed: Filter by closed status
            archived: Filter by archived status
            order: Sort field (volume24hr, liquidity, createdAt, endDate)
            ascending: Sort direction

        Returns:
            List of GammaMarket objects
        """
        params = {
            "limit": min(limit, 100),
            "offset": offset,
            "order": order,
            "ascending": str(ascending).lower(),
        }

        if active is not None:
            params["active"] = str(active).lower()
        if closed is not None:
            params["closed"] = str(closed).lower()
        if archived is not None:
            params["archived"] = str(archived).lower()

        params.update(extra_params)

        data = self.get("/markets", params)
        if isinstance(data, list):
            return [GammaMarket.model_validate(m) for m in data]
        return []

    def get_all_markets(
        self,
        batch_size: int = 100,
        **filter_params
    ) -> Generator[GammaMarket, None, None]:
        """
        Iterate through all markets with pagination

        Yields:
            GammaMarket objects
        """
        offset = 0
        while True:
            markets = self.get_markets(
                limit=batch_size,
                offset=offset,
                **filter_params
            )
            if not markets:
                break
            for market in markets:
                yield market
            if len(markets) < batch_size:
                break
            offset += batch_size

    def get_market(self, market_id: str) -> Optional[GammaMarket]:
        """Get a single market by ID"""
        try:
            data = self.get(f"/markets/{market_id}")
            return GammaMarket.model_validate(data) if data else None
        except requests.exceptions.HTTPError:
            return None

    def search_markets(
        self,
        query: str,
        limit: int = 100,
        active: bool = True
    ) -> List[GammaMarket]:
        """
        Search markets by text query

        Args:
            query: Search string
            limit: Max results
            active: Only return active markets

        Returns:
            Matching markets
        """
        params = {
            "limit": limit,
            "_q": query,
        }
        if active:
            params["active"] = "true"
            params["closed"] = "false"

        data = self.get("/markets", params)
        if isinstance(data, list):
            return [GammaMarket.model_validate(m) for m in data]
        return []

    # -------------------------------------------------------------------------
    # Events
    # -------------------------------------------------------------------------

    def get_events(
        self,
        limit: int = 100,
        offset: int = 0,
        active: Optional[bool] = None,
        closed: Optional[bool] = None,
        order: str = "volume",
        ascending: bool = False,
    ) -> List[Event]:
        """
        Fetch events (groups of related markets)

        Args:
            limit: Max results
            offset: Pagination offset
            active: Filter by active
            closed: Filter by closed
            order: Sort field
            ascending: Sort direction

        Returns:
            List of Event objects
        """
        params = {
            "limit": min(limit, 100),
            "offset": offset,
            "order": order,
            "ascending": str(ascending).lower(),
        }

        if active is not None:
            params["active"] = str(active).lower()
        if closed is not None:
            params["closed"] = str(closed).lower()

        data = self.get("/events", params)
        if isinstance(data, list):
            return [Event.model_validate(e) for e in data]
        return []

    def get_event(self, event_id: str) -> Optional[Event]:
        """Get event by ID with its markets"""
        try:
            data = self.get(f"/events/{event_id}")
            return Event.model_validate(data) if data else None
        except requests.exceptions.HTTPError:
            return None

    def get_event_by_slug(self, slug: str) -> Optional[Event]:
        """Get event by slug"""
        try:
            data = self.get(f"/events/slug/{slug}")
            return Event.model_validate(data) if data else None
        except requests.exceptions.HTTPError:
            return None


# ============================================================================
# CLOB API Client (Trading)
# ============================================================================

class CLOBAPIClient(BaseAPIClient):
    """
    Client for Polymarket CLOB API

    CLOB (Central Limit Order Book) handles all trading operations:
    order books, trades, order placement, etc.
    """

    def __init__(
        self,
        base_url: str = "https://clob.polymarket.com",
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        passphrase: Optional[str] = None,
        **kwargs
    ):
        super().__init__(base_url, **kwargs)
        self.api_key = api_key or os.getenv("POLYMARKET_API_KEY")
        self.api_secret = api_secret or os.getenv("POLYMARKET_SECRET")
        self.passphrase = passphrase or os.getenv("POLYMARKET_PASSPHRASE")

    def _auth_headers(self) -> Dict[str, str]:
        """Generate auth headers for authenticated endpoints"""
        # Note: Actual Polymarket auth uses L1/L2 signatures
        # This is placeholder for API key auth
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    # -------------------------------------------------------------------------
    # Market Info
    # -------------------------------------------------------------------------

    def get_markets(self, next_cursor: Optional[str] = None) -> Tuple[List[CLOBMarket], Optional[str]]:
        """
        Get all CLOB markets

        Returns:
            Tuple of (markets list, next cursor for pagination)
        """
        params = {}
        if next_cursor:
            params["next_cursor"] = next_cursor

        data = self.get("/markets", params)
        markets = []
        cursor = data.get("next_cursor") if isinstance(data, dict) else None

        market_list = data if isinstance(data, list) else data.get("data", [])
        for m in market_list:
            try:
                markets.append(CLOBMarket.model_validate(m))
            except Exception as e:
                logger.warning(f"Failed to parse market: {e}")

        return markets, cursor

    def get_all_clob_markets(self) -> Generator[CLOBMarket, None, None]:
        """Iterate through all CLOB markets"""
        cursor = None
        while True:
            markets, cursor = self.get_markets(cursor)
            for m in markets:
                yield m
            if not cursor:
                break

    def get_market(self, condition_id: str) -> Optional[CLOBMarket]:
        """Get CLOB market by condition ID"""
        try:
            data = self.get(f"/markets/{condition_id}")
            return CLOBMarket.model_validate(data) if data else None
        except requests.exceptions.HTTPError:
            return None

    # -------------------------------------------------------------------------
    # Order Book
    # -------------------------------------------------------------------------

    def get_order_book(self, token_id: str) -> OrderBook:
        """
        Get order book for a token

        Args:
            token_id: The token ID to get book for

        Returns:
            OrderBook with bids and asks
        """
        data = self.get(f"/book", params={"token_id": token_id})

        bids = [
            OrderBookLevel(price=float(b["price"]), size=float(b["size"]))
            for b in data.get("bids", [])
        ]
        asks = [
            OrderBookLevel(price=float(a["price"]), size=float(a["size"]))
            for a in data.get("asks", [])
        ]

        return OrderBook(
            token_id=token_id,
            market=data.get("market"),
            asset_id=data.get("asset_id"),
            hash=data.get("hash"),
            timestamp=data.get("timestamp"),
            bids=sorted(bids, key=lambda x: x.price, reverse=True),
            asks=sorted(asks, key=lambda x: x.price),
        )

    def get_order_books(self, token_ids: List[str]) -> Dict[str, OrderBook]:
        """Get order books for multiple tokens"""
        return {tid: self.get_order_book(tid) for tid in token_ids}

    def get_midpoint(self, token_id: str) -> Optional[float]:
        """Get midpoint price for a token"""
        try:
            data = self.get(f"/midpoint", params={"token_id": token_id})
            return float(data.get("mid")) if data.get("mid") else None
        except:
            return None

    def get_price(self, token_id: str, side: str) -> Optional[float]:
        """Get best price for a side"""
        try:
            data = self.get(f"/price", params={"token_id": token_id, "side": side})
            return float(data.get("price")) if data.get("price") else None
        except:
            return None

    def get_spread(self, token_id: str) -> Optional[float]:
        """Get bid-ask spread"""
        try:
            data = self.get(f"/spread", params={"token_id": token_id})
            return float(data.get("spread")) if data.get("spread") else None
        except:
            return None

    # -------------------------------------------------------------------------
    # Trades
    # -------------------------------------------------------------------------

    def get_trades(
        self,
        token_id: Optional[str] = None,
        market: Optional[str] = None,
        maker: Optional[str] = None,
        limit: int = 100,
        before: Optional[int] = None,
        after: Optional[int] = None,
    ) -> TradeHistory:
        """
        Get trade history

        Args:
            token_id: Filter by token
            market: Filter by market (condition_id)
            maker: Filter by maker address
            limit: Max trades to return
            before: Get trades before this timestamp
            after: Get trades after this timestamp

        Returns:
            TradeHistory with trades list
        """
        params = {"limit": limit}
        if token_id:
            params["asset_id"] = token_id
        if market:
            params["market"] = market
        if maker:
            params["maker"] = maker
        if before:
            params["before"] = before
        if after:
            params["after"] = after

        data = self.get("/trades", params)

        trades = []
        for t in data if isinstance(data, list) else data.get("data", []):
            try:
                trades.append(Trade.model_validate(t))
            except Exception as e:
                logger.warning(f"Failed to parse trade: {e}")

        return TradeHistory(
            token_id=token_id or "",
            trades=trades,
            next_cursor=data.get("next_cursor") if isinstance(data, dict) else None
        )

    def get_last_trade_price(self, token_id: str) -> Optional[float]:
        """Get last traded price for a token"""
        try:
            data = self.get(f"/last-trade-price", params={"token_id": token_id})
            return float(data.get("price")) if data.get("price") else None
        except:
            return None

    # -------------------------------------------------------------------------
    # User Orders (Authenticated)
    # -------------------------------------------------------------------------

    def get_orders(
        self,
        market: Optional[str] = None,
        asset_id: Optional[str] = None,
        state: str = "LIVE",
    ) -> List[Order]:
        """
        Get user's orders (requires authentication)

        Args:
            market: Filter by market condition_id
            asset_id: Filter by token_id
            state: Order state filter (LIVE, MATCHED, CANCELLED, ALL)

        Returns:
            List of Order objects
        """
        params = {"state": state}
        if market:
            params["market"] = market
        if asset_id:
            params["asset_id"] = asset_id

        data = self.get("/orders", params)
        orders = []
        for o in data if isinstance(data, list) else data.get("data", []):
            try:
                orders.append(Order.model_validate(o))
            except Exception as e:
                logger.warning(f"Failed to parse order: {e}")

        return orders

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID"""
        try:
            data = self.get(f"/order/{order_id}")
            return Order.model_validate(data) if data else None
        except:
            return None

    # -------------------------------------------------------------------------
    # Tick Size / Trading Info
    # -------------------------------------------------------------------------

    def get_tick_size(self, token_id: str) -> Optional[float]:
        """Get minimum tick size for a token"""
        try:
            data = self.get(f"/tick-size", params={"token_id": token_id})
            return float(data.get("minimum_tick_size")) if data else None
        except:
            return None

    def get_neg_risk(self, token_id: str) -> bool:
        """Check if token is neg risk"""
        try:
            data = self.get(f"/neg-risk", params={"token_id": token_id})
            return data.get("neg_risk", False)
        except:
            return False


# ============================================================================
# Unified Polymarket Client
# ============================================================================

class PolymarketClient:
    """
    Unified client for all Polymarket APIs

    Combines Gamma (metadata) and CLOB (trading) APIs with:
    - Market filtering by status (active/closed/resolved)
    - Liquidity metrics calculation
    - Convenient data access methods
    """

    def __init__(
        self,
        clob_url: str = "https://clob.polymarket.com",
        gamma_url: str = "https://gamma-api.polymarket.com",
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        passphrase: Optional[str] = None,
        timeout: int = 30,
        max_retries: int = 3,
        calls_per_second: float = 10,
    ):
        """
        Initialize the unified Polymarket client

        Args:
            clob_url: CLOB API base URL
            gamma_url: Gamma API base URL
            api_key: Polymarket API key
            api_secret: Polymarket API secret
            passphrase: Polymarket API passphrase
            timeout: Request timeout in seconds
            max_retries: Max retry attempts
            calls_per_second: Rate limit
        """
        common_kwargs = {
            "timeout": timeout,
            "max_retries": max_retries,
            "calls_per_second": calls_per_second,
        }

        self.gamma = GammaAPIClient(base_url=gamma_url, **common_kwargs)
        self.clob = CLOBAPIClient(
            base_url=clob_url,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            **common_kwargs
        )

        # Cache for market data
        self._market_cache: Dict[str, GammaMarket] = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl = timedelta(minutes=5)

    # -------------------------------------------------------------------------
    # Market Discovery & Filtering
    # -------------------------------------------------------------------------

    def get_markets(
        self,
        filter: Optional[MarketFilter] = None,
        use_cache: bool = True,
    ) -> List[GammaMarket]:
        """
        Get markets with optional filtering

        Args:
            filter: MarketFilter object with filter criteria
            use_cache: Whether to use cached data

        Returns:
            List of filtered GammaMarket objects
        """
        f = filter or MarketFilter()

        # Determine API filter params
        api_params = {
            "limit": f.limit,
            "offset": f.offset,
            "order": f.order_by or "volume24hr",
            "ascending": f.ascending,
        }

        # Status filtering
        if f.status:
            if f.status == MarketStatus.ACTIVE:
                api_params["active"] = True
                api_params["closed"] = False
            elif f.status == MarketStatus.CLOSED:
                api_params["closed"] = True
            elif f.status == MarketStatus.RESOLVED:
                api_params["closed"] = True
            elif f.status == MarketStatus.ARCHIVED:
                api_params["archived"] = True
        elif f.active_only:
            api_params["active"] = True
            api_params["closed"] = False
        elif f.closed_only:
            api_params["closed"] = True

        # Fetch from API
        markets = self.gamma.get_markets(**api_params)

        # Apply local filters
        filtered = []
        for m in markets:
            # Liquidity filter
            if f.min_liquidity and (m.liquidity or 0) < f.min_liquidity:
                continue

            # Volume filter
            if f.min_volume_24h and (m.volume_24hr or 0) < f.min_volume_24h:
                continue

            # Search query
            if f.search_query:
                query = f.search_query.lower()
                if query not in m.question.lower() and query not in (m.description or "").lower():
                    continue

            # Event slug filter
            if f.event_slug and m.event_slug != f.event_slug:
                continue

            filtered.append(m)

        return filtered

    def get_active_markets(
        self,
        min_liquidity: Optional[float] = None,
        min_volume: Optional[float] = None,
        limit: int = 100,
    ) -> List[GammaMarket]:
        """Convenience method to get only active markets"""
        return self.get_markets(MarketFilter(
            status=MarketStatus.ACTIVE,
            min_liquidity=min_liquidity,
            min_volume_24h=min_volume,
            limit=limit,
        ))

    def get_closed_markets(self, limit: int = 100) -> List[GammaMarket]:
        """Get closed/resolved markets"""
        return self.get_markets(MarketFilter(
            status=MarketStatus.CLOSED,
            limit=limit,
        ))

    def get_resolved_markets(self, limit: int = 100) -> List[GammaMarket]:
        """Get resolved markets (with winners determined)"""
        markets = self.gamma.get_markets(closed=True, limit=limit)
        # Filter to only those with a winner
        return [m for m in markets if any(t.get("winner") for t in m.tokens)]

    def search_markets(self, query: str, active_only: bool = True) -> List[GammaMarket]:
        """Search markets by text"""
        return self.gamma.search_markets(query, active=active_only)

    def get_market(self, market_id: str) -> Optional[GammaMarket]:
        """Get single market by ID"""
        return self.gamma.get_market(market_id)

    # -------------------------------------------------------------------------
    # Events
    # -------------------------------------------------------------------------

    def get_events(
        self,
        active_only: bool = True,
        limit: int = 100,
    ) -> List[Event]:
        """Get events (market groups)"""
        return self.gamma.get_events(
            active=active_only if active_only else None,
            limit=limit
        )

    def get_event(self, event_id: str) -> Optional[Event]:
        """Get event by ID"""
        return self.gamma.get_event(event_id)

    # -------------------------------------------------------------------------
    # Order Book & Trading Data
    # -------------------------------------------------------------------------

    def get_order_book(self, token_id: str) -> OrderBook:
        """Get order book for a token"""
        return self.clob.get_order_book(token_id)

    def get_order_books_for_market(self, market: GammaMarket) -> Dict[str, OrderBook]:
        """Get order books for all tokens in a market"""
        token_ids = [t.get("token_id") for t in market.tokens if t.get("token_id")]
        return {tid: self.clob.get_order_book(tid) for tid in token_ids}

    def get_trades(
        self,
        token_id: Optional[str] = None,
        market_id: Optional[str] = None,
        limit: int = 100,
    ) -> TradeHistory:
        """Get trade history"""
        return self.clob.get_trades(
            token_id=token_id,
            market=market_id,
            limit=limit
        )

    def get_prices(self, market: GammaMarket) -> Dict[str, float]:
        """Get current prices for all tokens in a market"""
        prices = {}
        for token in market.tokens:
            tid = token.get("token_id")
            if tid:
                mid = self.clob.get_midpoint(tid)
                if mid is not None:
                    prices[tid] = mid
        return prices

    # -------------------------------------------------------------------------
    # Liquidity Analysis
    # -------------------------------------------------------------------------

    def calculate_liquidity_metrics(
        self,
        token_id: str,
        include_trades: bool = True,
    ) -> LiquidityMetrics:
        """
        Calculate comprehensive liquidity metrics for a token

        Args:
            token_id: The token to analyze
            include_trades: Whether to include 24h trade stats

        Returns:
            LiquidityMetrics with all calculated values
        """
        # Get order book
        book = self.clob.get_order_book(token_id)

        metrics = LiquidityMetrics(
            token_id=token_id,
            best_bid=book.best_bid,
            best_ask=book.best_ask,
            mid_price=book.mid_price,
            spread=book.spread,
            bid_depth_total=book.total_bid_depth,
            ask_depth_total=book.total_ask_depth,
            bid_value_total=book.total_bid_value,
            ask_value_total=book.total_ask_value,
        )

        # Spread in bps
        if book.spread and book.mid_price:
            metrics.spread_bps = (book.spread / book.mid_price) * 10000

        # Depth at various price levels
        if book.best_bid:
            for pct, attr in [(0.01, "bid_depth_1pct"), (0.05, "bid_depth_5pct"), (0.10, "bid_depth_10pct")]:
                threshold = book.best_bid * (1 - pct)
                depth_value = sum(
                    b.price * b.size for b in book.bids if b.price >= threshold
                )
                setattr(metrics, attr, depth_value)

        if book.best_ask:
            for pct, attr in [(0.01, "ask_depth_1pct"), (0.05, "ask_depth_5pct"), (0.10, "ask_depth_10pct")]:
                threshold = book.best_ask * (1 + pct)
                depth_value = sum(
                    a.price * a.size for a in book.asks if a.price <= threshold
                )
                setattr(metrics, attr, depth_value)

        # Price impact
        for size in [100, 500, 1000]:
            buy_impact = book.price_impact(size, OrderSide.BUY)
            sell_impact = book.price_impact(size, OrderSide.SELL)
            setattr(metrics, f"impact_buy_{size}", buy_impact)
            setattr(metrics, f"impact_sell_{size}", sell_impact)

        # Trade stats (last 24h)
        if include_trades:
            try:
                cutoff = int((datetime.utcnow() - timedelta(hours=24)).timestamp())
                trades = self.clob.get_trades(token_id=token_id, after=cutoff, limit=1000)
                if trades.trades:
                    metrics.volume_24h = trades.total_volume
                    metrics.trade_count_24h = trades.trade_count
                    if trades.trade_count > 0:
                        metrics.avg_trade_size_24h = trades.total_volume / trades.trade_count
            except Exception as e:
                logger.warning(f"Failed to get trade stats: {e}")

        return metrics

    def get_market_liquidity(self, market: GammaMarket) -> Dict[str, LiquidityMetrics]:
        """Get liquidity metrics for all tokens in a market"""
        result = {}
        for token in market.tokens:
            tid = token.get("token_id")
            if tid:
                try:
                    result[tid] = self.calculate_liquidity_metrics(tid)
                except Exception as e:
                    logger.warning(f"Failed to get liquidity for {tid}: {e}")
        return result

    def get_liquid_markets(
        self,
        min_score: float = 50,
        min_depth: float = 1000,
        max_spread_bps: float = 500,
        limit: int = 50,
    ) -> List[Tuple[GammaMarket, Dict[str, LiquidityMetrics]]]:
        """
        Find markets meeting liquidity criteria

        Args:
            min_score: Minimum liquidity score (0-100)
            min_depth: Minimum order book depth in USDC
            max_spread_bps: Maximum spread in basis points
            limit: Max markets to return

        Returns:
            List of (market, liquidity_metrics) tuples
        """
        results = []

        for market in self.get_active_markets(limit=limit * 2):
            try:
                liq = self.get_market_liquidity(market)
                if not liq:
                    continue

                # Check if any token meets criteria
                meets_criteria = any(
                    m.liquidity_score >= min_score and
                    (m.bid_value_total + m.ask_value_total) >= min_depth and
                    (m.spread_bps or float('inf')) <= max_spread_bps
                    for m in liq.values()
                )

                if meets_criteria:
                    results.append((market, liq))
                    if len(results) >= limit:
                        break

            except Exception as e:
                logger.warning(f"Error checking market {market.id}: {e}")

        return results

    def rank_markets_by_liquidity(
        self,
        markets: Optional[List[GammaMarket]] = None,
        limit: int = 20,
    ) -> List[Tuple[GammaMarket, float, Dict[str, LiquidityMetrics]]]:
        """
        Rank markets by liquidity score

        Args:
            markets: Markets to rank (fetches active if None)
            limit: Max markets to return

        Returns:
            List of (market, avg_score, metrics) sorted by score desc
        """
        if markets is None:
            markets = self.get_active_markets(limit=limit * 2)

        scored = []
        for market in markets[:limit * 2]:
            try:
                liq = self.get_market_liquidity(market)
                if liq:
                    avg_score = sum(m.liquidity_score for m in liq.values()) / len(liq)
                    scored.append((market, avg_score, liq))
            except Exception as e:
                logger.warning(f"Error scoring market {market.id}: {e}")

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    # -------------------------------------------------------------------------
    # User/Trading Operations (Authenticated)
    # -------------------------------------------------------------------------

    def get_my_orders(
        self,
        market_id: Optional[str] = None,
        token_id: Optional[str] = None,
        state: str = "LIVE",
    ) -> List[Order]:
        """Get user's orders"""
        return self.clob.get_orders(market=market_id, asset_id=token_id, state=state)

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID"""
        return self.clob.get_order(order_id)

    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------

    def get_token_ids_for_market(self, market_id: str) -> List[str]:
        """Get all token IDs for a market"""
        market = self.get_market(market_id)
        if market:
            return [t.get("token_id") for t in market.tokens if t.get("token_id")]
        return []

    def health_check(self) -> Dict[str, bool]:
        """Check API connectivity"""
        status = {"gamma": False, "clob": False}
        try:
            self.gamma.get("/markets", {"limit": 1})
            status["gamma"] = True
        except:
            pass
        try:
            self.clob.get("/markets")
            status["clob"] = True
        except:
            pass
        return status
