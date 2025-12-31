"""
Cross-Platform Arbitrage Executor

Handles atomic-ish execution of arbitrage opportunities across
Polymarket and Kalshi with parallel order submission.

For low-capacity strategies, the approach is:
1. Pre-fund both platforms (no transfer delays)
2. Fire both legs simultaneously via asyncio
3. Use limit orders at calculated arb prices
4. Handle failures with hedging or exit

Execution Flow:
    opportunity → validate → prepare_orders → submit_parallel → monitor → report

Failure Handling:
    - Both succeed: Perfect execution
    - One fails, one succeeds: Hedge the open position or accept directional risk
    - Both fail: No exposure, retry or skip
"""

import asyncio
import logging
from enum import Enum
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List, Callable
from dataclasses import dataclass, field
from decimal import Decimal

logger = logging.getLogger(__name__)


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Platform(str, Enum):
    POLYMARKET = "polymarket"
    KALSHI = "kalshi"


@dataclass
class OrderRequest:
    """Single order to be submitted"""
    platform: Platform
    market_id: str
    token_id: str  # Poly token or Kalshi contract
    side: OrderSide
    price: Decimal  # Limit price (0-1 for prediction markets)
    size: Decimal  # Number of contracts/shares

    # Execution config
    timeout_seconds: float = 30.0
    retry_count: int = 0

    def __post_init__(self):
        # Ensure Decimal types
        if not isinstance(self.price, Decimal):
            self.price = Decimal(str(self.price))
        if not isinstance(self.size, Decimal):
            self.size = Decimal(str(self.size))


@dataclass
class OrderResult:
    """Result of a single order submission"""
    request: OrderRequest
    status: OrderStatus
    order_id: Optional[str] = None
    filled_size: Decimal = Decimal("0")
    filled_price: Optional[Decimal] = None
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    latency_ms: float = 0.0

    @property
    def is_success(self) -> bool:
        return self.status in (OrderStatus.FILLED, OrderStatus.PARTIAL)

    @property
    def notional(self) -> Decimal:
        if self.filled_price:
            return self.filled_size * self.filled_price
        return Decimal("0")


@dataclass
class ArbExecution:
    """Result of full arbitrage execution (both legs)"""
    leg1: OrderResult  # First platform leg
    leg2: OrderResult  # Second platform leg

    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: Optional[datetime] = None

    @property
    def both_filled(self) -> bool:
        return self.leg1.is_success and self.leg2.is_success

    @property
    def one_filled(self) -> bool:
        return self.leg1.is_success != self.leg2.is_success

    @property
    def none_filled(self) -> bool:
        return not self.leg1.is_success and not self.leg2.is_success

    @property
    def needs_hedge(self) -> bool:
        return self.one_filled

    @property
    def exposed_leg(self) -> Optional[OrderResult]:
        """Returns the leg that filled if only one did"""
        if not self.one_filled:
            return None
        return self.leg1 if self.leg1.is_success else self.leg2

    @property
    def total_cost(self) -> Decimal:
        return self.leg1.notional + self.leg2.notional

    @property
    def total_latency_ms(self) -> float:
        return max(self.leg1.latency_ms, self.leg2.latency_ms)

    def summary(self) -> Dict[str, Any]:
        return {
            "status": "success" if self.both_filled else ("partial" if self.one_filled else "failed"),
            "leg1_platform": self.leg1.request.platform.value,
            "leg1_status": self.leg1.status.value,
            "leg1_filled": float(self.leg1.filled_size),
            "leg2_platform": self.leg2.request.platform.value,
            "leg2_status": self.leg2.status.value,
            "leg2_filled": float(self.leg2.filled_size),
            "total_cost": float(self.total_cost),
            "latency_ms": self.total_latency_ms,
            "needs_hedge": self.needs_hedge,
        }


class PlatformOrderClient:
    """
    Abstract interface for platform-specific order submission.

    Implementations handle the actual API calls to Polymarket/Kalshi.
    """

    async def submit_order(self, request: OrderRequest) -> OrderResult:
        raise NotImplementedError

    async def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError

    async def get_order_status(self, order_id: str) -> OrderStatus:
        raise NotImplementedError


