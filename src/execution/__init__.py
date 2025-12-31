"""Trade execution and order management"""

from .arb_executor import (
    # Enums
    OrderSide,
    OrderStatus,
    Platform,

    # Data classes
    OrderRequest,
    OrderResult,
    ArbExecution,

    # Clients
    PlatformOrderClient,
    PolymarketOrderClient,
    KalshiOrderClient,

    # Executor
    ArbExecutor,

    # Utilities
    create_arb_orders_from_opportunity,
    execute_arb,
)

__all__ = [
    "OrderSide",
    "OrderStatus",
    "Platform",
    "OrderRequest",
    "OrderResult",
    "ArbExecution",
    "PlatformOrderClient",
    "PolymarketOrderClient",
    "KalshiOrderClient",
    "ArbExecutor",
    "create_arb_orders_from_opportunity",
    "execute_arb",
]
