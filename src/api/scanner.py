"""
Illiquid Market Scanner

Scans Polymarket for opportunities in illiquid markets:
- Detects unusual order flow (large orders appearing)
- Spots whale activity before price moves
- Identifies book imbalance shifts
- Finds spread compression (liquidity arriving)

The thesis: In illiquid markets, large informed orders are visible
before they move price. Get there first.

IMPORTANT: Thresholds are computed dynamically from actual market data,
not hardcoded. This ensures the scanner adapts to changing market conditions.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

from .client import PolymarketClient
from .models import (
    GammaMarket, MarketFilter, MarketStatus,
    OrderBookSnapshot, LargeOrder, WhaleTrade,
    IlliquidMarketSignal
)

# Import dynamic thresholds - these are computed from actual market data
try:
    from ..core import MarketStats, DynamicThresholds, Platform
    HAS_CORE = True
except ImportError:
    HAS_CORE = False

logger = logging.getLogger(__name__)


@dataclass
class MarketOpportunity:
    """A detected opportunity in an illiquid market"""
    market: GammaMarket
    token_id: str
    outcome: str
    detected_at: datetime = field(default_factory=datetime.utcnow)

    # Why this is interesting
    signal_type: str = ""  # "large_order", "whale_flow", "imbalance", "spread_compression"
    description: str = ""
    urgency: str = "low"  # "low", "medium", "high"

    # Market state
    mid_price: Optional[float] = None
    spread_bps: Optional[float] = None
    book_imbalance: Optional[float] = None  # [-1, 1]

    # The signal data
    large_orders: List[LargeOrder] = field(default_factory=list)
    whale_trades: List[WhaleTrade] = field(default_factory=list)
    total_unusual_volume: float = 0.0

    # Context
    daily_volume: Optional[float] = None
    total_liquidity: Optional[float] = None

    @property
    def signal_strength(self) -> float:
        """0-1 score of how strong this signal is"""
        score = 0.0

        # Large orders on book
        if self.large_orders:
            largest = max(o.notional for o in self.large_orders)
            if largest >= 10000:
                score += 0.4
            elif largest >= 5000:
                score += 0.3
            elif largest >= 2000:
                score += 0.2

        # Book imbalance
        if self.book_imbalance is not None:
            imb = abs(self.book_imbalance)
            if imb >= 0.7:
                score += 0.3
            elif imb >= 0.5:
                score += 0.2

        # Whale trades
        if self.whale_trades:
            whale_vol = sum(t.notional for t in self.whale_trades)
            if whale_vol >= 20000:
                score += 0.3
            elif whale_vol >= 10000:
                score += 0.2
            elif whale_vol >= 5000:
                score += 0.1

        return min(1.0, score)

    @property
    def suggested_side(self) -> Optional[str]:
        """Which side the signal suggests"""
        if self.book_imbalance is not None and abs(self.book_imbalance) >= 0.3:
            return "buy" if self.book_imbalance > 0 else "sell"

        if self.large_orders:
            bid_vol = sum(o.notional for o in self.large_orders if o.side == "bid")
            ask_vol = sum(o.notional for o in self.large_orders if o.side == "ask")
            if bid_vol > ask_vol * 1.5:
                return "buy"
            elif ask_vol > bid_vol * 1.5:
                return "sell"

        if self.whale_trades:
            buy_vol = sum(t.notional for t in self.whale_trades if t.side.upper() == "BUY")
            sell_vol = sum(t.notional for t in self.whale_trades if t.side.upper() == "SELL")
            if buy_vol > sell_vol * 1.5:
                return "buy"
            elif sell_vol > buy_vol * 1.5:
                return "sell"

        return None


class MarketScanner:
    """
    Scans for opportunities in illiquid Polymarket markets.

    Workflow:
    1. Compute dynamic thresholds from actual market data
    2. Find illiquid markets (bottom quartile by volume/liquidity)
    3. Check each for unusual activity (top percentile orders)
    4. Rank opportunities by signal strength
    5. Return actionable list

    Usage:
        scanner = MarketScanner(client)

        # Thresholds computed automatically from market data
        opportunities = scanner.scan()

        for opp in opportunities:
            print(f"{opp.market.question}")
            print(f"  Signal: {opp.signal_type} - {opp.description}")
            print(f"  Strength: {opp.signal_strength:.0%}")
            print(f"  Suggested: {opp.suggested_side}")
    """

    def __init__(
        self,
        client: PolymarketClient,
        # Optional manual overrides (if None, computed from data)
        max_daily_volume: Optional[float] = None,
        max_liquidity: Optional[float] = None,
        min_spread_bps: Optional[float] = None,
        large_order_threshold: Optional[float] = None,
        whale_trade_threshold: Optional[float] = None,
        imbalance_threshold: float = 0.4,  # Book imbalance is inherently normalized

        # Stats computation config
        stats_sample_size: int = 300,
    ):
        self.client = client
        self.imbalance_threshold = imbalance_threshold
        self.stats_sample_size = stats_sample_size

        # Store manual overrides (None means compute from data)
        self._manual_max_daily_volume = max_daily_volume
        self._manual_max_liquidity = max_liquidity
        self._manual_min_spread_bps = min_spread_bps
        self._manual_large_order_threshold = large_order_threshold
        self._manual_whale_trade_threshold = whale_trade_threshold

        # Cached computed values
        self._stats: Optional[Any] = None
        self._thresholds: Optional[Any] = None

    def compute_thresholds(self, force_refresh: bool = False) -> Dict[str, float]:
        """
        Compute thresholds from actual market data.

        Uses statistical distributions to determine:
        - Illiquid: bottom 25th percentile of volume/liquidity
        - Large orders: top 10th percentile of trade sizes
        - Whale orders: top 1st percentile (99th percentile)

        Returns dict with all computed thresholds.
        """
        if self._thresholds is not None and not force_refresh:
            return self._thresholds

        logger.info(f"Computing thresholds from {self.stats_sample_size} markets...")

        # Sample markets to build distributions
        import numpy as np

        markets = self.client.get_markets(MarketFilter(
            status=MarketStatus.ACTIVE,
            limit=self.stats_sample_size,
            order_by="volume24hr",
            ascending=False,  # Get most active first for representative sample
        ))

        if not markets:
            logger.warning("No markets found, using fallback thresholds")
            self._thresholds = self._fallback_thresholds()
            return self._thresholds

        # Collect distributions
        volumes = []
        liquidities = []
        spreads = []
        trade_sizes = []

        for market in markets:
            if market.volume_24hr:
                volumes.append(market.volume_24hr)
            if market.liquidity:
                liquidities.append(market.liquidity)

            # Sample order books for spread and trade data
            for token in market.tokens[:2]:
                token_id = token.get("token_id")
                if not token_id:
                    continue

                try:
                    book = self.client.clob.get_order_book(token_id)
                    if book.spread_pct:
                        spreads.append(book.spread_pct * 10000)  # Convert to bps

                    # Get trade sizes
                    trades = self.client.clob.get_trades(token_id=token_id, limit=30)
                    for t in trades.trades:
                        trade_sizes.append(t.price * t.size)
                except Exception:
                    pass

        def percentile(data, p):
            if not data:
                return 0.0
            return float(np.percentile(data, p))

        # Compute thresholds from distributions
        computed = {
            # Illiquid = bottom quartile
            "max_daily_volume": self._manual_max_daily_volume or percentile(volumes, 25),
            "max_liquidity": self._manual_max_liquidity or percentile(liquidities, 25),
            # Wide spread = top quartile (75th percentile of spreads)
            "min_spread_bps": self._manual_min_spread_bps or percentile(spreads, 75),
            # Large order = top 10%
            "large_order_threshold": self._manual_large_order_threshold or percentile(trade_sizes, 90),
            # Whale = top 1%
            "whale_trade_threshold": self._manual_whale_trade_threshold or percentile(trade_sizes, 99),
            # Stats for reference
            "volume_median": percentile(volumes, 50),
            "liquidity_median": percentile(liquidities, 50),
            "spread_median": percentile(spreads, 50),
            "trade_size_median": percentile(trade_sizes, 50),
            "sample_size": len(markets),
        }

        logger.info(
            f"Computed thresholds: illiquid_vol=${computed['max_daily_volume']:,.0f}, "
            f"large_order=${computed['large_order_threshold']:,.0f}, "
            f"whale=${computed['whale_trade_threshold']:,.0f}"
        )

        self._thresholds = computed
        return computed

    def _fallback_thresholds(self) -> Dict[str, float]:
        """Fallback thresholds if we can't compute from data"""
        return {
            "max_daily_volume": 50000,
            "max_liquidity": 100000,
            "min_spread_bps": 100,
            "large_order_threshold": 1000,
            "whale_trade_threshold": 5000,
            "volume_median": 100000,
            "liquidity_median": 200000,
            "spread_median": 50,
            "trade_size_median": 100,
            "sample_size": 0,
        }

    @property
    def max_daily_volume(self) -> float:
        return self.compute_thresholds()["max_daily_volume"]

    @property
    def max_liquidity(self) -> float:
        return self.compute_thresholds()["max_liquidity"]

    @property
    def min_spread_bps(self) -> float:
        return self.compute_thresholds()["min_spread_bps"]

    @property
    def large_order_threshold(self) -> float:
        return self.compute_thresholds()["large_order_threshold"]

    @property
    def whale_trade_threshold(self) -> float:
        return self.compute_thresholds()["whale_trade_threshold"]

    def find_illiquid_markets(self, limit: int = 100) -> List[GammaMarket]:
        """Find active markets that are illiquid enough to have inefficiencies"""
        # Get active markets sorted by volume (ascending = least volume first)
        markets = self.client.get_markets(MarketFilter(
            status=MarketStatus.ACTIVE,
            limit=limit,
            order_by="volume24hr",
            ascending=True,  # Least volume first
        ))

        illiquid = []
        for m in markets:
            vol = m.volume_24hr or 0
            liq = m.liquidity or 0

            # Skip if too liquid (efficient markets)
            if vol > self.max_daily_volume:
                continue
            if liq > self.max_liquidity:
                continue

            # Skip if no tokens
            if not m.tokens:
                continue

            illiquid.append(m)

        logger.info(f"Found {len(illiquid)} illiquid markets out of {len(markets)}")
        return illiquid

    def analyze_market(self, market: GammaMarket) -> List[MarketOpportunity]:
        """Analyze a single market for opportunities"""
        opportunities = []

        for token in market.tokens:
            token_id = token.get("token_id")
            outcome = token.get("outcome", "Unknown")
            if not token_id:
                continue

            try:
                opp = self._analyze_token(market, token_id, outcome)
                if opp and opp.signal_strength > 0.2:  # Only interesting signals
                    opportunities.append(opp)
            except Exception as e:
                logger.warning(f"Error analyzing {token_id}: {e}")

        return opportunities

    def _analyze_token(
        self,
        market: GammaMarket,
        token_id: str,
        outcome: str
    ) -> Optional[MarketOpportunity]:
        """Analyze a single token for signals"""

        # Get order book
        try:
            snapshot = self.client.get_raw_order_book(token_id)
        except Exception as e:
            logger.debug(f"Failed to get book for {token_id}: {e}")
            return None

        # Skip if no real book
        if not snapshot.bids or not snapshot.asks:
            return None

        # Check spread - skip if too tight (efficient)
        if snapshot.spread_bps and snapshot.spread_bps < self.min_spread_bps:
            return None

        opp = MarketOpportunity(
            market=market,
            token_id=token_id,
            outcome=outcome,
            mid_price=snapshot.mid_price,
            spread_bps=snapshot.spread_bps,
            book_imbalance=snapshot.imbalance,
            total_liquidity=snapshot.total_bid_notional + snapshot.total_ask_notional,
            daily_volume=market.volume_24hr,
        )

        signals = []

        # 1. Check for large orders on the book
        large_orders = self.client.get_large_orders(
            token_id,
            min_notional=self.large_order_threshold
        )
        if large_orders:
            opp.large_orders = large_orders
            total = sum(o.notional for o in large_orders)
            opp.total_unusual_volume += total
            signals.append(f"${total:,.0f} in large orders on book")

        # 2. Check for whale trades
        whale_trades = self.client.get_whale_trades(
            token_id=token_id,
            min_notional=self.whale_trade_threshold,
            limit=20
        )
        if whale_trades:
            opp.whale_trades = whale_trades
            total = sum(t.notional for t in whale_trades)
            opp.total_unusual_volume += total
            signals.append(f"${total:,.0f} in recent whale trades")

        # 3. Check book imbalance
        if snapshot.imbalance and abs(snapshot.imbalance) >= self.imbalance_threshold:
            direction = "bid-heavy" if snapshot.imbalance > 0 else "ask-heavy"
            signals.append(f"Book {direction} ({snapshot.imbalance:.0%} imbalance)")

        # Build description
        if signals:
            opp.description = "; ".join(signals)

            # Determine primary signal type
            if opp.large_orders and sum(o.notional for o in opp.large_orders) >= 5000:
                opp.signal_type = "large_order"
                opp.urgency = "high"
            elif opp.whale_trades and sum(t.notional for t in opp.whale_trades) >= 10000:
                opp.signal_type = "whale_flow"
                opp.urgency = "high"
            elif snapshot.imbalance and abs(snapshot.imbalance) >= 0.6:
                opp.signal_type = "imbalance"
                opp.urgency = "medium"
            else:
                opp.signal_type = "mixed"
                opp.urgency = "low"

            return opp

        return None

    def scan(
        self,
        market_limit: int = 50,
        min_signal_strength: float = 0.3,
    ) -> List[MarketOpportunity]:
        """
        Full scan: find illiquid markets, analyze each, return ranked opportunities.

        Args:
            market_limit: Max markets to scan
            min_signal_strength: Minimum signal strength to include

        Returns:
            List of opportunities sorted by signal strength (best first)
        """
        logger.info("Starting market scan...")

        # Find illiquid markets
        markets = self.find_illiquid_markets(limit=market_limit * 2)

        # Analyze each
        all_opportunities = []
        for i, market in enumerate(markets[:market_limit]):
            if i % 10 == 0:
                logger.info(f"Scanning market {i+1}/{min(len(markets), market_limit)}...")

            opps = self.analyze_market(market)
            all_opportunities.extend(opps)

        # Filter and sort
        filtered = [o for o in all_opportunities if o.signal_strength >= min_signal_strength]
        filtered.sort(key=lambda x: x.signal_strength, reverse=True)

        logger.info(f"Found {len(filtered)} opportunities above {min_signal_strength:.0%} threshold")
        return filtered

    def scan_specific_markets(
        self,
        market_ids: List[str],
        min_signal_strength: float = 0.2,
    ) -> List[MarketOpportunity]:
        """Scan specific markets (for watchlist monitoring)"""
        opportunities = []

        for market_id in market_ids:
            market = self.client.get_market(market_id)
            if not market:
                continue

            opps = self.analyze_market(market)
            opportunities.extend(opps)

        filtered = [o for o in opportunities if o.signal_strength >= min_signal_strength]
        filtered.sort(key=lambda x: x.signal_strength, reverse=True)
        return filtered

    def quick_scan(self, limit: int = 20) -> List[MarketOpportunity]:
        """
        Quick scan of the most illiquid active markets.
        Faster, fewer markets, higher thresholds.
        """
        return self.scan(
            market_limit=limit,
            min_signal_strength=0.4,
        )


