"""
Execution Metrics Tracking

Tracks trade execution quality:
- Fill rates
- Slippage (expected vs actual price)
- Execution latency
- Partial fills
- Arb execution success rate
"""

import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from threading import Lock
import statistics

from .metrics import MetricsRegistry, Counter, Gauge, Histogram


class ExecutionSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class ExecutionStatus(str, Enum):
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass
class ExecutionRecord:
    """Record of a single order execution"""
    order_id: str
    platform: str
    market_id: str
    side: ExecutionSide
    status: ExecutionStatus

    # Pricing
    expected_price: Decimal
    filled_price: Optional[Decimal] = None

    # Size
    requested_size: Decimal = Decimal("0")
    filled_size: Decimal = Decimal("0")

    # Timing
    submitted_at: datetime = field(default_factory=datetime.utcnow)
    filled_at: Optional[datetime] = None
    latency_ms: float = 0.0

    # Context
    is_arb_leg: bool = False
    arb_id: Optional[str] = None
    labels: Dict[str, str] = field(default_factory=dict)

    @property
    def slippage_bps(self) -> Optional[float]:
        """Slippage in basis points (negative = better than expected)"""
        if not self.filled_price or not self.expected_price:
            return None

        if self.side == ExecutionSide.BUY:
            # For buys, higher fill price = negative slippage
            slippage = (float(self.filled_price) - float(self.expected_price)) / float(self.expected_price)
        else:
            # For sells, lower fill price = negative slippage
            slippage = (float(self.expected_price) - float(self.filled_price)) / float(self.expected_price)

        return slippage * 10000  # Convert to bps

    @property
    def fill_rate(self) -> float:
        """Percentage of order filled"""
        if self.requested_size == 0:
            return 0.0
        return float(self.filled_size / self.requested_size)

    @property
    def notional(self) -> Decimal:
        """Filled notional value"""
        if self.filled_price:
            return self.filled_size * self.filled_price
        return Decimal("0")


@dataclass
class ArbExecutionRecord:
    """Record of a full arbitrage execution (both legs)"""
    arb_id: str
    leg1: ExecutionRecord
    leg2: ExecutionRecord

    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    expected_profit: Decimal = Decimal("0")

    @property
    def both_filled(self) -> bool:
        return (
            self.leg1.status == ExecutionStatus.FILLED
            and self.leg2.status == ExecutionStatus.FILLED
        )

    @property
    def partial(self) -> bool:
        return (
            (self.leg1.status == ExecutionStatus.FILLED) !=
            (self.leg2.status == ExecutionStatus.FILLED)
        )

    @property
    def total_latency_ms(self) -> float:
        return max(self.leg1.latency_ms, self.leg2.latency_ms)

    @property
    def realized_profit(self) -> Optional[Decimal]:
        """Calculate actual profit from fills"""
        if not self.both_filled:
            return None

        # For arbs: cost = price_leg1 + price_leg2, payout = $1
        # profit = 1 - cost
        if self.leg1.filled_price and self.leg2.filled_price:
            cost = self.leg1.filled_price + self.leg2.filled_price
            return Decimal("1") - cost
        return None

    @property
    def profit_vs_expected(self) -> Optional[float]:
        """How much we deviated from expected profit"""
        realized = self.realized_profit
        if realized is None or self.expected_profit == 0:
            return None
        return float((realized - self.expected_profit) / self.expected_profit)


