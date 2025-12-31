"""
Unified Prediction Market Models

Platform-agnostic models for prediction markets that both
Polymarket and Kalshi (and future platforms) can map to.

This enables:
- Cross-platform arbitrage detection
- Unified scanning and analysis
- Platform-agnostic strategies
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, computed_field
from abc import ABC, abstractmethod


class Platform(str, Enum):
    """Supported prediction market platforms"""
    POLYMARKET = "polymarket"
    KALSHI = "kalshi"
    # Future: predictit, metaculus, etc.


class MarketType(str, Enum):
    """Types of prediction markets"""
    BINARY = "binary"           # Yes/No
    CATEGORICAL = "categorical"  # Multiple outcomes
    SCALAR = "scalar"           # Numeric range


class UnifiedOutcome(BaseModel):
    """A single outcome in a prediction market"""
    platform: Platform
    platform_id: str  # Platform-specific token/contract ID

    name: str  # "Yes", "No", "Trump", "Biden", etc.

    # Current pricing
    bid: Optional[float] = None  # Best bid price (0-1)
    ask: Optional[float] = None  # Best ask price (0-1)
    last: Optional[float] = None  # Last trade price

    # Depth
    bid_size: Optional[float] = None  # $ at best bid
    ask_size: Optional[float] = None  # $ at best ask
    total_bid_depth: Optional[float] = None
    total_ask_depth: Optional[float] = None

    @computed_field
    @property
    def mid(self) -> Optional[float]:
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2
        return self.last

    @computed_field
    @property
    def spread(self) -> Optional[float]:
        if self.bid is not None and self.ask is not None:
            return self.ask - self.bid
        return None

    @computed_field
    @property
    def spread_bps(self) -> Optional[float]:
        if self.spread is not None and self.mid:
            return (self.spread / self.mid) * 10000
        return None


class UnifiedMarket(BaseModel):
    """
    Platform-agnostic representation of a prediction market.

    Both Polymarket and Kalshi markets map to this format,
    enabling cross-platform comparison and arbitrage detection.
    """
    # Platform info
    platform: Platform
    platform_id: str  # Original ID on the platform
    platform_url: Optional[str] = None

    # Market identity
    question: str  # The prediction question
    description: Optional[str] = None
    category: Optional[str] = None

    # For cross-platform matching
    normalized_question: Optional[str] = None  # Lowercase, stripped
    event_slug: Optional[str] = None  # Common event identifier

    # Market type
    market_type: MarketType = MarketType.BINARY

    # Outcomes
    outcomes: List[UnifiedOutcome] = Field(default_factory=list)

    # Status
    is_active: bool = True
    is_resolved: bool = False
    resolution: Optional[str] = None  # Winning outcome if resolved

    # Timing
    created_at: Optional[datetime] = None
    end_date: Optional[datetime] = None  # When market closes
    resolution_date: Optional[datetime] = None

    # Volume & Liquidity (in USD)
    volume_24h: Optional[float] = None
    volume_total: Optional[float] = None
    liquidity: Optional[float] = None  # Total book depth
    open_interest: Optional[float] = None  # For Kalshi

    # Fees
    maker_fee: Optional[float] = None
    taker_fee: Optional[float] = None

    # Raw platform data
    raw_data: Optional[Dict[str, Any]] = None

    @computed_field
    @property
    def yes_price(self) -> Optional[float]:
        """Price of Yes outcome (for binary markets)"""
        for o in self.outcomes:
            if o.name.lower() in ("yes", "true", "1"):
                return o.mid
        if len(self.outcomes) == 2:
            return self.outcomes[0].mid
        return None

    @computed_field
    @property
    def no_price(self) -> Optional[float]:
        """Price of No outcome (for binary markets)"""
        for o in self.outcomes:
            if o.name.lower() in ("no", "false", "0"):
                return o.mid
        if len(self.outcomes) == 2:
            return self.outcomes[1].mid
        return None

    @computed_field
    @property
    def best_spread_bps(self) -> Optional[float]:
        """Tightest spread among outcomes"""
        spreads = [o.spread_bps for o in self.outcomes if o.spread_bps is not None]
        return min(spreads) if spreads else None

    @computed_field
    @property
    def total_liquidity(self) -> float:
        """Total depth across all outcomes"""
        total = 0.0
        for o in self.outcomes:
            if o.total_bid_depth:
                total += o.total_bid_depth
            if o.total_ask_depth:
                total += o.total_ask_depth
        return total


class MarketPair(BaseModel):
    """
    A matched pair of markets across platforms.
    Same event, different platforms = arbitrage opportunity.
    """
    event_description: str  # What event this represents

    polymarket: Optional[UnifiedMarket] = None
    kalshi: Optional[UnifiedMarket] = None

    match_confidence: float = 0.0  # How confident we are these are the same event
    matched_at: datetime = Field(default_factory=datetime.utcnow)

    @computed_field
    @property
    def has_both(self) -> bool:
        return self.polymarket is not None and self.kalshi is not None

    @computed_field
    @property
    def price_diff(self) -> Optional[float]:
        """Price difference for Yes outcome (Poly - Kalshi)"""
        if not self.has_both:
            return None
        poly_yes = self.polymarket.yes_price
        kalshi_yes = self.kalshi.yes_price
        if poly_yes is not None and kalshi_yes is not None:
            return poly_yes - kalshi_yes
        return None

    @computed_field
    @property
    def price_diff_bps(self) -> Optional[float]:
        """Price difference in basis points"""
        if self.price_diff is not None:
            avg = ((self.polymarket.yes_price or 0) + (self.kalshi.yes_price or 0)) / 2
            if avg > 0:
                return (self.price_diff / avg) * 10000
        return None

    @computed_field
    @property
    def arb_opportunity(self) -> Optional[Dict[str, Any]]:
        """
        Detect arbitrage opportunity.
        Returns None if no arb, otherwise details.
        """
        if not self.has_both:
            return None

        poly_yes = self.polymarket.yes_price
        kalshi_yes = self.kalshi.yes_price

        if poly_yes is None or kalshi_yes is None:
            return None

        # Check for Yes/No arb
        # If Poly Yes < Kalshi No (i.e., 1 - Kalshi Yes), there's an arb
        kalshi_no = 1 - kalshi_yes if kalshi_yes else None
        poly_no = 1 - poly_yes if poly_yes else None

        # Arb exists if you can buy Yes on one and No on other for < $1
        if kalshi_no is not None and poly_yes is not None:
            # Buy Yes on Poly, buy No on Kalshi
            cost = poly_yes + kalshi_no
            if cost < 1.0:
                profit = 1.0 - cost
                return {
                    "type": "yes_poly_no_kalshi",
                    "buy_yes_on": "polymarket",
                    "buy_no_on": "kalshi",
                    "poly_yes_price": poly_yes,
                    "kalshi_no_price": kalshi_no,
                    "total_cost": cost,
                    "guaranteed_profit": profit,
                    "profit_pct": profit / cost * 100,
                }

        if poly_no is not None and kalshi_yes is not None:
            # Buy Yes on Kalshi, buy No on Poly
            cost = kalshi_yes + poly_no
            if cost < 1.0:
                profit = 1.0 - cost
                return {
                    "type": "yes_kalshi_no_poly",
                    "buy_yes_on": "kalshi",
                    "buy_no_on": "polymarket",
                    "kalshi_yes_price": kalshi_yes,
                    "poly_no_price": poly_no,
                    "total_cost": cost,
                    "guaranteed_profit": profit,
                    "profit_pct": profit / cost * 100,
                }

        return None


# ============================================================================
# Dynamic Thresholds (computed from actual data)
# ============================================================================

class MarketStats(BaseModel):
    """
    Statistics computed from actual market data.
    Used to derive dynamic thresholds.
    """
    platform: Platform
    computed_at: datetime = Field(default_factory=datetime.utcnow)
    sample_size: int = 0

    # Volume distribution
    volume_24h_mean: float = 0.0
    volume_24h_median: float = 0.0
    volume_24h_std: float = 0.0
    volume_24h_p25: float = 0.0  # 25th percentile
    volume_24h_p75: float = 0.0  # 75th percentile
    volume_24h_p90: float = 0.0  # 90th percentile

    # Liquidity distribution
    liquidity_mean: float = 0.0
    liquidity_median: float = 0.0
    liquidity_std: float = 0.0
    liquidity_p25: float = 0.0
    liquidity_p75: float = 0.0
    liquidity_p90: float = 0.0

    # Spread distribution
    spread_bps_mean: float = 0.0
    spread_bps_median: float = 0.0
    spread_bps_std: float = 0.0
    spread_bps_p25: float = 0.0
    spread_bps_p75: float = 0.0
    spread_bps_p90: float = 0.0

    # Trade size distribution
    trade_size_mean: float = 0.0
    trade_size_median: float = 0.0
    trade_size_std: float = 0.0
    trade_size_p25: float = 0.0
    trade_size_p75: float = 0.0
    trade_size_p90: float = 0.0
    trade_size_p99: float = 0.0  # Whale threshold

    def is_illiquid(self, market: UnifiedMarket) -> bool:
        """Is this market illiquid relative to platform average?"""
        # Below median liquidity AND volume
        vol = market.volume_24h or 0
        liq = market.liquidity or 0
        return vol < self.volume_24h_median and liq < self.liquidity_median

    def is_large_order(self, notional: float) -> bool:
        """Is this order large relative to platform average?"""
        return notional >= self.trade_size_p90

    def is_whale_order(self, notional: float) -> bool:
        """Is this a whale-sized order?"""
        return notional >= self.trade_size_p99

    def volume_zscore(self, volume: float) -> float:
        """How many std devs is this volume from mean?"""
        if self.volume_24h_std > 0:
            return (volume - self.volume_24h_mean) / self.volume_24h_std
        return 0.0

    def is_unusual_volume(self, volume: float, threshold_std: float = 2.0) -> bool:
        """Is this volume unusually high?"""
        return self.volume_zscore(volume) >= threshold_std


class DynamicThresholds(BaseModel):
    """
    Thresholds derived from actual market data.
    Updated periodically from MarketStats.
    """
    platform: Platform
    computed_at: datetime = Field(default_factory=datetime.utcnow)

    # What counts as "illiquid" (opportunity territory)
    illiquid_volume_24h: float = 0.0      # Below this = illiquid
    illiquid_liquidity: float = 0.0       # Below this = illiquid
    illiquid_spread_bps: float = 0.0      # Above this = illiquid

    # What counts as "large" (signal territory)
    large_order_notional: float = 0.0     # Above this = noteworthy
    whale_order_notional: float = 0.0     # Above this = whale
    large_trade_notional: float = 0.0     # Above this = significant trade

    # What counts as "unusual" (something happening)
    unusual_volume_threshold: float = 0.0  # Above this = unusual activity
    unusual_order_count: int = 0           # Above this = unusual flow

    @classmethod
    def from_stats(cls, stats: MarketStats) -> "DynamicThresholds":
        """Derive thresholds from computed statistics"""
        return cls(
            platform=stats.platform,
            computed_at=datetime.utcnow(),

            # Illiquid = bottom quartile
            illiquid_volume_24h=stats.volume_24h_p25,
            illiquid_liquidity=stats.liquidity_p25,
            illiquid_spread_bps=stats.spread_bps_p75,  # Wide spreads

            # Large = top 10%
            large_order_notional=stats.trade_size_p90,
            whale_order_notional=stats.trade_size_p99,
            large_trade_notional=stats.trade_size_p90,

            # Unusual = 2 std devs above mean
            unusual_volume_threshold=stats.volume_24h_mean + 2 * stats.volume_24h_std,
        )


# ============================================================================
# Platform Adapter Interface
# ============================================================================

class PlatformAdapter(ABC):
    """
    Abstract interface for prediction market platforms.
    Implement this for each platform (Polymarket, Kalshi, etc.)
    """

    @property
    @abstractmethod
    def platform(self) -> Platform:
        """Which platform this adapter is for"""
        pass

    @abstractmethod
    def get_markets(self, active_only: bool = True, limit: int = 100) -> List[UnifiedMarket]:
        """Fetch markets and convert to unified format"""
        pass

    @abstractmethod
    def get_market(self, market_id: str) -> Optional[UnifiedMarket]:
        """Fetch single market"""
        pass

    @abstractmethod
    def get_orderbook(self, market_id: str, outcome_id: str) -> Dict[str, Any]:
        """Fetch order book for an outcome"""
        pass

    @abstractmethod
    def compute_stats(self, sample_size: int = 500) -> MarketStats:
        """Compute platform statistics for dynamic thresholds"""
        pass

    def get_thresholds(self, sample_size: int = 500) -> DynamicThresholds:
        """Get dynamic thresholds based on current market data"""
        stats = self.compute_stats(sample_size)
        return DynamicThresholds.from_stats(stats)


# Import adapters and cross-platform components
from .polymarket_adapter import PolymarketAdapter
from .kalshi_adapter import KalshiAdapter, KalshiClient
from .cross_platform import CrossPlatformScanner, ArbitrageOpportunity, format_arb_opportunity

__all__ = [
    # Enums
    "Platform",
    "MarketType",

    # Core Models
    "UnifiedOutcome",
    "UnifiedMarket",
    "MarketPair",

    # Statistics & Thresholds
    "MarketStats",
    "DynamicThresholds",

    # Abstract Interface
    "PlatformAdapter",

    # Platform Adapters
    "PolymarketAdapter",
    "KalshiAdapter",
    "KalshiClient",

    # Cross-Platform
    "CrossPlatformScanner",
    "ArbitrageOpportunity",
    "format_arb_opportunity",
]
