"""Pydantic models for Polymarket API data structures"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple
from pydantic import BaseModel, Field, computed_field
import hashlib


class MarketStatus(str, Enum):
    """Market lifecycle status"""
    ACTIVE = "active"
    CLOSED = "closed"
    RESOLVED = "resolved"
    ARCHIVED = "archived"


class OrderSide(str, Enum):
    """Order side"""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Order types supported by CLOB"""
    GTC = "GTC"  # Good til cancelled
    GTD = "GTD"  # Good til date
    FOK = "FOK"  # Fill or kill
    IOC = "IOC"  # Immediate or cancel


class OutcomeType(str, Enum):
    """Types of market outcomes"""
    BINARY = "binary"
    CATEGORICAL = "categorical"
    SCALAR = "scalar"


# ============================================================================
# Token & Market Core Models
# ============================================================================

class Token(BaseModel):
    """Represents a tradeable outcome token"""
    token_id: str
    outcome: str
    price: Optional[float] = None
    winner: Optional[bool] = None


class CLOBToken(BaseModel):
    """Token info from CLOB API"""
    token_id: str
    outcome: str
    price: float = 0.0


class GammaMarket(BaseModel):
    """Market data from Gamma API (metadata/events)"""
    id: str
    question: str
    description: Optional[str] = None
    slug: Optional[str] = None

    active: bool = True
    closed: bool = False
    archived: bool = False

    market_type: Optional[str] = None
    outcomes: List[str] = Field(default_factory=list)
    outcome_prices: Optional[str] = None  # Comma-separated prices

    volume: Optional[float] = None
    volume_24hr: Optional[float] = Field(None, alias="volume24hr")
    liquidity: Optional[float] = None

    start_date: Optional[datetime] = Field(None, alias="startDate")
    end_date: Optional[datetime] = Field(None, alias="endDate")
    created_at: Optional[datetime] = Field(None, alias="createdAt")

    condition_id: Optional[str] = Field(None, alias="conditionId")
    question_id: Optional[str] = Field(None, alias="questionId")

    tokens: List[Dict[str, Any]] = Field(default_factory=list)

    # Event grouping
    event_slug: Optional[str] = Field(None, alias="eventSlug")
    group_slug: Optional[str] = Field(None, alias="groupSlug")

    class Config:
        populate_by_name = True

    @computed_field
    @property
    def status(self) -> MarketStatus:
        """Derive market status from flags"""
        if self.archived:
            return MarketStatus.ARCHIVED
        if self.closed:
            return MarketStatus.RESOLVED if any(
                t.get("winner") for t in self.tokens
            ) else MarketStatus.CLOSED
        return MarketStatus.ACTIVE


class CLOBMarket(BaseModel):
    """Market data from CLOB API (trading)"""
    condition_id: str
    question_id: str
    tokens: List[CLOBToken] = Field(default_factory=list)

    min_tick_size: float = Field(0.01, alias="minimum_tick_size")
    min_order_size: float = Field(1.0, alias="minimum_order_size")

    active: bool = True
    closed: bool = False
    accepting_orders: bool = Field(True, alias="accepting_orders")
    accepting_order_timestamp: Optional[str] = None

    maker_fee: float = Field(0.0, alias="maker_base_fee")
    taker_fee: float = Field(0.0, alias="taker_base_fee")

    class Config:
        populate_by_name = True


# ============================================================================
# Order Book Models
# ============================================================================

class OrderBookLevel(BaseModel):
    """Single price level in order book"""
    price: float
    size: float

    @computed_field
    @property
    def value(self) -> float:
        """Total value at this level"""
        return self.price * self.size