class PolymarketOrderClient(PlatformOrderClient):
    """
    Polymarket order submission via CLOB API.

    Requires:
    - Funded wallet on Polygon
    - API credentials (or wallet signing)
    - Approved for CLOB trading
    """

    def __init__(self, api_key: Optional[str] = None, private_key: Optional[str] = None):
        self.api_key = api_key
        self.private_key = private_key
        # In production, initialize py_clob_client here

    async def submit_order(self, request: OrderRequest) -> OrderResult:
        """Submit order to Polymarket CLOB"""
        start = datetime.utcnow()

        try:
            # Polymarket uses signed orders
            # The py_clob_client library handles signing

            # For now, simulate the API call structure
            # In production:
            # from py_clob_client.client import ClobClient
            # client = ClobClient(host, key=api_key, chain_id=137)
            # order = client.create_and_post_order(OrderArgs(...))

            logger.info(f"[POLY] Submitting {request.side.value} {request.size} @ {request.price}")

            # Simulate network latency
            await asyncio.sleep(0.1)  # Remove in production

            latency = (datetime.utcnow() - start).total_seconds() * 1000

            # Production implementation would return real order ID
            return OrderResult(
                request=request,
                status=OrderStatus.SUBMITTED,
                order_id=f"poly_{datetime.utcnow().timestamp()}",
                latency_ms=latency,
            )

        except Exception as e:
            latency = (datetime.utcnow() - start).total_seconds() * 1000
            logger.error(f"[POLY] Order failed: {e}")
            return OrderResult(
                request=request,
                status=OrderStatus.FAILED,
                error=str(e),
                latency_ms=latency,
            )

    async def cancel_order(self, order_id: str) -> bool:
        try:
            logger.info(f"[POLY] Cancelling order {order_id}")
            # client.cancel(order_id)
            return True
        except Exception as e:
            logger.error(f"[POLY] Cancel failed: {e}")
            return False

    async def get_order_status(self, order_id: str) -> OrderStatus:
        # client.get_order(order_id)
        return OrderStatus.PENDING


class KalshiOrderClient(PlatformOrderClient):
    """
    Kalshi order submission via REST API.

    Requires:
    - Kalshi account with trading enabled
    - API credentials
    - Funded account balance
    """

    def __init__(self, email: Optional[str] = None, password: Optional[str] = None, api_key: Optional[str] = None):
        self.email = email
        self.password = password
        self.api_key = api_key
        self.token: Optional[str] = None

    async def _ensure_auth(self):
        """Ensure we have valid auth token"""
        if self.token:
            return
        # In production: login and get token
        # POST /trade-api/v2/login
        pass

    async def submit_order(self, request: OrderRequest) -> OrderResult:
        """Submit order to Kalshi"""
        start = datetime.utcnow()

        try:
            await self._ensure_auth()

            # Kalshi API order structure:
            # POST /trade-api/v2/portfolio/orders
            # {
            #   "ticker": "KXBTC-25100-T31",
            #   "action": "buy",
            #   "side": "yes",
            #   "type": "limit",
            #   "count": 10,
            #   "yes_price": 55  # In cents (1-99)
            # }

            logger.info(f"[KALSHI] Submitting {request.side.value} {request.size} @ {request.price}")

            await asyncio.sleep(0.1)  # Remove in production

            latency = (datetime.utcnow() - start).total_seconds() * 1000

            return OrderResult(
                request=request,
                status=OrderStatus.SUBMITTED,
                order_id=f"kalshi_{datetime.utcnow().timestamp()}",
                latency_ms=latency,
            )

        except Exception as e:
            latency = (datetime.utcnow() - start).total_seconds() * 1000
            logger.error(f"[KALSHI] Order failed: {e}")
            return OrderResult(
                request=request,
                status=OrderStatus.FAILED,
                error=str(e),
                latency_ms=latency,
            )

    async def cancel_order(self, order_id: str) -> bool:
        try:
            logger.info(f"[KALSHI] Cancelling order {order_id}")
            # DELETE /trade-api/v2/portfolio/orders/{order_id}
            return True
        except Exception as e:
            logger.error(f"[KALSHI] Cancel failed: {e}")
            return False

    async def get_order_status(self, order_id: str) -> OrderStatus:
        # GET /trade-api/v2/portfolio/orders/{order_id}
        return OrderStatus.PENDING


