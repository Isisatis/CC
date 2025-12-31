"""
Polymarket API Clients

Complete API access to Polymarket's CLOB and Gamma APIs including:
- Market discovery and filtering (active/closed/resolved)
- Order book data and trading operations
- Liquidity metrics and analysis
- Trade history and market events
- Whale detection and order flow tracking
- Raw order book data for illiquid market analysis
"""

from .client import (
    PolymarketClient,
    GammaAPIClient,
    CLOBAPIClient,
    RateLimiter,
    BaseAPIClient,
)

from .models import (
    # Enums
    MarketStatus,
    OrderSide,
    OrderType,
    OutcomeType,

    # Core market models
    Token,
    CLOBToken,
    GammaMarket,
    CLOBMarket,
    Event,

    # Order book
    OrderBook,
    OrderBookLevel,

    # Trading
    Trade,
    TradeHistory,
    Order,

    # Filtering & Metrics (abstracted)
    MarketFilter,
    LiquidityMetrics,
    PaginatedResponse,

    # Raw order book data (no abstraction)
    RawOrderBookLevel,
    OrderBookSnapshot,
    OrderBookDelta,

    # Whale & Large Order Detection
    LargeOrder,
    WhaleTrade,
    WalletActivity,
    MarketWhaleActivity,

    # Order Flow
    OrderFlowEvent,
    OrderFlowSummary,

    # Signals
    IlliquidMarketSignal,
)

from .orderflow import (
    OrderBookTracker,
    OrderFlowAnalyzer,
    WhaleDetector,
    SignalDetector,
)

from .scanner import (
    MarketScanner,
    MarketOpportunity,
    format_opportunity,
)

# Conditional WebSocket import
try:
    from .orderflow import PolymarketWebSocket
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False

__all__ = [
    # Clients
    "PolymarketClient",
    "GammaAPIClient",
    "CLOBAPIClient",
    "RateLimiter",
    "BaseAPIClient",

    # Enums
    "MarketStatus",
    "OrderSide",
    "OrderType",
    "OutcomeType",

    # Core Models
    "Token",
    "CLOBToken",
    "GammaMarket",
    "CLOBMarket",
    "Event",
    "OrderBook",
    "OrderBookLevel",
    "Trade",
    "TradeHistory",
    "Order",
    "MarketFilter",
    "LiquidityMetrics",
    "PaginatedResponse",

    # Raw Order Book (for illiquid market analysis)
    "RawOrderBookLevel",
    "OrderBookSnapshot",
    "OrderBookDelta",

    # Whale Detection
    "LargeOrder",
    "WhaleTrade",
    "WalletActivity",
    "MarketWhaleActivity",

    # Order Flow
    "OrderFlowEvent",
    "OrderFlowSummary",
    "OrderBookTracker",
    "OrderFlowAnalyzer",
    "WhaleDetector",
    "SignalDetector",

    # Signals
    "IlliquidMarketSignal",

    # Scanner
    "MarketScanner",
    "MarketOpportunity",
    "format_opportunity",
]

if HAS_WEBSOCKET:
    __all__.append("PolymarketWebSocket")