class OrderBook(BaseModel):
    """Full order book for a token"""
    token_id: str
    market: Optional[str] = None
    asset_id: Optional[str] = None
    hash: Optional[str] = None
    timestamp: Optional[int] = None

    bids: List[OrderBookLevel] = Field(default_factory=list)
    asks: List[OrderBookLevel] = Field(default_factory=list)

    @computed_field
    @property
    def best_bid(self) -> Optional[float]:
        """Highest bid price"""
        return max((b.price for b in self.bids), default=None)

    @computed_field
    @property
    def best_ask(self) -> Optional[float]:
        """Lowest ask price"""
        return min((a.price for a in self.asks), default=None)

    @computed_field
    @property
    def spread(self) -> Optional[float]:
        """Bid-ask spread"""
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    @computed_field
    @property
    def spread_pct(self) -> Optional[float]:
        """Spread as percentage of mid price"""
        if self.spread is not None and self.mid_price:
            return (self.spread / self.mid_price) * 100
        return None

    @computed_field
    @property
    def mid_price(self) -> Optional[float]:
        """Mid-market price"""
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return None

    @computed_field
    @property
    def total_bid_depth(self) -> float:
        """Total size on bid side"""
        return sum(b.size for b in self.bids)

    @computed_field
    @property
    def total_ask_depth(self) -> float:
        """Total size on ask side"""
        return sum(a.size for a in self.asks)

    @computed_field
    @property
    def total_bid_value(self) -> float:
        """Total value on bid side"""
        return sum(b.price * b.size for b in self.bids)

    @computed_field
    @property
    def total_ask_value(self) -> float:
        """Total value on ask side"""
        return sum(a.price * a.size for a in self.asks)

    def depth_at_price(self, price: float, side: OrderSide) -> float:
        """Get cumulative depth up to a price level"""
        if side == OrderSide.BUY:
            return sum(b.size for b in self.bids if b.price >= price)
        return sum(a.size for a in self.asks if a.price <= price)

    def price_impact(self, size: float, side: OrderSide) -> Optional[float]:
        """Calculate price impact for a given order size"""
        levels = self.asks if side == OrderSide.BUY else self.bids
        if not levels:
            return None

        remaining = size
        total_cost = 0.0

        sorted_levels = sorted(levels, key=lambda x: x.price,
                               reverse=(side == OrderSide.SELL))

        for level in sorted_levels:
            fill = min(remaining, level.size)
            total_cost += fill * level.price
            remaining -= fill
            if remaining <= 0:
                break

        if remaining > 0:
            return None  # Insufficient liquidity

        avg_price = total_cost / size
        reference = self.best_ask if side == OrderSide.BUY else self.best_bid
        if reference:
            return abs(avg_price - reference) / reference
        return None


# ============================================================================
# Trade Models
# ============================================================================

class Trade(BaseModel):
    """Individual trade record"""
    id: Optional[str] = None
    taker_order_id: Optional[str] = None
    market: Optional[str] = None
    asset_id: Optional[str] = None

    side: str
    price: float
    size: float

    fee_rate_bps: Optional[float] = None
    timestamp: Optional[int] = None

    maker_address: Optional[str] = None
    match_time: Optional[str] = None
    outcome: Optional[str] = None
    owner: Optional[str] = None
    bucket_index: Optional[int] = None

    transaction_hash: Optional[str] = None

    @computed_field
    @property
    def value(self) -> float:
        """Trade value in USDC"""
        return self.price * self.size

    @computed_field
    @property
    def datetime(self) -> Optional[datetime]:
        """Convert timestamp to datetime"""
        if self.timestamp:
            return datetime.fromtimestamp(self.timestamp)
        return None


class TradeHistory(BaseModel):
    """Collection of trades with metadata"""
    token_id: str
    trades: List[Trade] = Field(default_factory=list)
    next_cursor: Optional[str] = None

    @computed_field
    @property
    def total_volume(self) -> float:
        """Total traded volume"""
        return sum(t.value for t in self.trades)

    @computed_field
    @property
    def trade_count(self) -> int:
        """Number of trades"""
        return len(self.trades)

    @computed_field
    @property
    def vwap(self) -> Optional[float]:
        """Volume-weighted average price"""
        if not self.trades:
            return None
        total_value = sum(t.price * t.size for t in self.trades)
        total_size = sum(t.size for t in self.trades)
        return total_value / total_size if total_size > 0 else None


# ============================================================================
# Order Models
# ============================================================================

