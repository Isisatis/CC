"""
Performance & P&L Tracking

Tracks trading session performance:
- Real-time P&L (unrealized + realized)
- Position tracking
- Session statistics
- Return metrics (Sharpe-like, win rate, etc.)
"""

import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from threading import Lock
import statistics
import math

from .metrics import MetricsRegistry, Gauge, Counter


@dataclass
class Position:
    """Current position in a market"""
    platform: str
    market_id: str
    token_id: str
    outcome: str  # "yes" or "no"

    quantity: Decimal = Decimal("0")
    avg_entry_price: Decimal = Decimal("0")
    current_price: Optional[Decimal] = None

    # For tracking
    opened_at: datetime = field(default_factory=datetime.utcnow)
    last_updated: datetime = field(default_factory=datetime.utcnow)

    @property
    def cost_basis(self) -> Decimal:
        """Total cost to acquire position"""
        return self.quantity * self.avg_entry_price

    @property
    def market_value(self) -> Optional[Decimal]:
        """Current market value"""
        if self.current_price is None:
            return None
        return self.quantity * self.current_price

    @property
    def unrealized_pnl(self) -> Optional[Decimal]:
        """Unrealized P&L"""
        mv = self.market_value
        if mv is None:
            return None
        return mv - self.cost_basis

    @property
    def unrealized_pnl_pct(self) -> Optional[float]:
        """Unrealized P&L as percentage"""
        if self.cost_basis == 0:
            return None
        pnl = self.unrealized_pnl
        if pnl is None:
            return None
        return float(pnl / self.cost_basis) * 100

    def add(self, quantity: Decimal, price: Decimal):
        """Add to position"""
        total_cost = self.cost_basis + (quantity * price)
        self.quantity += quantity
        if self.quantity > 0:
            self.avg_entry_price = total_cost / self.quantity
        self.last_updated = datetime.utcnow()

    def reduce(self, quantity: Decimal) -> Decimal:
        """Reduce position, return realized P&L"""
        if quantity > self.quantity:
            quantity = self.quantity

        realized = Decimal("0")
        if self.current_price is not None:
            realized = quantity * (self.current_price - self.avg_entry_price)

        self.quantity -= quantity
        self.last_updated = datetime.utcnow()

        return realized


@dataclass
class Trade:
    """Record of a completed trade"""
    trade_id: str
    platform: str
    market_id: str
    token_id: str
    outcome: str

    side: str  # "buy" or "sell"
    quantity: Decimal
    price: Decimal
    fees: Decimal = Decimal("0")

    timestamp: datetime = field(default_factory=datetime.utcnow)
    realized_pnl: Optional[Decimal] = None

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.price

    @property
    def net_notional(self) -> Decimal:
        """Notional after fees"""
        if self.side == "buy":
            return self.notional + self.fees
        else:
            return self.notional - self.fees


@dataclass
class SessionStats:
    """Statistics for a trading session"""
    session_id: str
    started_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None

    # Starting capital
    starting_balance: Decimal = Decimal("0")

    # Cumulative metrics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0

    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")

    # Volume
    total_volume: Decimal = Decimal("0")
    total_notional: Decimal = Decimal("0")

    # For Sharpe calculation
    pnl_history: List[Tuple[datetime, Decimal]] = field(default_factory=list)

    @property
    def total_pnl(self) -> Decimal:
        return self.realized_pnl + self.unrealized_pnl

    @property
    def net_pnl(self) -> Decimal:
        """P&L after fees"""
        return self.total_pnl - self.total_fees

    @property
    def win_rate(self) -> float:
        """Percentage of winning trades"""
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    @property
    def return_pct(self) -> float:
        """Return as percentage of starting balance"""
        if self.starting_balance == 0:
            return 0.0
        return float(self.net_pnl / self.starting_balance) * 100

    @property
    def profit_factor(self) -> Optional[float]:
        """Gross profit / Gross loss"""
        gross_profit = sum(p for _, p in self.pnl_history if p > 0)
        gross_loss = abs(sum(p for _, p in self.pnl_history if p < 0))
        if gross_loss == 0:
            return None
        return float(gross_profit / gross_loss)

    @property
    def avg_trade_pnl(self) -> Decimal:
        """Average P&L per trade"""
        if self.total_trades == 0:
            return Decimal("0")
        return self.realized_pnl / self.total_trades

    def compute_sharpe(self, risk_free_rate: float = 0.0, annualize: bool = True) -> Optional[float]:
        """
        Compute Sharpe-like ratio from P&L history.

        For prediction market arbs, we measure return per unit of volatility.
        Not a true Sharpe since we're not measuring against a benchmark,
        but useful for comparing strategy performance.
        """
        if len(self.pnl_history) < 2:
            return None

        returns = [float(p) for _, p in self.pnl_history]

        mean_return = statistics.mean(returns)
        std_return = statistics.stdev(returns)

        if std_return == 0:
            return None

        sharpe = (mean_return - risk_free_rate) / std_return

        if annualize and self.started_at:
            duration = datetime.utcnow() - self.started_at
            trades_per_day = len(returns) / max(duration.days, 1)
            sharpe *= math.sqrt(trades_per_day * 252)  # Annualize assuming 252 trading days

        return sharpe


