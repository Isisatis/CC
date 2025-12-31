"""
Real-Time CLI Dashboard

Combines all monitoring components into a clean terminal display.
Supports both ad-hoc queries and live refresh mode.

Output is designed for terminal width of 80+ columns.
"""

import os
import sys
import time
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, Any, List
from dataclasses import dataclass

from .health import HealthMonitor, HealthStatus, ConnectionStatus
from .execution import ExecutionTracker
from .performance import PerformanceTracker


# ANSI color codes
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"

    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"


def colorize(text: str, color: str) -> str:
    """Apply color to text"""
    return f"{color}{text}{Colors.RESET}"


def status_color(status: str) -> str:
    """Get color for status string"""
    status_lower = status.lower()
    if status_lower in ("healthy", "connected", "success", "filled"):
        return Colors.GREEN
    elif status_lower in ("degraded", "partial", "warning"):
        return Colors.YELLOW
    elif status_lower in ("unhealthy", "error", "failed", "disconnected"):
        return Colors.RED
    return Colors.WHITE


def format_currency(value: float, include_sign: bool = False) -> str:
    """Format currency value"""
    if include_sign and value > 0:
        return f"+${value:,.2f}"
    elif value < 0:
        return f"-${abs(value):,.2f}"
    return f"${value:,.2f}"


def format_pct(value: float, include_sign: bool = False) -> str:
    """Format percentage value"""
    if include_sign and value > 0:
        return f"+{value:.2f}%"
    return f"{value:.2f}%"


def format_latency(ms: float) -> str:
    """Format latency value"""
    if ms < 100:
        return colorize(f"{ms:.0f}ms", Colors.GREEN)
    elif ms < 500:
        return colorize(f"{ms:.0f}ms", Colors.YELLOW)
    else:
        return colorize(f"{ms:.0f}ms", Colors.RED)