class Order(BaseModel):
    """Order representation"""
    id: Optional[str] = None
    market: Optional[str] = None
    asset_id: Optional[str] = None

    side: str
    price: float
    original_size: float = Field(alias="original_size")
    size_matched: float = Field(0.0, alias="size_matched")

    order_type: Optional[str] = Field(None, alias="type")
    status: Optional[str] = None
    outcome: Optional[str] = None
    owner: Optional[str] = None

    expiration: Optional[int] = None
    created_at: Optional[int] = None

    associate_trades: List[Trade] = Field(default_factory=list)

    class Config:
        populate_by_name = True

    @computed_field
    @property
    def remaining_size(self) -> float:
        """Unfilled size"""
        return self.original_size - self.size_matched

    @computed_field
    @property
    def fill_pct(self) -> float:
        """Percentage filled"""
        if self.original_size > 0:
            return (self.size_matched / self.original_size) * 100
        return 0.0


# ============================================================================
# Liquidity Metrics
# ============================================================================

class LiquidityMetrics(BaseModel):
    """Comprehensive liquidity analysis for a market"""
    token_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Price metrics
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    mid_price: Optional[float] = None
    spread: Optional[float] = None
    spread_bps: Optional[float] = None  # Spread in basis points

    # Depth metrics
    bid_depth_total: float = 0.0
    ask_depth_total: float = 0.0
    bid_value_total: float = 0.0  # In USDC
    ask_value_total: float = 0.0  # In USDC

    # Depth at various levels (cumulative USDC value)
    bid_depth_1pct: float = 0.0  # Depth within 1% of best bid
    ask_depth_1pct: float = 0.0  # Depth within 1% of best ask
    bid_depth_5pct: float = 0.0
    ask_depth_5pct: float = 0.0
    bid_depth_10pct: float = 0.0
    ask_depth_10pct: float = 0.0

    # Price impact
    impact_buy_100: Optional[float] = None   # Price impact to buy $100
    impact_buy_500: Optional[float] = None
    impact_buy_1000: Optional[float] = None
    impact_sell_100: Optional[float] = None
    impact_sell_500: Optional[float] = None
    impact_sell_1000: Optional[float] = None

    # Volume metrics
    volume_24h: Optional[float] = None
    trade_count_24h: Optional[int] = None
    avg_trade_size_24h: Optional[float] = None

    # Derived scores (0-100)
    @computed_field
    @property
    def depth_score(self) -> float:
        """Score based on order book depth (0-100)"""
        # Based on total value available
        total_value = self.bid_value_total + self.ask_value_total
        if total_value >= 100000:
            return 100.0
        elif total_value >= 50000:
            return 80.0
        elif total_value >= 10000:
            return 60.0
        elif total_value >= 5000:
            return 40.0
        elif total_value >= 1000:
            return 20.0
        return max(0, total_value / 50)  # 0-20 for <1000

    @computed_field
    @property
    def spread_score(self) -> float:
        """Score based on spread tightness (0-100)"""
        if self.spread_bps is None:
            return 0.0
        if self.spread_bps <= 50:  # 0.5%
            return 100.0
        elif self.spread_bps <= 100:  # 1%
            return 80.0
        elif self.spread_bps <= 200:  # 2%
            return 60.0
        elif self.spread_bps <= 500:  # 5%
            return 40.0
        elif self.spread_bps <= 1000:  # 10%
            return 20.0
        return 0.0

    @computed_field
    @property
    def liquidity_score(self) -> float:
        """Overall liquidity score (0-100)"""
        # Weighted average of components
        depth_weight = 0.4
        spread_weight = 0.4
        volume_weight = 0.2

        volume_score = 0.0
        if self.volume_24h:
            if self.volume_24h >= 50000:
                volume_score = 100.0
            elif self.volume_24h >= 10000:
                volume_score = 80.0
            elif self.volume_24h >= 5000:
                volume_score = 60.0
            elif self.volume_24h >= 1000:
                volume_score = 40.0
            else:
                volume_score = min(40, self.volume_24h / 25)

        return (
            self.depth_score * depth_weight +
            self.spread_score * spread_weight +
            volume_score * volume_weight
        )

    @computed_field
    @property
    def is_tradeable(self) -> bool:
        """Whether this market has sufficient liquidity for trading"""
        return (
            self.liquidity_score >= 30 and
            self.spread_bps is not None and
            self.spread_bps < 1000 and
            self.bid_depth_total > 0 and
            self.ask_depth_total > 0
        )


# ============================================================================
# Event Models
# ============================================================================