class ExecutionTracker:
    """
    Tracks execution quality across all trades.

    Usage:
        tracker = ExecutionTracker()

        # Record an execution
        record = tracker.record_execution(
            order_id="123",
            platform="polymarket",
            market_id="0xabc",
            side=ExecutionSide.BUY,
            expected_price=Decimal("0.45"),
            requested_size=Decimal("100"),
        )

        # Update when filled
        tracker.update_execution(
            order_id="123",
            status=ExecutionStatus.FILLED,
            filled_price=Decimal("0.46"),
            filled_size=Decimal("100"),
            latency_ms=45.2,
        )

        # Get stats
        stats = tracker.get_stats()
    """

    def __init__(self, metrics_registry: Optional[MetricsRegistry] = None):
        self.registry = metrics_registry or MetricsRegistry()

        self._executions: Dict[str, ExecutionRecord] = {}
        self._arbs: Dict[str, ArbExecutionRecord] = {}
        self._history: List[ExecutionRecord] = []
        self._arb_history: List[ArbExecutionRecord] = []
        self._max_history = 1000

        self._lock = Lock()

        # Metrics
        self._orders_submitted = self.registry.counter("orders_submitted_total")
        self._orders_filled = self.registry.counter("orders_filled_total")
        self._orders_failed = self.registry.counter("orders_failed_total")
        self._fill_latency = self.registry.histogram("order_fill_latency_ms")
        self._slippage = self.registry.histogram("order_slippage_bps")
        self._fill_rate = self.registry.histogram("order_fill_rate_pct")

        self._arb_attempts = self.registry.counter("arb_attempts_total")
        self._arb_success = self.registry.counter("arb_success_total")
        self._arb_partial = self.registry.counter("arb_partial_total")
        self._arb_profit = self.registry.histogram("arb_realized_profit")

    def record_execution(
        self,
        order_id: str,
        platform: str,
        market_id: str,
        side: ExecutionSide,
        expected_price: Decimal,
        requested_size: Decimal,
        is_arb_leg: bool = False,
        arb_id: Optional[str] = None,
        labels: Optional[Dict[str, str]] = None,
    ) -> ExecutionRecord:
        """Record a new order submission"""
        record = ExecutionRecord(
            order_id=order_id,
            platform=platform,
            market_id=market_id,
            side=side,
            status=ExecutionStatus.SUBMITTED,
            expected_price=expected_price,
            requested_size=requested_size,
            is_arb_leg=is_arb_leg,
            arb_id=arb_id,
            labels=labels or {},
        )

        with self._lock:
            self._executions[order_id] = record
            self._orders_submitted.inc(labels={"platform": platform})

        return record

    def update_execution(
        self,
        order_id: str,
        status: ExecutionStatus,
        filled_price: Optional[Decimal] = None,
        filled_size: Optional[Decimal] = None,
        latency_ms: Optional[float] = None,
    ) -> Optional[ExecutionRecord]:
        """Update an existing execution record"""
        with self._lock:
            record = self._executions.get(order_id)
            if not record:
                return None

            record.status = status

            if filled_price is not None:
                record.filled_price = filled_price
            if filled_size is not None:
                record.filled_size = filled_size
            if latency_ms is not None:
                record.latency_ms = latency_ms

            if status in (ExecutionStatus.FILLED, ExecutionStatus.PARTIAL):
                record.filled_at = datetime.utcnow()

                # Record metrics
                self._orders_filled.inc(labels={"platform": record.platform})
                if latency_ms:
                    self._fill_latency.observe(latency_ms, labels={"platform": record.platform})
                if record.slippage_bps is not None:
                    self._slippage.observe(record.slippage_bps, labels={"platform": record.platform})
                self._fill_rate.observe(record.fill_rate * 100)

            elif status in (ExecutionStatus.FAILED, ExecutionStatus.REJECTED):
                self._orders_failed.inc(labels={"platform": record.platform})

            # Move to history if terminal
            if status in (ExecutionStatus.FILLED, ExecutionStatus.CANCELLED, ExecutionStatus.REJECTED, ExecutionStatus.FAILED):
                self._history.append(record)
                del self._executions[order_id]
                if len(self._history) > self._max_history:
                    self._history.pop(0)

            return record

    def record_arb_execution(
        self,
        arb_id: str,
        leg1: ExecutionRecord,
        leg2: ExecutionRecord,
        expected_profit: Decimal,
    ) -> ArbExecutionRecord:
        """Record an arbitrage execution attempt"""
        arb = ArbExecutionRecord(
            arb_id=arb_id,
            leg1=leg1,
            leg2=leg2,
            expected_profit=expected_profit,
        )

        with self._lock:
            self._arbs[arb_id] = arb
            self._arb_attempts.inc()

        return arb

    def update_arb_execution(self, arb_id: str) -> Optional[ArbExecutionRecord]:
        """Update arb record after legs complete"""
        with self._lock:
            arb = self._arbs.get(arb_id)
            if not arb:
                return None

            # Check if complete
            leg1_terminal = arb.leg1.status in (ExecutionStatus.FILLED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED)
            leg2_terminal = arb.leg2.status in (ExecutionStatus.FILLED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED)

            if leg1_terminal and leg2_terminal:
                arb.completed_at = datetime.utcnow()

                if arb.both_filled:
                    self._arb_success.inc()
                    if arb.realized_profit is not None:
                        self._arb_profit.observe(float(arb.realized_profit))
                elif arb.partial:
                    self._arb_partial.inc()

                # Move to history
                self._arb_history.append(arb)
                del self._arbs[arb_id]
                if len(self._arb_history) > self._max_history:
                    self._arb_history.pop(0)

            return arb

    def get_execution(self, order_id: str) -> Optional[ExecutionRecord]:
        """Get execution record by ID"""
        return self._executions.get(order_id)

    def get_arb(self, arb_id: str) -> Optional[ArbExecutionRecord]:
        """Get arb record by ID"""
        return self._arbs.get(arb_id)

    def get_stats(
        self,
        since: Optional[datetime] = None,
        platform: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get execution statistics"""
        with self._lock:
            executions = list(self._history)
            arbs = list(self._arb_history)

        if since:
            executions = [e for e in executions if e.submitted_at >= since]
            arbs = [a for a in arbs if a.started_at >= since]

        if platform:
            executions = [e for e in executions if e.platform == platform]

        # Calculate stats
        total_orders = len(executions)
        filled_orders = [e for e in executions if e.status == ExecutionStatus.FILLED]
        failed_orders = [e for e in executions if e.status in (ExecutionStatus.FAILED, ExecutionStatus.REJECTED)]

        slippages = [e.slippage_bps for e in filled_orders if e.slippage_bps is not None]
        latencies = [e.latency_ms for e in filled_orders if e.latency_ms > 0]
        fill_rates = [e.fill_rate for e in executions if e.fill_rate > 0]

        # Arb stats
        total_arbs = len(arbs)
        successful_arbs = [a for a in arbs if a.both_filled]
        partial_arbs = [a for a in arbs if a.partial]
        arb_profits = [float(a.realized_profit) for a in successful_arbs if a.realized_profit is not None]

        return {
            "orders": {
                "total": total_orders,
                "filled": len(filled_orders),
                "failed": len(failed_orders),
                "fill_rate": len(filled_orders) / total_orders if total_orders > 0 else 0,
            },
            "slippage_bps": {
                "mean": statistics.mean(slippages) if slippages else 0,
                "median": statistics.median(slippages) if slippages else 0,
                "max": max(slippages) if slippages else 0,
                "min": min(slippages) if slippages else 0,
            } if slippages else {},
            "latency_ms": {
                "mean": statistics.mean(latencies) if latencies else 0,
                "median": statistics.median(latencies) if latencies else 0,
                "p95": sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) >= 20 else (max(latencies) if latencies else 0),
            } if latencies else {},
            "arbs": {
                "total": total_arbs,
                "successful": len(successful_arbs),
                "partial": len(partial_arbs),
                "success_rate": len(successful_arbs) / total_arbs if total_arbs > 0 else 0,
            },
            "arb_profits": {
                "total": sum(arb_profits) if arb_profits else 0,
                "mean": statistics.mean(arb_profits) if arb_profits else 0,
                "min": min(arb_profits) if arb_profits else 0,
                "max": max(arb_profits) if arb_profits else 0,
            } if arb_profits else {},
        }

    def get_open_orders(self) -> List[ExecutionRecord]:
        """Get currently open orders"""
        return list(self._executions.values())

    def get_open_arbs(self) -> List[ArbExecutionRecord]:
        """Get currently open arb executions"""
        return list(self._arbs.values())

    def get_recent_executions(self, limit: int = 20) -> List[ExecutionRecord]:
        """Get recent execution history"""
        return self._history[-limit:]

    def get_recent_arbs(self, limit: int = 20) -> List[ArbExecutionRecord]:
        """Get recent arb history"""
        return self._arb_history[-limit:]