class Dashboard:
    """
    Terminal dashboard for monitoring the trading system.

    Usage:
        # Create components
        health = HealthMonitor()
        execution = ExecutionTracker()
        performance = PerformanceTracker(starting_balance=Decimal("10000"))

        # Create dashboard
        dashboard = Dashboard(health, execution, performance)

        # Print once
        dashboard.print_summary()

        # Live refresh (blocking)
        await dashboard.run_live(refresh_seconds=1)
    """

    def __init__(
        self,
        health_monitor: Optional[HealthMonitor] = None,
        execution_tracker: Optional[ExecutionTracker] = None,
        performance_tracker: Optional[PerformanceTracker] = None,
    ):
        self.health = health_monitor
        self.execution = execution_tracker
        self.performance = performance_tracker

        self._term_width = self._get_terminal_width()

    def _get_terminal_width(self) -> int:
        """Get terminal width"""
        try:
            return os.get_terminal_size().columns
        except OSError:
            return 80

    def _header(self, title: str) -> str:
        """Create section header"""
        width = min(self._term_width, 80)
        padding = (width - len(title) - 4) // 2
        return f"\n{'─' * padding} {colorize(title, Colors.BOLD + Colors.CYAN)} {'─' * padding}"

    def _separator(self) -> str:
        """Create separator line"""
        return "─" * min(self._term_width, 80)

    def _kv(self, key: str, value: str, key_width: int = 20) -> str:
        """Format key-value pair"""
        return f"  {colorize(key + ':', Colors.DIM):<{key_width + 10}} {value}"

    def render_health(self) -> str:
        """Render health status section"""
        if not self.health:
            return ""

        lines = [self._header("SYSTEM HEALTH")]

        health = self.health.get_system_health()
        status = health["status"]
        status_text = colorize(f" {status.upper()} ", status_color(status) + Colors.BOLD)
        lines.append(f"\n  Overall: {status_text}")

        for platform, data in health.get("platforms", {}).items():
            platform_status = data["status"]
            ws_status = data["websocket"]

            lines.append(f"\n  {colorize(platform.upper(), Colors.BOLD)}")
            lines.append(self._kv(
                "API",
                colorize(platform_status, status_color(platform_status))
            ))
            lines.append(self._kv(
                "WebSocket",
                colorize(ws_status, status_color(ws_status))
            ))

            if data.get("rate_limit_remaining") is not None:
                rl = data["rate_limit_remaining"]
                rl_color = Colors.GREEN if rl > 50 else Colors.YELLOW if rl > 10 else Colors.RED
                lines.append(self._kv("Rate Limit", colorize(str(rl), rl_color)))

        # Latency
        latency = health.get("latency", {})
        if latency:
            lines.append(f"\n  {colorize('LATENCY', Colors.BOLD)}")
            for endpoint, stats in list(latency.items())[:5]:
                if stats:
                    p50 = stats.get("median", 0)
                    p95 = stats.get("p95", 0)
                    lines.append(self._kv(
                        endpoint[:25],
                        f"p50={format_latency(p50)} p95={format_latency(p95)}"
                    ))

        return "\n".join(lines)

    def render_execution(self) -> str:
        """Render execution metrics section"""
        if not self.execution:
            return ""

        lines = [self._header("EXECUTION")]

        stats = self.execution.get_stats()

        # Orders
        orders = stats.get("orders", {})
        lines.append(f"\n  {colorize('ORDERS', Colors.BOLD)}")
        lines.append(self._kv("Total", str(orders.get("total", 0))))
        lines.append(self._kv("Filled", str(orders.get("filled", 0))))
        lines.append(self._kv("Failed", str(orders.get("failed", 0))))

        fill_rate = orders.get("fill_rate", 0) * 100
        fill_color = Colors.GREEN if fill_rate > 90 else Colors.YELLOW if fill_rate > 70 else Colors.RED
        lines.append(self._kv("Fill Rate", colorize(format_pct(fill_rate), fill_color)))

        # Slippage
        slippage = stats.get("slippage_bps", {})
        if slippage:
            lines.append(f"\n  {colorize('SLIPPAGE (bps)', Colors.BOLD)}")
            mean_slip = slippage.get("mean", 0)
            slip_color = Colors.GREEN if mean_slip < 5 else Colors.YELLOW if mean_slip < 20 else Colors.RED
            lines.append(self._kv("Mean", colorize(f"{mean_slip:.1f}", slip_color)))
            lines.append(self._kv("Max", f"{slippage.get('max', 0):.1f}"))

        # Latency
        latency = stats.get("latency_ms", {})
        if latency:
            lines.append(f"\n  {colorize('FILL LATENCY', Colors.BOLD)}")
            lines.append(self._kv("Mean", format_latency(latency.get("mean", 0))))
            lines.append(self._kv("p95", format_latency(latency.get("p95", 0))))

        # Arbs
        arbs = stats.get("arbs", {})
        if arbs.get("total", 0) > 0:
            lines.append(f"\n  {colorize('ARBITRAGE', Colors.BOLD)}")
            lines.append(self._kv("Attempts", str(arbs.get("total", 0))))
            lines.append(self._kv("Successful", str(arbs.get("successful", 0))))
            lines.append(self._kv("Partial", str(arbs.get("partial", 0))))

            success_rate = arbs.get("success_rate", 0) * 100
            sr_color = Colors.GREEN if success_rate > 80 else Colors.YELLOW if success_rate > 50 else Colors.RED
            lines.append(self._kv("Success Rate", colorize(format_pct(success_rate), sr_color)))

        # Open orders
        open_orders = self.execution.get_open_orders()
        if open_orders:
            lines.append(f"\n  {colorize('OPEN ORDERS', Colors.BOLD)}")
            for order in open_orders[:3]:
                lines.append(f"    {order.platform} {order.side.value} @ {order.expected_price}")
            if len(open_orders) > 3:
                lines.append(f"    ... and {len(open_orders) - 3} more")

        return "\n".join(lines)

    def render_performance(self) -> str:
        """Render P&L and performance section"""
        if not self.performance:
            return ""

        lines = [self._header("PERFORMANCE")]

        summary = self.performance.get_session_summary()

        # Session info
        duration = summary.get("duration_minutes", 0)
        lines.append(f"\n  Session: {summary['session_id']}")
        lines.append(self._kv("Duration", f"{duration:.0f} min"))
        lines.append(self._kv("Starting Balance", format_currency(summary["starting_balance"])))

        # P&L
        pnl = summary.get("pnl", {})
        lines.append(f"\n  {colorize('P&L', Colors.BOLD)}")

        total_pnl = pnl.get("total", 0)
        pnl_color = Colors.GREEN if total_pnl > 0 else Colors.RED if total_pnl < 0 else Colors.WHITE
        lines.append(self._kv(
            "Total",
            colorize(format_currency(total_pnl, include_sign=True), pnl_color + Colors.BOLD)
        ))
        lines.append(self._kv("Realized", format_currency(pnl.get("realized", 0), include_sign=True)))
        lines.append(self._kv("Unrealized", format_currency(pnl.get("unrealized", 0), include_sign=True)))
        lines.append(self._kv("Fees", format_currency(pnl.get("fees", 0))))
        lines.append(self._kv("Net", format_currency(pnl.get("net", 0), include_sign=True)))

        # Trades
        trades = summary.get("trades", {})
        lines.append(f"\n  {colorize('TRADES', Colors.BOLD)}")
        lines.append(self._kv("Total", str(trades.get("total", 0))))
        lines.append(self._kv(
            "Win/Loss",
            f"{trades.get('winning', 0)}/{trades.get('losing', 0)}"
        ))

        win_rate = trades.get("win_rate", 0) * 100
        wr_color = Colors.GREEN if win_rate > 60 else Colors.YELLOW if win_rate > 40 else Colors.RED
        lines.append(self._kv("Win Rate", colorize(format_pct(win_rate), wr_color)))
        lines.append(self._kv("Avg P&L/Trade", format_currency(trades.get("avg_pnl", 0), include_sign=True)))

        # Metrics
        metrics = summary.get("metrics", {})
        lines.append(f"\n  {colorize('METRICS', Colors.BOLD)}")

        return_pct = metrics.get("return_pct", 0)
        ret_color = Colors.GREEN if return_pct > 0 else Colors.RED if return_pct < 0 else Colors.WHITE
        lines.append(self._kv("Return", colorize(format_pct(return_pct, include_sign=True), ret_color)))

        pf = metrics.get("profit_factor")
        if pf is not None:
            pf_color = Colors.GREEN if pf > 1.5 else Colors.YELLOW if pf > 1 else Colors.RED
            lines.append(self._kv("Profit Factor", colorize(f"{pf:.2f}", pf_color)))

        sharpe = metrics.get("sharpe")
        if sharpe is not None:
            sharpe_color = Colors.GREEN if sharpe > 1 else Colors.YELLOW if sharpe > 0 else Colors.RED
            lines.append(self._kv("Sharpe", colorize(f"{sharpe:.2f}", sharpe_color)))

        # Positions
        positions = summary.get("positions", {})
        lines.append(f"\n  {colorize('POSITIONS', Colors.BOLD)}")
        lines.append(self._kv("Open", str(positions.get("open", 0))))
        lines.append(self._kv("Exposure", format_currency(positions.get("total_exposure", 0))))

        # Balances
        balances = summary.get("balances", {})
        if balances:
            lines.append(f"\n  {colorize('BALANCES', Colors.BOLD)}")
            for platform, balance in balances.items():
                lines.append(self._kv(platform.capitalize(), format_currency(balance)))

        return "\n".join(lines)

    def render_positions(self) -> str:
        """Render open positions table"""
        if not self.performance:
            return ""

        positions = self.performance.get_positions()
        if not positions:
            return ""

        lines = [self._header("OPEN POSITIONS")]

        # Header
        lines.append(f"\n  {'Platform':<12} {'Market':<20} {'Side':<6} {'Qty':>8} {'Entry':>8} {'Current':>8} {'P&L':>12}")
        lines.append(f"  {'-'*12} {'-'*20} {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*12}")

        for pos in positions:
            pnl = pos.unrealized_pnl
            pnl_str = format_currency(float(pnl), include_sign=True) if pnl else "N/A"
            pnl_color = Colors.GREEN if pnl and pnl > 0 else Colors.RED if pnl and pnl < 0 else Colors.WHITE

            current = f"{pos.current_price:.4f}" if pos.current_price else "N/A"

            lines.append(
                f"  {pos.platform:<12} "
                f"{pos.market_id[:20]:<20} "
                f"{pos.outcome:<6} "
                f"{float(pos.quantity):>8.2f} "
                f"{float(pos.avg_entry_price):>8.4f} "
                f"{current:>8} "
                f"{colorize(pnl_str, pnl_color):>12}"
            )

        return "\n".join(lines)

    def render_recent_trades(self, limit: int = 5) -> str:
        """Render recent trades"""
        if not self.performance:
            return ""

        trades = self.performance.get_trade_history(limit=limit)
        if not trades:
            return ""

        lines = [self._header("RECENT TRADES")]

        for trade in reversed(trades):
            side_color = Colors.GREEN if trade["side"] == "buy" else Colors.RED
            pnl = trade.get("realized_pnl")
            pnl_str = ""
            if pnl is not None:
                pnl_str = colorize(
                    format_currency(pnl, include_sign=True),
                    Colors.GREEN if pnl > 0 else Colors.RED
                )

            lines.append(
                f"  {trade['timestamp'][11:19]} "
                f"{trade['platform']:<10} "
                f"{colorize(trade['side'].upper(), side_color):<4} "
                f"{trade['quantity']:.2f} @ {trade['price']:.4f} "
                f"{pnl_str}"
            )

        return "\n".join(lines)

    def print_summary(self):
        """Print full dashboard summary"""
        sections = [
            f"\n{colorize('═' * 60, Colors.CYAN)}",
            colorize("  TRADING SYSTEM DASHBOARD", Colors.BOLD + Colors.CYAN),
            colorize(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC", Colors.DIM),
            colorize('═' * 60, Colors.CYAN),
            self.render_health(),
            self.render_execution(),
            self.render_performance(),
            self.render_positions(),
            self.render_recent_trades(),
            f"\n{self._separator()}\n",
        ]

        print("\n".join(filter(None, sections)))

    def clear_screen(self):
        """Clear terminal screen"""
        os.system('cls' if os.name == 'nt' else 'clear')

    async def run_live(self, refresh_seconds: float = 1.0):
        """Run live dashboard with auto-refresh"""
        try:
            while True:
                self.clear_screen()
                self.print_summary()
                print(colorize(f"\n  Refreshing every {refresh_seconds}s... (Ctrl+C to exit)", Colors.DIM))
                await asyncio.sleep(refresh_seconds)
        except KeyboardInterrupt:
            print("\n\nDashboard stopped.")


def create_dashboard(
    health_monitor: Optional[HealthMonitor] = None,
    execution_tracker: Optional[ExecutionTracker] = None,
    performance_tracker: Optional[PerformanceTracker] = None,
) -> Dashboard:
    """Factory function to create dashboard with optional components"""
    return Dashboard(health_monitor, execution_tracker, performance_tracker)