class Event(BaseModel):
    """Event containing multiple markets"""
    id: str
    slug: Optional[str] = None
    title: str
    description: Optional[str] = None

    active: bool = True
    closed: bool = False
    archived: bool = False

    start_date: Optional[datetime] = Field(None, alias="startDate")
    end_date: Optional[datetime] = Field(None, alias="endDate")
    created_at: Optional[datetime] = Field(None, alias="createdAt")

    markets: List[GammaMarket] = Field(default_factory=list)

    volume: Optional[float] = None
    liquidity: Optional[float] = None

    category: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

    image: Optional[str] = None
    icon: Optional[str] = None

    class Config:
        populate_by_name = True


# ============================================================================
# Response Wrappers
# ============================================================================

class PaginatedResponse(BaseModel):
    """Generic paginated response"""
    data: List[Any] = Field(default_factory=list)
    next_cursor: Optional[str] = None
    limit: Optional[int] = None
    count: Optional[int] = None


class MarketFilter(BaseModel):
    """Filter criteria for market queries"""
    status: Optional[MarketStatus] = None
    active_only: bool = True
    closed_only: bool = False

    min_liquidity: Optional[float] = None
    min_volume_24h: Optional[float] = None
    max_spread_pct: Optional[float] = None

    search_query: Optional[str] = None

    event_slug: Optional[str] = None
    category: Optional[str] = None

    order_by: Optional[str] = "volume_24hr"  # volume_24hr, liquidity, created_at
    ascending: bool = False

    limit: int = 100
    offset: int = 0


# ============================================================================
# Raw Liquidity Data (No Abstraction)
# ============================================================================

class RawOrderBookLevel(BaseModel):
    """Individual order book level with full detail"""
    price: float
    size: float  # Number of shares

    @property
    def notional(self) -> float:
        """Dollar value at this level"""
        return self.price * self.size

    @property
    def cost_to_fill(self) -> float:
        """Cost to completely fill this level"""
        return self.price * self.size