class ArbExecutor:
    """
    Executes cross-platform arbitrage with parallel order submission.

    Usage:
        executor = ArbExecutor(
            poly_client=PolymarketOrderClient(api_key="..."),
            kalshi_client=KalshiOrderClient(email="...", password="..."),
        )

        # From ArbitrageOpportunity
        result = await executor.execute(
            poly_order=OrderRequest(
                platform=Platform.POLYMARKET,
                market_id="...",
                token_id="...",
                side=OrderSide.BUY,
                price=Decimal("0.43"),
                size=Decimal("100"),
            ),
            kalshi_order=OrderRequest(
                platform=Platform.KALSHI,
                market_id="...",
                token_id="...",
                side=OrderSide.BUY,
                price=Decimal("0.50"),
                size=Decimal("100"),
            ),
        )

        if result.both_filled:
            print("Arb executed successfully!")
        elif result.needs_hedge:
            await executor.hedge_exposure(result)
    """

    def __init__(
        self,
        poly_client: PolymarketOrderClient,
        kalshi_client: KalshiOrderClient,
        max_slippage_bps: int = 50,  # 0.5% max slippage
        hedge_on_partial: bool = True,
    ):
        self.poly_client = poly_client
        self.kalshi_client = kalshi_client
        self.max_slippage_bps = max_slippage_bps
        self.hedge_on_partial = hedge_on_partial

        self._executions: List[ArbExecution] = []

    async def execute(
        self,
        poly_order: OrderRequest,
        kalshi_order: OrderRequest,
        wait_for_fill: bool = True,
        fill_timeout: float = 30.0,
    ) -> ArbExecution:
        """
        Execute both legs of an arbitrage in parallel.

        Args:
            poly_order: Order for Polymarket leg
            kalshi_order: Order for Kalshi leg
            wait_for_fill: Whether to wait for fills or return after submission
            fill_timeout: Max seconds to wait for fills

        Returns:
            ArbExecution with results of both legs
        """
        start_time = datetime.utcnow()

        logger.info(
            f"Executing arb: POLY {poly_order.side.value} {poly_order.size}@{poly_order.price} | "
            f"KALSHI {kalshi_order.side.value} {kalshi_order.size}@{kalshi_order.price}"
        )

        # Submit both orders in parallel - this is the key to atomic-ish execution
        poly_task = asyncio.create_task(self.poly_client.submit_order(poly_order))
        kalshi_task = asyncio.create_task(self.kalshi_client.submit_order(kalshi_order))

        # Wait for both submissions
        poly_result, kalshi_result = await asyncio.gather(poly_task, kalshi_task)

        # Optionally wait for fills
        if wait_for_fill:
            poly_result, kalshi_result = await self._wait_for_fills(
                poly_result, kalshi_result, fill_timeout
            )

        execution = ArbExecution(
            leg1=poly_result,
            leg2=kalshi_result,
            start_time=start_time,
            end_time=datetime.utcnow(),
        )

        self._executions.append(execution)

        # Log result
        if execution.both_filled:
            logger.info(f"Arb SUCCESS: cost=${execution.total_cost:.4f}, latency={execution.total_latency_ms:.1f}ms")
        elif execution.needs_hedge:
            exposed = execution.exposed_leg
            logger.warning(
                f"Arb PARTIAL: {exposed.request.platform.value} filled, "
                f"other leg failed. Hedge needed!"
            )
        else:
            logger.error(f"Arb FAILED: neither leg filled")

        return execution

    async def _wait_for_fills(
        self,
        poly_result: OrderResult,
        kalshi_result: OrderResult,
        timeout: float,
    ) -> Tuple[OrderResult, OrderResult]:
        """Poll for order fills with timeout"""

        deadline = datetime.utcnow().timestamp() + timeout

        while datetime.utcnow().timestamp() < deadline:
            # Check if already terminal
            poly_terminal = poly_result.status in (
                OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.FAILED
            )
            kalshi_terminal = kalshi_result.status in (
                OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.FAILED
            )

            if poly_terminal and kalshi_terminal:
                break

            # Poll for updates
            if not poly_terminal and poly_result.order_id:
                status = await self.poly_client.get_order_status(poly_result.order_id)
                poly_result.status = status
                if status == OrderStatus.FILLED:
                    poly_result.filled_size = poly_result.request.size
                    poly_result.filled_price = poly_result.request.price

            if not kalshi_terminal and kalshi_result.order_id:
                status = await self.kalshi_client.get_order_status(kalshi_result.order_id)
                kalshi_result.status = status
                if status == OrderStatus.FILLED:
                    kalshi_result.filled_size = kalshi_result.request.size
                    kalshi_result.filled_price = kalshi_result.request.price

            await asyncio.sleep(0.5)  # Poll interval

        return poly_result, kalshi_result

    async def hedge_exposure(self, execution: ArbExecution) -> Optional[OrderResult]:
        """
        Hedge an exposed position from a partial fill.

        If one leg filled and the other didn't, we have directional exposure.
        Options:
        1. Close the position on the same platform (accept loss)
        2. Try to complete the other leg at a worse price
        3. Accept the directional risk (do nothing)

        This implementation attempts to close on the same platform.
        """
        if not execution.needs_hedge:
            return None

        exposed = execution.exposed_leg
        if not exposed:
            return None

        logger.warning(f"Hedging exposure on {exposed.request.platform.value}")

        # Create opposite order to close position
        hedge_order = OrderRequest(
            platform=exposed.request.platform,
            market_id=exposed.request.market_id,
            token_id=exposed.request.token_id,
            side=OrderSide.SELL if exposed.request.side == OrderSide.BUY else OrderSide.BUY,
            price=exposed.request.price,  # Try at same price first
            size=exposed.filled_size,
        )

        if exposed.request.platform == Platform.POLYMARKET:
            return await self.poly_client.submit_order(hedge_order)
        else:
            return await self.kalshi_client.submit_order(hedge_order)

    async def cancel_all_pending(self) -> Dict[str, bool]:
        """Cancel all pending orders on both platforms"""
        results = {}

        for execution in self._executions:
            for leg in [execution.leg1, execution.leg2]:
                if leg.status == OrderStatus.PENDING and leg.order_id:
                    if leg.request.platform == Platform.POLYMARKET:
                        success = await self.poly_client.cancel_order(leg.order_id)
                    else:
                        success = await self.kalshi_client.cancel_order(leg.order_id)
                    results[leg.order_id] = success

        return results

    def get_execution_stats(self) -> Dict[str, Any]:
        """Get statistics on execution performance"""
        if not self._executions:
            return {"count": 0}

        successful = sum(1 for e in self._executions if e.both_filled)
        partial = sum(1 for e in self._executions if e.one_filled)
        failed = sum(1 for e in self._executions if e.none_filled)

        latencies = [e.total_latency_ms for e in self._executions]

        return {
            "count": len(self._executions),
            "successful": successful,
            "partial": partial,
            "failed": failed,
            "success_rate": successful / len(self._executions),
            "avg_latency_ms": sum(latencies) / len(latencies),
            "max_latency_ms": max(latencies),
            "min_latency_ms": min(latencies),
        }