def format_opportunity(opp: MarketOpportunity) -> str:
    """Format an opportunity for display"""
    lines = [
        f"{'='*60}",
        f"OPPORTUNITY: {opp.outcome}",
        f"Market: {opp.market.question[:60]}...",
        f"{'='*60}",
        f"",
        f"Signal Type: {opp.signal_type.upper()}",
        f"Signal Strength: {opp.signal_strength:.0%}",
        f"Urgency: {opp.urgency.upper()}",
        f"Suggested Side: {opp.suggested_side or 'unclear'}",
        f"",
        f"Market State:",
        f"  Mid Price: {opp.mid_price:.4f}" if opp.mid_price else "  Mid Price: N/A",
        f"  Spread: {opp.spread_bps:.0f} bps" if opp.spread_bps else "  Spread: N/A",
        f"  Book Imbalance: {opp.book_imbalance:.0%}" if opp.book_imbalance else "  Book Imbalance: N/A",
        f"  Total Liquidity: ${opp.total_liquidity:,.0f}" if opp.total_liquidity else "  Total Liquidity: N/A",
        f"  24h Volume: ${opp.daily_volume:,.0f}" if opp.daily_volume else "  24h Volume: N/A",
        f"",
        f"Signals: {opp.description}",
    ]

    if opp.large_orders:
        lines.append(f"")
        lines.append(f"Large Orders on Book:")
        for o in opp.large_orders[:5]:
            lines.append(f"  {o.side.upper()} ${o.notional:,.0f} @ {o.price:.4f} ({o.pct_of_book:.1f}% of book)")

    if opp.whale_trades:
        lines.append(f"")
        lines.append(f"Recent Whale Trades:")
        for t in opp.whale_trades[:5]:
            time_str = t.timestamp.strftime("%H:%M:%S")
            lines.append(f"  {t.side.upper()} ${t.notional:,.0f} @ {t.price:.4f} [{time_str}]")

    return "\n".join(lines)
