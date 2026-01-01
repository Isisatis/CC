"""Data models for market data."""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Optional, Dict, Any
import json


@dataclass
class Market:
    """Represents a Polymarket market."""

    condition_id: str
    question: str
    description: str = ""
    market_slug: str = ""
    end_date_iso: str = ""
    game_start_time: str = ""
    active: bool = True
    closed: bool = False
    archived: bool = False
    accepting_orders: bool = True
    minimum_order_size: float = 0.0
    minimum_tick_size: float = 0.01
    volume: float = 0.0
    volume_24h: float = 0.0
    liquidity: float = 0.0
    tokens: List[Dict[str, Any]] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    fetched_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "Market":
        """Create Market from API response data."""
        return cls(
            condition_id=data.get("condition_id", ""),
            question=data.get("question", ""),
            description=data.get("description", ""),
            market_slug=data.get("market_slug", ""),
            end_date_iso=data.get("end_date_iso", ""),
            game_start_time=data.get("game_start_time", ""),
            active=data.get("active", True),
            closed=data.get("closed", False),
            archived=data.get("archived", False),
            accepting_orders=data.get("accepting_orders", True),
            minimum_order_size=float(data.get("minimum_order_size", 0)),
            minimum_tick_size=float(data.get("minimum_tick_size", 0.01)),
            volume=float(data.get("volume", 0) or 0),
            volume_24h=float(data.get("volume_24h", 0) or 0),
            liquidity=float(data.get("liquidity", 0) or 0),
            tokens=data.get("tokens", []),
            tags=data.get("tags", []),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


@dataclass
class OrderBookLevel:
    """Represents a single level in an order book."""

    price: float
    size: float

    def to_dict(self) -> Dict[str, float]:
        return {"price": self.price, "size": self.size}


@dataclass
class OrderBook:
    """Represents an order book snapshot."""

    token_id: str
    bids: List[OrderBookLevel] = field(default_factory=list)
    asks: List[OrderBookLevel] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @property
    def best_bid(self) -> Optional[float]:
        """Get the best (highest) bid price."""
        if self.bids:
            return max(b.price for b in self.bids)
        return None

    @property
    def best_ask(self) -> Optional[float]:
        """Get the best (lowest) ask price."""
        if self.asks:
            return min(a.price for a in self.asks)
        return None

    @property
    def spread(self) -> Optional[float]:
        """Calculate the bid-ask spread."""
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    @property
    def spread_pct(self) -> Optional[float]:
        """Calculate the spread as a percentage of midpoint."""
        if self.spread is not None and self.midpoint is not None:
            return (self.spread / self.midpoint) * 100
        return None

    @property
    def midpoint(self) -> Optional[float]:
        """Calculate the midpoint price."""
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def total_bid_size(self) -> float:
        """Total size on the bid side."""
        return sum(b.size for b in self.bids)

    @property
    def total_ask_size(self) -> float:
        """Total size on the ask side."""
        return sum(a.size for a in self.asks)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "token_id": self.token_id,
            "bids": [b.to_dict() for b in self.bids],
            "asks": [a.to_dict() for a in self.asks],
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "spread": self.spread,
            "spread_pct": self.spread_pct,
            "midpoint": self.midpoint,
            "total_bid_size": self.total_bid_size,
            "total_ask_size": self.total_ask_size,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_api_response(cls, token_id: str, data: Dict[str, Any]) -> "OrderBook":
        """Create OrderBook from API response data."""
        bids = [
            OrderBookLevel(price=float(b.get("price", 0)), size=float(b.get("size", 0)))
            for b in data.get("bids", [])
        ]
        asks = [
            OrderBookLevel(price=float(a.get("price", 0)), size=float(a.get("size", 0)))
            for a in data.get("asks", [])
        ]
        return cls(token_id=token_id, bids=bids, asks=asks)


@dataclass
class Trade:
    """Represents a trade."""

    id: str
    token_id: str
    price: float
    size: float
    side: str  # 'buy' or 'sell'
    maker: str = ""
    taker: str = ""
    timestamp: str = ""
    transaction_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "Trade":
        """Create Trade from API response data."""
        return cls(
            id=data.get("id", ""),
            token_id=data.get("asset_id", data.get("token_id", "")),
            price=float(data.get("price", 0)),
            size=float(data.get("size", 0)),
            side=data.get("side", ""),
            maker=data.get("maker", ""),
            taker=data.get("taker", ""),
            timestamp=data.get("created_at", data.get("timestamp", "")),
            transaction_hash=data.get("transaction_hash", ""),
        )


@dataclass
class PriceSnapshot:
    """Represents a price snapshot at a point in time."""

    token_id: str
    price: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    volume: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_order_book(cls, order_book: OrderBook) -> "PriceSnapshot":
        """Create PriceSnapshot from OrderBook."""
        return cls(
            token_id=order_book.token_id,
            price=order_book.midpoint or 0.0,
            bid=order_book.best_bid,
            ask=order_book.best_ask,
            timestamp=order_book.timestamp,
        )
