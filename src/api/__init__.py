"""
Polymarket API Clients

Complete API access to Polymarket's CLOB and Gamma APIs including:
- Market discovery and filtering (active/closed/resolved)
- Order book data and trading operations
- Liquidity metrics and analysis
- Trade history and market events
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

    # Filtering & Metrics
    MarketFilter,
    LiquidityMetrics,
    PaginatedResponse,
)

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

    # Models
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
]