class OrderBookSnapshot(BaseModel):
    """Complete order book state at a point in time"""
    token_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    snapshot_hash: Optional[str] = None

    bids: List[RawOrderBookLevel] = Field(default_factory=list)
    asks: List[RawOrderBookLevel] = Field(default_factory=list)

    # Raw aggregates - no scores, just numbers
    @computed_field
    @property
    def bid_count(self) -> int:
        """Number of bid levels"""
        return len(self.bids)

    @computed_field
    @property
    def ask_count(self) -> int:
        """Number of ask levels"""
        return len(self.asks)

    @computed_field
    @property
    def total_bid_size(self) -> float:
        """Total shares on bid side"""
        return sum(b.size for b in self.bids)

    @computed_field
    @property
    def total_ask_size(self) -> float:
        """Total shares on ask side"""
        return sum(a.size for a in self.asks)

    @computed_field
    @property
    def total_bid_notional(self) -> float:
        """Total $ on bid side"""
        return sum(b.notional for b in self.bids)

    @computed_field
    @property
    def total_ask_notional(self) -> float:
        """Total $ on ask side"""
        return sum(a.notional for a in self.asks)

    @computed_field
    @property
    def best_bid(self) -> Optional[float]:
        return max((b.price for b in self.bids), default=None)

    @computed_field
    @property
    def best_ask(self) -> Optional[float]:
        return min((a.price for a in self.asks), default=None)

    @computed_field
    @property
    def spread_abs(self) -> Optional[float]:
        """Absolute spread in price units"""
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None

    @computed_field
    @property
    def spread_bps(self) -> Optional[float]:
        """Spread in basis points"""
        if self.best_bid and self.best_ask:
            mid = (self.best_bid + self.best_ask) / 2
            return ((self.best_ask - self.best_bid) / mid) * 10000
        return None

    @computed_field
    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None

    @computed_field
    @property
    def imbalance(self) -> Optional[float]:
        """Order book imbalance: (bids - asks) / (bids + asks), range [-1, 1]"""
        total = self.total_bid_notional + self.total_ask_notional
        if total > 0:
            return (self.total_bid_notional - self.total_ask_notional) / total
        return None

    def compute_hash(self) -> str:
        """Compute hash of order book state for change detection"""
        content = f"{self.token_id}|"
        content += "|".join(f"{b.price}:{b.size}" for b in sorted(self.bids, key=lambda x: -x.price))
        content += "||"
        content += "|".join(f"{a.price}:{a.size}" for a in sorted(self.asks, key=lambda x: x.price))
        return hashlib.md5(content.encode()).hexdigest()

    def depth_at_distance(self, bps: float) -> Tuple[float, float]:
        """
        Get bid/ask depth within X basis points of best price
        Returns: (bid_notional, ask_notional)
        """
        bid_depth = 0.0
        ask_depth = 0.0

        if self.best_bid:
            threshold = self.best_bid * (1 - bps / 10000)
            bid_depth = sum(b.notional for b in self.bids if b.price >= threshold)

        if self.best_ask:
            threshold = self.best_ask * (1 + bps / 10000)
            ask_depth = sum(a.notional for a in self.asks if a.price <= threshold)

        return bid_depth, ask_depth

    def cost_to_move_price(self, bps: float, side: OrderSide) -> Optional[float]:
        """
        Calculate cost to move price by X basis points
        side: BUY = cost to push ask up, SELL = cost to push bid down
        """
        if side == OrderSide.BUY:
            if not self.best_ask:
                return None
            target = self.best_ask * (1 + bps / 10000)
            return sum(a.notional for a in self.asks if a.price <= target)
        else:
            if not self.best_bid:
                return None
            target = self.best_bid * (1 - bps / 10000)
            return sum(b.notional for b in self.bids if b.price >= target)

    def fill_simulation(self, size: float, side: OrderSide) -> Dict[str, Any]:
        """
        Simulate filling an order of given size
        Returns detailed execution analysis
        """
        levels = self.asks if side == OrderSide.BUY else self.bids
        sorted_levels = sorted(levels, key=lambda x: x.price, reverse=(side == OrderSide.SELL))

        remaining = size
        fills: List[Dict] = []
        total_cost = 0.0

        for level in sorted_levels:
            if remaining <= 0:
                break
            fill_size = min(remaining, level.size)
            fill_cost = fill_size * level.price
            fills.append({
                "price": level.price,
                "size": fill_size,
                "cost": fill_cost,
            })
            total_cost += fill_cost
            remaining -= fill_size

        filled = size - remaining
        avg_price = total_cost / filled if filled > 0 else None
        reference = self.best_ask if side == OrderSide.BUY else self.best_bid
        slippage = None
        if avg_price and reference:
            slippage = abs(avg_price - reference) / reference

        return {
            "requested_size": size,
            "filled_size": filled,
            "unfilled_size": remaining,
            "fill_rate": filled / size if size > 0 else 0,
            "total_cost": total_cost,
            "avg_price": avg_price,
            "reference_price": reference,
            "slippage_pct": slippage * 100 if slippage else None,
            "slippage_bps": slippage * 10000 if slippage else None,
            "levels_consumed": len(fills),
            "fills": fills,
        }


# ============================================================================
# Order Book Change Detection
# ============================================================================

class OrderBookDelta(BaseModel):
    """Changes between two order book snapshots"""
    token_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    time_delta_ms: Optional[int] = None

    # Price changes
    bid_price_change: Optional[float] = None  # Change in best bid
    ask_price_change: Optional[float] = None  # Change in best ask
    mid_price_change: Optional[float] = None
    spread_change: Optional[float] = None

    # Size changes
    bid_size_change: float = 0.0  # Net change in bid size
    ask_size_change: float = 0.0  # Net change in ask size
    bid_notional_change: float = 0.0
    ask_notional_change: float = 0.0

    # Level changes
    new_bid_levels: List[RawOrderBookLevel] = Field(default_factory=list)
    removed_bid_levels: List[RawOrderBookLevel] = Field(default_factory=list)
    modified_bid_levels: List[Dict[str, Any]] = Field(default_factory=list)  # {price, old_size, new_size}

    new_ask_levels: List[RawOrderBookLevel] = Field(default_factory=list)
    removed_ask_levels: List[RawOrderBookLevel] = Field(default_factory=list)
    modified_ask_levels: List[Dict[str, Any]] = Field(default_factory=list)

    @computed_field
    @property
    def has_significant_change(self) -> bool:
        """Whether there was meaningful change"""
        return (
            len(self.new_bid_levels) > 0 or
            len(self.removed_bid_levels) > 0 or
            len(self.new_ask_levels) > 0 or
            len(self.removed_ask_levels) > 0 or
            abs(self.bid_notional_change) > 100 or
            abs(self.ask_notional_change) > 100
        )

    @computed_field
    @property
    def net_flow(self) -> float:
        """Net order flow: positive = more bids, negative = more asks"""
        return self.bid_notional_change - self.ask_notional_change

    @computed_field
    @property
    def is_bid_heavy(self) -> bool:
        """Whether changes favor buy side"""
        return self.net_flow > 0

    @computed_field
    @property
    def largest_new_order(self) -> Optional[Dict[str, Any]]:
        """Largest new order added"""
        all_new = [(l, "bid") for l in self.new_bid_levels] + [(l, "ask") for l in self.new_ask_levels]
        if not all_new:
            return None
        largest = max(all_new, key=lambda x: x[0].notional)
        return {
            "side": largest[1],
            "price": largest[0].price,
            "size": largest[0].size,
            "notional": largest[0].notional,
        }