class PerformanceTracker:
    """
    Tracks session performance and P&L.

    Usage:
        tracker = PerformanceTracker(starting_balance=Decimal("10000"))

        # Record trades
        tracker.record_trade(Trade(
            trade_id="t1",
            platform="polymarket",
            market_id="0x...",
            token_id="0x...",
            outcome="yes",
            side="buy",
            quantity=Decimal("100"),
            price=Decimal("0.45"),
        ))

        # Update position prices
        tracker.update_price("polymarket", "0x...", "0x...", Decimal("0.52"))

        # Get P&L
        pnl = tracker.get_pnl()
        print(f"Total P&L: ${pnl['total']}")

        # Get session summary
        summary = tracker.get_session_summary()
    """

    def __init__(
        self,
        starting_balance: Decimal = Decimal("0"),
        session_id: Optional[str] = None,
        metrics_registry: Optional[MetricsRegistry] = None,
    ):
        self.registry = metrics_registry or MetricsRegistry()

        self._session = SessionStats(
            session_id=session_id or f"session_{int(time.time())}",
            starting_balance=starting_balance,
        )

        self._positions: Dict[str, Position] = {}  # key = platform:market:token
        self._trades: List[Trade] = []

        # Per-platform balances
        self._balances: Dict[str, Decimal] = {}

        self._lock = Lock()

        # Metrics
        self._realized_pnl = self.registry.gauge("realized_pnl")
        self._unrealized_pnl = self.registry.gauge("unrealized_pnl")
        self._total_pnl = self.registry.gauge("total_pnl")
        self._trade_count = self.registry.counter("trade_count")
        self._win_count = self.registry.counter("winning_trades")
        self._loss_count = self.registry.counter("losing_trades")

    def _position_key(self, platform: str, market_id: str, token_id: str) -> str:
        return f"{platform}:{market_id}:{token_id}"

    def set_balance(self, platform: str, balance: Decimal):
        """Set account balance for a platform"""
        with self._lock:
            self._balances[platform] = balance

    def get_balance(self, platform: str) -> Decimal:
        """Get account balance for a platform"""
        return self._balances.get(platform, Decimal("0"))

    def record_trade(self, trade: Trade):
        """Record a completed trade"""
        with self._lock:
            self._trades.append(trade)
            self._session.total_trades += 1
            self._session.total_notional += trade.notional
            self._session.total_fees += trade.fees

            self._trade_count.inc()

            # Update position
            key = self._position_key(trade.platform, trade.market_id, trade.token_id)

            if key not in self._positions:
                self._positions[key] = Position(
                    platform=trade.platform,
                    market_id=trade.market_id,
                    token_id=trade.token_id,
                    outcome=trade.outcome,
                )

            pos = self._positions[key]

            if trade.side == "buy":
                pos.add(trade.quantity, trade.price)
            else:
                # Realize P&L on sell
                pos.current_price = trade.price
                realized = pos.reduce(trade.quantity)
                trade.realized_pnl = realized
                self._session.realized_pnl += realized

                # Track wins/losses
                if realized > 0:
                    self._session.winning_trades += 1
                    self._win_count.inc()
                elif realized < 0:
                    self._session.losing_trades += 1
                    self._loss_count.inc()

                # Record for Sharpe
                self._session.pnl_history.append((datetime.utcnow(), realized))

            # Clean up empty positions
            if pos.quantity == 0:
                del self._positions[key]

            # Update metrics
            self._realized_pnl.set(float(self._session.realized_pnl))

    def update_price(
        self,
        platform: str,
        market_id: str,
        token_id: str,
        current_price: Decimal,
    ):
        """Update current price for a position"""
        key = self._position_key(platform, market_id, token_id)
        with self._lock:
            if key in self._positions:
                self._positions[key].current_price = current_price
                self._update_unrealized_pnl()

    def _update_unrealized_pnl(self):
        """Recalculate total unrealized P&L"""
        total_unrealized = Decimal("0")
        for pos in self._positions.values():
            if pos.unrealized_pnl is not None:
                total_unrealized += pos.unrealized_pnl

        self._session.unrealized_pnl = total_unrealized
        self._unrealized_pnl.set(float(total_unrealized))
        self._total_pnl.set(float(self._session.total_pnl))

    def get_positions(self) -> List[Position]:
        """Get all open positions"""
        return list(self._positions.values())

    def get_position(
        self,
        platform: str,
        market_id: str,
        token_id: str,
    ) -> Optional[Position]:
        """Get specific position"""
        key = self._position_key(platform, market_id, token_id)
        return self._positions.get(key)

    def get_pnl(self) -> Dict[str, Any]:
        """Get current P&L summary"""
        with self._lock:
            return {
                "realized": float(self._session.realized_pnl),
                "unrealized": float(self._session.unrealized_pnl),
                "total": float(self._session.total_pnl),
                "fees": float(self._session.total_fees),
                "net": float(self._session.net_pnl),
            }

    def get_session_summary(self) -> Dict[str, Any]:
        """Get full session summary"""
        with self._lock:
            session = self._session
            return {
                "session_id": session.session_id,
                "started_at": session.started_at.isoformat(),
                "duration_minutes": (datetime.utcnow() - session.started_at).total_seconds() / 60,
                "starting_balance": float(session.starting_balance),

                "pnl": {
                    "realized": float(session.realized_pnl),
                    "unrealized": float(session.unrealized_pnl),
                    "total": float(session.total_pnl),
                    "net": float(session.net_pnl),
                    "fees": float(session.total_fees),
                },

                "trades": {
                    "total": session.total_trades,
                    "winning": session.winning_trades,
                    "losing": session.losing_trades,
                    "win_rate": session.win_rate,
                    "avg_pnl": float(session.avg_trade_pnl),
                },

                "metrics": {
                    "return_pct": session.return_pct,
                    "profit_factor": session.profit_factor,
                    "sharpe": session.compute_sharpe(),
                },

                "volume": {
                    "total_notional": float(session.total_notional),
                },

                "positions": {
                    "open": len(self._positions),
                    "total_exposure": float(sum(
                        p.cost_basis for p in self._positions.values()
                    )),
                },

                "balances": {
                    platform: float(balance)
                    for platform, balance in self._balances.items()
                },
            }

    def get_trade_history(
        self,
        limit: int = 50,
        platform: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get recent trade history"""
        trades = list(self._trades)
        if platform:
            trades = [t for t in trades if t.platform == platform]

        return [
            {
                "trade_id": t.trade_id,
                "platform": t.platform,
                "market_id": t.market_id,
                "outcome": t.outcome,
                "side": t.side,
                "quantity": float(t.quantity),
                "price": float(t.price),
                "notional": float(t.notional),
                "fees": float(t.fees),
                "realized_pnl": float(t.realized_pnl) if t.realized_pnl else None,
                "timestamp": t.timestamp.isoformat(),
            }
            for t in trades[-limit:]
        ]

    def export_session(self) -> Dict[str, Any]:
        """Export full session data for persistence"""
        return {
            "session": self.get_session_summary(),
            "positions": [
                {
                    "platform": p.platform,
                    "market_id": p.market_id,
                    "token_id": p.token_id,
                    "outcome": p.outcome,
                    "quantity": float(p.quantity),
                    "avg_entry_price": float(p.avg_entry_price),
                    "current_price": float(p.current_price) if p.current_price else None,
                    "cost_basis": float(p.cost_basis),
                    "unrealized_pnl": float(p.unrealized_pnl) if p.unrealized_pnl else None,
                }
                for p in self._positions.values()
            ],
            "trades": self.get_trade_history(limit=1000),
        }

    def reset_session(self, starting_balance: Optional[Decimal] = None):
        """Reset session statistics"""
        with self._lock:
            old_session = self._session
            self._session = SessionStats(
                session_id=f"session_{int(time.time())}",
                starting_balance=starting_balance or old_session.starting_balance,
            )
            self._positions.clear()
            self._trades.clear()
