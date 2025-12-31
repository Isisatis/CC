"""
Kalshi Adapter

Maps Kalshi API data to unified prediction market models.

Kalshi API: https://trading-api.readme.io/reference/getting-started
- REST API for market data
- WebSocket for real-time updates
- Requires API key for trading
"""

import re
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
import requests

from . import (
    Platform, MarketType, PlatformAdapter,
    UnifiedMarket, UnifiedOutcome, MarketStats, DynamicThresholds
)

logger = logging.getLogger(__name__)


class KalshiClient:
    """
    Raw client for Kalshi API.

    Kalshi uses:
    - Events: Groups of related markets
    - Markets: Individual binary contracts
    - Each market has Yes/No outcomes priced 1-99 cents

    API Base: https://trading-api.kalshi.com/trade-api/v2
    """

    BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"
    DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"

    def __init__(
        self,
        api_key: Optional[str] = None,
        use_demo: bool = False,
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.base_url = self.DEMO_URL if use_demo else self.BASE_URL
        self.timeout = timeout
        self.session = requests.Session()

        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"

    def _request(self, method: str, endpoint: str, params: Optional[Dict] = None) -> Any:
        """Make API request"""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Kalshi API error: {e}")
            raise
        except Exception as e:
            logger.error(f"Kalshi request failed: {e}")
            raise

    def get(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        return self._request("GET", endpoint, params)

    # -------------------------------------------------------------------------
    # Events (Groups of Markets)
    # -------------------------------------------------------------------------

    def get_events(
        self,
        status: str = "open",  # open, closed, settled
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get events.

        Returns:
            {
                "events": [...],
                "cursor": "next_page_cursor"
            }
        """
        params = {
            "status": status,
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor
        return self.get("/events", params)

    def get_event(self, event_ticker: str) -> Dict[str, Any]:
        """Get single event by ticker"""
        return self.get(f"/events/{event_ticker}")

    # -------------------------------------------------------------------------
    # Markets
    # -------------------------------------------------------------------------

    def get_markets(
        self,
        event_ticker: Optional[str] = None,
        status: str = "open",
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get markets.

        Kalshi markets have:
        - ticker: Unique identifier
        - yes_bid/yes_ask: Current prices (in cents, 1-99)
        - volume: Total volume
        - open_interest: Current positions

        Returns:
            {
                "markets": [...],
                "cursor": "next_page_cursor"
            }
        """
        params = {
            "status": status,
            "limit": limit,
        }
        if event_ticker:
            params["event_ticker"] = event_ticker
        if cursor:
            params["cursor"] = cursor
        return self.get("/markets", params)

    def get_market(self, ticker: str) -> Dict[str, Any]:
        """Get single market by ticker"""
        return self.get(f"/markets/{ticker}")

    # -------------------------------------------------------------------------
    # Order Book
    # -------------------------------------------------------------------------

    def get_orderbook(self, ticker: str, depth: int = 10) -> Dict[str, Any]:
        """
        Get order book for a market.

        Returns:
            {
                "orderbook": {
                    "yes": [[price, size], ...],
                    "no": [[price, size], ...]
                }
            }
        """
        return self.get(f"/markets/{ticker}/orderbook", {"depth": depth})

    # -------------------------------------------------------------------------
    # Trades
    # -------------------------------------------------------------------------

    def get_trades(
        self,
        ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get recent trades"""
        params = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if cursor:
            params["cursor"] = cursor
        return self.get("/markets/trades", params)


class KalshiAdapter(PlatformAdapter):
    """
    Adapter that converts Kalshi data to unified format.
    """

    def __init__(self, client: Optional[KalshiClient] = None, api_key: Optional[str] = None):
        self.client = client or KalshiClient(api_key=api_key)
        self._stats_cache: Optional[MarketStats] = None
        self._thresholds_cache: Optional[DynamicThresholds] = None

    @property
    def platform(self) -> Platform:
        return Platform.KALSHI

    def _normalize_question(self, title: str) -> str:
        """Normalize question for cross-platform matching"""
        q = title.lower()
        q = re.sub(r'[^\w\s]', '', q)
        stopwords = {'will', 'the', 'a', 'an', 'be', 'in', 'on', 'at', 'to', 'for'}
        words = [w for w in q.split() if w not in stopwords]
        return ' '.join(words)

    def _convert_market(self, market: Dict[str, Any]) -> UnifiedMarket:
        """Convert Kalshi market to unified format"""

        # Kalshi prices are in cents (1-99), convert to 0-1
        yes_bid = market.get("yes_bid", 0) / 100.0 if market.get("yes_bid") else None
        yes_ask = market.get("yes_ask", 0) / 100.0 if market.get("yes_ask") else None
        last_price = market.get("last_price", 0) / 100.0 if market.get("last_price") else None

        # Build outcomes
        outcomes = [
            UnifiedOutcome(
                platform=Platform.KALSHI,
                platform_id=f"{market['ticker']}_yes",
                name="Yes",
                bid=yes_bid,
                ask=yes_ask,
                last=last_price,
            ),
            UnifiedOutcome(
                platform=Platform.KALSHI,
                platform_id=f"{market['ticker']}_no",
                name="No",
                bid=1 - yes_ask if yes_ask else None,  # No bid = 1 - Yes ask
                ask=1 - yes_bid if yes_bid else None,  # No ask = 1 - Yes bid
                last=1 - last_price if last_price else None,
            ),
        ]

        # Parse dates
        close_time = None
        if market.get("close_time"):
            try:
                close_time = datetime.fromisoformat(market["close_time"].replace("Z", "+00:00"))
            except:
                pass

        return UnifiedMarket(
            platform=Platform.KALSHI,
            platform_id=market["ticker"],
            platform_url=f"https://kalshi.com/markets/{market['ticker']}",
            question=market.get("title", ""),
            description=market.get("subtitle"),
            normalized_question=self._normalize_question(market.get("title", "")),
            event_slug=market.get("event_ticker"),
            market_type=MarketType.BINARY,
            outcomes=outcomes,
            is_active=market.get("status") == "open",
            is_resolved=market.get("status") == "settled",
            resolution="Yes" if market.get("result") == "yes" else "No" if market.get("result") == "no" else None,
            end_date=close_time,
            volume_24h=market.get("volume_24h"),
            volume_total=market.get("volume"),
            open_interest=market.get("open_interest"),
            raw_data=market,
        )

    def get_markets(
        self,
        active_only: bool = True,
        limit: int = 100
    ) -> List[UnifiedMarket]:
        """Fetch markets and convert to unified format"""
        status = "open" if active_only else None
        result = self.client.get_markets(status=status, limit=limit)

        unified = []
        for m in result.get("markets", []):
            try:
                unified.append(self._convert_market(m))
            except Exception as e:
                logger.warning(f"Failed to convert Kalshi market {m.get('ticker')}: {e}")

        return unified

    def get_market(self, market_id: str) -> Optional[UnifiedMarket]:
        """Fetch single market"""
        try:
            result = self.client.get_market(market_id)
            market = result.get("market")
            if market:
                return self._convert_market(market)
        except Exception as e:
            logger.error(f"Failed to get Kalshi market {market_id}: {e}")
        return None

    def get_orderbook(self, market_id: str, outcome_id: str) -> Dict[str, Any]:
        """Fetch order book for an outcome"""
        result = self.client.get_orderbook(market_id)
        book = result.get("orderbook", {})

        # Kalshi returns yes/no books, convert prices from cents
        yes_book = book.get("yes", [])
        no_book = book.get("no", [])

        # Determine which side based on outcome_id
        if "yes" in outcome_id.lower():
            bids = [(price / 100, size) for price, size in yes_book]
            asks = [(1 - price / 100, size) for price, size in no_book]  # Derive from no side
        else:
            bids = [(1 - price / 100, size) for price, size in no_book]
            asks = [(price / 100, size) for price, size in yes_book]

        return {
            "bids": bids,
            "asks": asks,
            "best_bid": bids[0][0] if bids else None,
            "best_ask": asks[0][0] if asks else None,
        }

    def compute_stats(self, sample_size: int = 500) -> MarketStats:
        """Compute platform statistics for dynamic thresholds"""
        logger.info(f"Computing Kalshi stats from {sample_size} markets...")

        try:
            result = self.client.get_markets(limit=sample_size)
            markets = result.get("markets", [])
        except Exception as e:
            logger.error(f"Failed to fetch Kalshi markets: {e}")
            return MarketStats(platform=Platform.KALSHI)

        import numpy as np

        volumes = []
        spreads = []
        trade_sizes = []

        for m in markets:
            if m.get("volume_24h"):
                volumes.append(m["volume_24h"])

            yes_bid = m.get("yes_bid", 0)
            yes_ask = m.get("yes_ask", 0)
            if yes_bid and yes_ask:
                spread_bps = (yes_ask - yes_bid) / ((yes_bid + yes_ask) / 2) * 10000
                spreads.append(spread_bps)

        # Get some trades for size distribution
        try:
            trades_result = self.client.get_trades(limit=500)
            for t in trades_result.get("trades", []):
                if t.get("count") and t.get("yes_price"):
                    # Kalshi trade size is count * price (in cents)
                    size = t["count"] * t["yes_price"] / 100
                    trade_sizes.append(size)
        except Exception as e:
            logger.warning(f"Failed to fetch Kalshi trades: {e}")

        def percentile(data, p):
            return float(np.percentile(data, p)) if data else 0.0

        def mean(data):
            return float(np.mean(data)) if data else 0.0

        def std(data):
            return float(np.std(data)) if data else 0.0

        def median(data):
            return float(np.median(data)) if data else 0.0

        stats = MarketStats(
            platform=Platform.KALSHI,
            sample_size=len(markets),

            volume_24h_mean=mean(volumes),
            volume_24h_median=median(volumes),
            volume_24h_std=std(volumes),
            volume_24h_p25=percentile(volumes, 25),
            volume_24h_p75=percentile(volumes, 75),
            volume_24h_p90=percentile(volumes, 90),

            spread_bps_mean=mean(spreads),
            spread_bps_median=median(spreads),
            spread_bps_std=std(spreads),
            spread_bps_p25=percentile(spreads, 25),
            spread_bps_p75=percentile(spreads, 75),
            spread_bps_p90=percentile(spreads, 90),

            trade_size_mean=mean(trade_sizes),
            trade_size_median=median(trade_sizes),
            trade_size_std=std(trade_sizes),
            trade_size_p25=percentile(trade_sizes, 25),
            trade_size_p75=percentile(trade_sizes, 75),
            trade_size_p90=percentile(trade_sizes, 90),
            trade_size_p99=percentile(trade_sizes, 99),
        )

        self._stats_cache = stats
        return stats

    def get_thresholds(self, sample_size: int = 500) -> DynamicThresholds:
        """Get dynamic thresholds based on current market data"""
        if self._thresholds_cache is None:
            stats = self.compute_stats(sample_size)
            self._thresholds_cache = DynamicThresholds.from_stats(stats)
        return self._thresholds_cache
