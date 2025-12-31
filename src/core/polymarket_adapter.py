"""
Polymarket Adapter

Maps Polymarket API data to unified prediction market models.
"""

import re
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
import numpy as np

from . import (
    Platform, MarketType, PlatformAdapter,
    UnifiedMarket, UnifiedOutcome, MarketStats, DynamicThresholds
)
from ..api import PolymarketClient, GammaMarket, MarketStatus

logger = logging.getLogger(__name__)


class PolymarketAdapter(PlatformAdapter):
    """
    Adapter that converts Polymarket data to unified format.
    """

    def __init__(self, client: Optional[PolymarketClient] = None):
        self.client = client or PolymarketClient()
        self._stats_cache: Optional[MarketStats] = None
        self._thresholds_cache: Optional[DynamicThresholds] = None

    @property
    def platform(self) -> Platform:
        return Platform.POLYMARKET

    def _normalize_question(self, question: str) -> str:
        """Normalize question for cross-platform matching"""
        # Lowercase
        q = question.lower()
        # Remove punctuation
        q = re.sub(r'[^\w\s]', '', q)
        # Remove common filler words
        stopwords = {'will', 'the', 'a', 'an', 'be', 'in', 'on', 'at', 'to', 'for'}
        words = [w for w in q.split() if w not in stopwords]
        return ' '.join(words)

    def _convert_market(self, market: GammaMarket) -> UnifiedMarket:
        """Convert Polymarket market to unified format"""
        outcomes = []

        for token in market.tokens:
            token_id = token.get("token_id")
            outcome_name = token.get("outcome", "Unknown")

            # Get order book for this token
            book_data = {}
            try:
                book = self.client.clob.get_order_book(token_id)
                book_data = {
                    "bid": book.best_bid,
                    "ask": book.best_ask,
                    "bid_size": book.bids[0].size * book.bids[0].price if book.bids else None,
                    "ask_size": book.asks[0].size * book.asks[0].price if book.asks else None,
                    "total_bid_depth": book.total_bid_value,
                    "total_ask_depth": book.total_ask_value,
                }
            except Exception as e:
                logger.debug(f"Failed to get book for {token_id}: {e}")

            # Get last trade price
            last_price = None
            try:
                last_price = self.client.clob.get_last_trade_price(token_id)
            except:
                pass

            outcome = UnifiedOutcome(
                platform=Platform.POLYMARKET,
                platform_id=token_id or "",
                name=outcome_name,
                bid=book_data.get("bid"),
                ask=book_data.get("ask"),
                last=last_price,
                bid_size=book_data.get("bid_size"),
                ask_size=book_data.get("ask_size"),
                total_bid_depth=book_data.get("total_bid_depth"),
                total_ask_depth=book_data.get("total_ask_depth"),
            )
            outcomes.append(outcome)

        # Determine market type
        market_type = MarketType.BINARY
        if len(outcomes) > 2:
            market_type = MarketType.CATEGORICAL

        return UnifiedMarket(
            platform=Platform.POLYMARKET,
            platform_id=market.id,
            platform_url=f"https://polymarket.com/event/{market.slug}" if market.slug else None,
            question=market.question,
            description=market.description,
            normalized_question=self._normalize_question(market.question),
            event_slug=market.event_slug,
            market_type=market_type,
            outcomes=outcomes,
            is_active=market.active and not market.closed,
            is_resolved=market.closed and any(t.get("winner") for t in market.tokens),
            resolution=next((t.get("outcome") for t in market.tokens if t.get("winner")), None),
            created_at=market.created_at,
            end_date=market.end_date,
            volume_24h=market.volume_24hr,
            volume_total=market.volume,
            liquidity=market.liquidity,
            raw_data=market.model_dump(),
        )

    def get_markets(
        self,
        active_only: bool = True,
        limit: int = 100
    ) -> List[UnifiedMarket]:
        """Fetch markets and convert to unified format"""
        from ..api import MarketFilter

        filter = MarketFilter(
            status=MarketStatus.ACTIVE if active_only else None,
            limit=limit,
        )
        markets = self.client.get_markets(filter)

        unified = []
        for m in markets:
            try:
                unified.append(self._convert_market(m))
            except Exception as e:
                logger.warning(f"Failed to convert market {m.id}: {e}")

        return unified

    def get_market(self, market_id: str) -> Optional[UnifiedMarket]:
        """Fetch single market"""
        market = self.client.get_market(market_id)
        if market:
            return self._convert_market(market)
        return None

    def get_orderbook(self, market_id: str, outcome_id: str) -> Dict[str, Any]:
        """Fetch order book for an outcome"""
        book = self.client.clob.get_order_book(outcome_id)
        return {
            "bids": [(b.price, b.size) for b in book.bids],
            "asks": [(a.price, a.size) for a in book.asks],
            "best_bid": book.best_bid,
            "best_ask": book.best_ask,
            "spread": book.spread,
            "spread_bps": book.spread_pct * 100 if book.spread_pct else None,
        }

    def compute_stats(self, sample_size: int = 500) -> MarketStats:
        """
        Compute platform statistics for dynamic thresholds.
        Samples markets and trades to build distributions.
        """
        logger.info(f"Computing Polymarket stats from {sample_size} markets...")

        # Fetch markets
        from ..api import MarketFilter
        markets = self.client.get_markets(MarketFilter(
            status=MarketStatus.ACTIVE,
            limit=sample_size,
            order_by="volume24hr",
            ascending=False,
        ))

        if not markets:
            logger.warning("No markets found for stats computation")
            return MarketStats(platform=Platform.POLYMARKET)

        # Collect data
        volumes = []
        liquidities = []
        spreads = []
        trade_sizes = []

        for market in markets:
            if market.volume_24hr:
                volumes.append(market.volume_24hr)
            if market.liquidity:
                liquidities.append(market.liquidity)

            # Get spread and trade data for each token
            for token in market.tokens[:2]:  # Just first 2 outcomes
                token_id = token.get("token_id")
                if not token_id:
                    continue

                try:
                    # Get spread
                    book = self.client.clob.get_order_book(token_id)
                    if book.spread_pct:
                        spreads.append(book.spread_pct * 100)  # Convert to bps

                    # Get trade sizes
                    trades = self.client.clob.get_trades(token_id=token_id, limit=50)
                    for t in trades.trades:
                        trade_sizes.append(t.price * t.size)

                except Exception as e:
                    logger.debug(f"Error getting data for {token_id}: {e}")

        # Compute statistics
        def percentile(data, p):
            if not data:
                return 0.0
            return float(np.percentile(data, p))

        def mean(data):
            return float(np.mean(data)) if data else 0.0

        def std(data):
            return float(np.std(data)) if data else 0.0

        def median(data):
            return float(np.median(data)) if data else 0.0

        stats = MarketStats(
            platform=Platform.POLYMARKET,
            sample_size=len(markets),

            volume_24h_mean=mean(volumes),
            volume_24h_median=median(volumes),
            volume_24h_std=std(volumes),
            volume_24h_p25=percentile(volumes, 25),
            volume_24h_p75=percentile(volumes, 75),
            volume_24h_p90=percentile(volumes, 90),

            liquidity_mean=mean(liquidities),
            liquidity_median=median(liquidities),
            liquidity_std=std(liquidities),
            liquidity_p25=percentile(liquidities, 25),
            liquidity_p75=percentile(liquidities, 75),
            liquidity_p90=percentile(liquidities, 90),

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

        logger.info(f"Stats computed: median volume=${stats.volume_24h_median:,.0f}, "
                    f"whale threshold=${stats.trade_size_p99:,.0f}")

        self._stats_cache = stats
        return stats

    def get_thresholds(self, sample_size: int = 500) -> DynamicThresholds:
        """Get dynamic thresholds based on current market data"""
        if self._thresholds_cache is None:
            stats = self.compute_stats(sample_size)
            self._thresholds_cache = DynamicThresholds.from_stats(stats)
        return self._thresholds_cache

    def refresh_thresholds(self, sample_size: int = 500) -> DynamicThresholds:
        """Force refresh of thresholds"""
        self._stats_cache = None
        self._thresholds_cache = None
        return self.get_thresholds(sample_size)