# ============================================================================
# Large Order / Whale Detection
# ============================================================================

class LargeOrder(BaseModel):
    """Detected large order on the book"""
    token_id: str
    market_id: Optional[str] = None
    detected_at: datetime = Field(default_factory=datetime.utcnow)

    side: str  # "bid" or "ask"
    price: float
    size: float
    notional: float

    # Context
    pct_of_book: float  # What % of that side of book this represents
    pct_of_level: float  # What % of orders at this price level
    distance_from_mid_bps: Optional[float] = None  # How far from mid price

    # Classification
    is_whale: bool = False  # > $10k typically
    is_iceberg_candidate: bool = False  # Suspiciously round size or repeated

    @computed_field
    @property
    def size_category(self) -> str:
        """Categorize order size"""
        if self.notional >= 50000:
            return "massive"
        elif self.notional >= 10000:
            return "whale"
        elif self.notional >= 5000:
            return "large"
        elif self.notional >= 1000:
            return "medium"
        return "small"


class WhaleTrade(BaseModel):
    """Large executed trade"""
    trade_id: Optional[str] = None
    token_id: str
    market_id: Optional[str] = None
    market_question: Optional[str] = None

    timestamp: datetime
    side: str
    price: float
    size: float
    notional: float

    # Addresses
    maker_address: Optional[str] = None
    taker_address: Optional[str] = None

    # Impact
    price_before: Optional[float] = None
    price_after: Optional[float] = None
    price_impact_bps: Optional[float] = None

    # Context
    pct_of_daily_volume: Optional[float] = None

    @computed_field
    @property
    def is_whale(self) -> bool:
        return self.notional >= 5000


class WalletActivity(BaseModel):
    """Aggregated activity for a wallet address"""
    address: str
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None

    # Trade stats
    total_trades: int = 0
    total_volume: float = 0.0
    total_buy_volume: float = 0.0
    total_sell_volume: float = 0.0

    # Per-market breakdown
    markets_traded: List[str] = Field(default_factory=list)
    market_volumes: Dict[str, float] = Field(default_factory=dict)

    # Trade history
    recent_trades: List[WhaleTrade] = Field(default_factory=list)
    largest_trade: Optional[WhaleTrade] = None

    # Patterns
    avg_trade_size: Optional[float] = None
    win_rate: Optional[float] = None  # If we can track resolved markets

    @computed_field
    @property
    def is_whale(self) -> bool:
        return self.total_volume >= 50000

    @computed_field
    @property
    def is_active(self) -> bool:
        if not self.last_seen:
            return False
        return (datetime.utcnow() - self.last_seen).days < 7

    @computed_field
    @property
    def net_position_bias(self) -> Optional[float]:
        """Net bias toward buying vs selling, range [-1, 1]"""
        total = self.total_buy_volume + self.total_sell_volume
        if total > 0:
            return (self.total_buy_volume - self.total_sell_volume) / total
        return None