def create_arb_orders_from_opportunity(
    opportunity: Dict[str, Any],
    size: Decimal,
    poly_token_yes: str,
    poly_token_no: str,
    kalshi_ticker: str,
) -> Tuple[OrderRequest, OrderRequest]:
    """
    Convert an ArbitrageOpportunity (from cross_platform.py) into OrderRequests.

    Args:
        opportunity: The arb_opportunity dict from MarketPair
        size: Number of contracts to trade
        poly_token_yes: Polymarket Yes token ID
        poly_token_no: Polymarket No token ID
        kalshi_ticker: Kalshi contract ticker

    Returns:
        (poly_order, kalshi_order) ready for execution
    """
    arb_type = opportunity.get("type", "")

    if arb_type == "yes_poly_no_kalshi":
        # Buy Yes on Poly, Buy No on Kalshi
        poly_order = OrderRequest(
            platform=Platform.POLYMARKET,
            market_id="",  # Set by caller
            token_id=poly_token_yes,
            side=OrderSide.BUY,
            price=Decimal(str(opportunity["poly_yes_price"])),
            size=size,
        )
        kalshi_order = OrderRequest(
            platform=Platform.KALSHI,
            market_id=kalshi_ticker,
            token_id=kalshi_ticker,
            side=OrderSide.BUY,  # Buy No
            price=Decimal(str(opportunity["kalshi_no_price"])),
            size=size,
        )
    elif arb_type == "yes_kalshi_no_poly":
        # Buy Yes on Kalshi, Buy No on Poly
        poly_order = OrderRequest(
            platform=Platform.POLYMARKET,
            market_id="",
            token_id=poly_token_no,
            side=OrderSide.BUY,
            price=Decimal(str(opportunity["poly_no_price"])),
            size=size,
        )
        kalshi_order = OrderRequest(
            platform=Platform.KALSHI,
            market_id=kalshi_ticker,
            token_id=kalshi_ticker,
            side=OrderSide.BUY,  # Buy Yes
            price=Decimal(str(opportunity["kalshi_yes_price"])),
            size=size,
        )
    else:
        raise ValueError(f"Unknown arb type: {arb_type}")

    return poly_order, kalshi_order


# Convenience function for quick execution
async def execute_arb(
    poly_client: PolymarketOrderClient,
    kalshi_client: KalshiOrderClient,
    opportunity: Dict[str, Any],
    size: Decimal,
    poly_token_yes: str,
    poly_token_no: str,
    kalshi_ticker: str,
) -> ArbExecution:
    """
    One-shot arbitrage execution.

    Usage:
        result = await execute_arb(
            poly_client=PolymarketOrderClient(api_key="..."),
            kalshi_client=KalshiOrderClient(email="..."),
            opportunity=market_pair.arb_opportunity,
            size=Decimal("50"),
            poly_token_yes="0x...",
            poly_token_no="0x...",
            kalshi_ticker="KXBTC-25100",
        )
    """
    executor = ArbExecutor(poly_client, kalshi_client)
    poly_order, kalshi_order = create_arb_orders_from_opportunity(
        opportunity, size, poly_token_yes, poly_token_no, kalshi_ticker
    )
    return await executor.execute(poly_order, kalshi_order)
