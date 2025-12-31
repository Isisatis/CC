"""
Monitoring Module

Real-time monitoring for the trading system:
- System health (connections, latency)
- Execution quality (fills, slippage)
- Performance tracking (P&L, positions)
- CLI dashboard
"""

from .metrics import (
    MetricType,
    MetricPoint,
    Metric,
    Counter,
    Gauge,
    Histogram,
    Timer,
    MetricsRegistry,
    default_registry,
    counter,
    gauge,
    histogram,
    timer,
)

from .health import (
    ConnectionStatus,
    HealthStatus,
    EndpointHealth,
    PlatformHealth,
    LatencyTracker,
    HealthMonitor,
    ConnectionProbe,
)

from .execution import (
    ExecutionSide,
    ExecutionStatus,
    ExecutionRecord,
    ArbExecutionRecord,
    ExecutionTracker,
)

from .performance import (
    Position,
    Trade,
    SessionStats,
    PerformanceTracker,
)

from .dashboard import (
    Dashboard,
    create_dashboard,
)

__all__ = [
    # Metrics
    "MetricType",
    "MetricPoint",
    "Metric",
    "Counter",
    "Gauge",
    "Histogram",
    "Timer",
    "MetricsRegistry",
    "default_registry",
    "counter",
    "gauge",
    "histogram",
    "timer",

    # Health
    "ConnectionStatus",
    "HealthStatus",
    "EndpointHealth",
    "PlatformHealth",
    "LatencyTracker",
    "HealthMonitor",
    "ConnectionProbe",

    # Execution
    "ExecutionSide",
    "ExecutionStatus",
    "ExecutionRecord",
    "ArbExecutionRecord",
    "ExecutionTracker",

    # Performance
    "Position",
    "Trade",
    "SessionStats",
    "PerformanceTracker",

    # Dashboard
    "Dashboard",
    "create_dashboard",
]