class MarketWhaleActivity(BaseModel):
    """Whale activity summary for a specific market"""
    market_id: str
    token_id: str
    market_question: Optional[str] = None
    analysis_period_hours: int = 24
    analyzed_at: datetime = Field(default_factory=datetime.utcnow)

    # Current large orders on book
    large_bids: List[LargeOrder] = Field(default_factory=list)
    large_asks: List[LargeOrder] = Field(default_factory=list)

    # Recent whale trades
    whale_trades: List[WhaleTrade] = Field(default_factory=list)

    # Active whales in this market
    active_whales: List[str] = Field(default_factory=list)  # Addresses
    whale_buy_volume: float = 0.0
    whale_sell_volume: float = 0.0

    # Concentration metrics
    top_5_pct_of_volume: Optional[float] = None  # What % of volume from top 5 wallets

    @computed_field
    @property
    def whale_flow(self) -> float:
        """Net whale buying pressure"""
        return self.whale_buy_volume - self.whale_sell_volume

    @computed_field
    @property
    def whale_flow_bias(self) -> Optional[float]:
        """Whale flow as ratio, range [-1, 1]"""
        total = self.whale_buy_volume + self.whale_sell_volume
        if total > 0:
            return self.whale_flow / total
        return None

    @computed_field
    @property
    def has_whale_activity(self) -> bool:
        return len(self.whale_trades) > 0 or len(self.large_bids) > 0 or len(self.large_asks) > 0


# ============================================================================
# Order Flow Tracking
# ============================================================================

class OrderFlowEvent(BaseModel):
    """Single order flow event from WebSocket or polling"""
    event_type: str  # "new_order", "cancel", "fill", "partial_fill"
    token_id: str
    market_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    side: str
    price: float
    size: float
    remaining_size: Optional[float] = None

    order_id: Optional[str] = None
    maker_address: Optional[str] = None

    @computed_field
    @property
    def notional(self) -> float:
        return self.price * self.size

    @computed_field
    @property
    def is_large(self) -> bool:
        return self.notional >= 1000


class OrderFlowSummary(BaseModel):
    """Aggregated order flow over a time period"""
    token_id: str
    period_start: datetime
    period_end: datetime
    period_seconds: int

    # Order counts
    new_bid_orders: int = 0
    new_ask_orders: int = 0
    cancelled_bid_orders: int = 0
    cancelled_ask_orders: int = 0

    # Volumes
    new_bid_volume: float = 0.0
    new_ask_volume: float = 0.0
    cancelled_bid_volume: float = 0.0
    cancelled_ask_volume: float = 0.0

    # Fills
    buy_fills: int = 0
    sell_fills: int = 0
    buy_fill_volume: float = 0.0
    sell_fill_volume: float = 0.0

    # Large order activity
    large_orders_placed: int = 0
    large_orders_cancelled: int = 0
    large_order_events: List[OrderFlowEvent] = Field(default_factory=list)

    @computed_field
    @property
    def net_order_flow(self) -> float:
        """Net new order flow (bids - asks)"""
        return (self.new_bid_volume - self.cancelled_bid_volume) - (self.new_ask_volume - self.cancelled_ask_volume)

    @computed_field
    @property
    def cancel_rate_bids(self) -> Optional[float]:
        """Bid cancellation rate"""
        if self.new_bid_orders > 0:
            return self.cancelled_bid_orders / self.new_bid_orders
        return None

    @computed_field
    @property
    def cancel_rate_asks(self) -> Optional[float]:
        """Ask cancellation rate"""
        if self.new_ask_orders > 0:
            return self.cancelled_ask_orders / self.new_ask_orders
        return None


# ============================================================================
# Illiquid Market Signals
# ============================================================================

class IlliquidMarketSignal(BaseModel):
    """Detected signal in an illiquid market"""
    signal_type: str  # "large_order", "whale_entry", "book_imbalance", "spread_compression", etc.
    token_id: str
    market_id: Optional[str] = None
    market_question: Optional[str] = None
    detected_at: datetime = Field(default_factory=datetime.utcnow)

    # Signal details
    description: str
    severity: str  # "low", "medium", "high"
    confidence: float  # 0-1

    # Raw data that triggered signal
    trigger_data: Dict[str, Any] = Field(default_factory=dict)

    # Market context
    mid_price: Optional[float] = None
    spread_bps: Optional[float] = None
    book_imbalance: Optional[float] = None

    # Suggested action
    suggested_side: Optional[str] = None  # "buy", "sell", None

    @computed_field
    @property
    def is_actionable(self) -> bool:
        return self.confidence >= 0.7 and self.severity in ("medium", "high")
