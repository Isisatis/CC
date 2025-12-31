"""
Core Metrics Collection

Low-overhead metrics collection for real-time monitoring.
Stores time-series data with configurable retention.

Metric Types:
- Counter: Monotonically increasing (trades, errors)
- Gauge: Point-in-time value (price, balance)
- Histogram: Distribution of values (latency, slippage)
- Timer: Duration tracking (execution time)
"""

import time
import threading
from threading import RLock
from enum import Enum
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from collections import deque
import statistics


class MetricType(str, Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    TIMER = "timer"


@dataclass
class MetricPoint:
    """Single metric observation"""
    timestamp: float  # Unix timestamp
    value: float
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class HistogramBuckets:
    """Pre-defined histogram buckets for common use cases"""
    # Latency buckets (milliseconds)
    LATENCY_MS = [1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
    # Slippage buckets (basis points)
    SLIPPAGE_BPS = [1, 5, 10, 25, 50, 100, 200, 500]
    # Order size buckets (USD)
    ORDER_SIZE = [10, 50, 100, 500, 1000, 5000, 10000, 50000]


class Metric:
    """Base metric with time-series storage"""

    def __init__(
        self,
        name: str,
        metric_type: MetricType,
        description: str = "",
        retention_seconds: int = 3600,  # 1 hour default
        labels: Optional[List[str]] = None,
    ):
        self.name = name
        self.type = metric_type
        self.description = description
        self.retention_seconds = retention_seconds
        self.label_names = labels or []

        self._points: deque = deque()
        self._lock = RLock()  # Reentrant lock for nested calls

    def _prune_old(self):
        """Remove points older than retention period"""
        cutoff = time.time() - self.retention_seconds
        while self._points and self._points[0].timestamp < cutoff:
            self._points.popleft()

    def record(self, value: float, labels: Optional[Dict[str, str]] = None):
        """Record a metric observation"""
        with self._lock:
            self._prune_old()
            self._points.append(MetricPoint(
                timestamp=time.time(),
                value=value,
                labels=labels or {},
            ))

    def get_points(
        self,
        since: Optional[float] = None,
        labels: Optional[Dict[str, str]] = None,
    ) -> List[MetricPoint]:
        """Get metric points, optionally filtered"""
        with self._lock:
            self._prune_old()
            points = list(self._points)

        if since:
            points = [p for p in points if p.timestamp >= since]

        if labels:
            points = [
                p for p in points
                if all(p.labels.get(k) == v for k, v in labels.items())
            ]

        return points

    def get_latest(self, labels: Optional[Dict[str, str]] = None) -> Optional[MetricPoint]:
        """Get most recent point"""
        points = self.get_points(labels=labels)
        return points[-1] if points else None


class Counter(Metric):
    """Monotonically increasing counter"""

    def __init__(self, name: str, description: str = "", **kwargs):
        super().__init__(name, MetricType.COUNTER, description, **kwargs)
        self._values: Dict[str, float] = {}  # label_key -> count

    def _label_key(self, labels: Optional[Dict[str, str]]) -> str:
        if not labels:
            return ""
        return "|".join(f"{k}={v}" for k, v in sorted(labels.items()))

    def inc(self, value: float = 1, labels: Optional[Dict[str, str]] = None):
        """Increment counter"""
        key = self._label_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0) + value
            self.record(self._values[key], labels)

    def get(self, labels: Optional[Dict[str, str]] = None) -> float:
        """Get current counter value"""
        key = self._label_key(labels)
        return self._values.get(key, 0)


class Gauge(Metric):
    """Point-in-time value"""

    def __init__(self, name: str, description: str = "", **kwargs):
        super().__init__(name, MetricType.GAUGE, description, **kwargs)

    def set(self, value: float, labels: Optional[Dict[str, str]] = None):
        """Set gauge value"""
        self.record(value, labels)

    def get(self, labels: Optional[Dict[str, str]] = None) -> Optional[float]:
        """Get current gauge value"""
        latest = self.get_latest(labels)
        return latest.value if latest else None


class Histogram(Metric):
    """Distribution of values with percentile calculation"""

    def __init__(
        self,
        name: str,
        description: str = "",
        buckets: Optional[List[float]] = None,
        **kwargs
    ):
        super().__init__(name, MetricType.HISTOGRAM, description, **kwargs)
        self.buckets = sorted(buckets or HistogramBuckets.LATENCY_MS)

    def observe(self, value: float, labels: Optional[Dict[str, str]] = None):
        """Record an observation"""
        self.record(value, labels)

    def get_percentile(
        self,
        percentile: float,
        since: Optional[float] = None,
        labels: Optional[Dict[str, str]] = None,
    ) -> Optional[float]:
        """Get percentile value (0-100)"""
        points = self.get_points(since=since, labels=labels)
        if not points:
            return None
        values = sorted(p.value for p in points)
        idx = int(len(values) * percentile / 100)
        return values[min(idx, len(values) - 1)]

    def get_stats(
        self,
        since: Optional[float] = None,
        labels: Optional[Dict[str, str]] = None,
    ) -> Dict[str, float]:
        """Get distribution statistics"""
        points = self.get_points(since=since, labels=labels)
        if not points:
            return {}

        values = [p.value for p in points]
        return {
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "mean": statistics.mean(values),
            "median": statistics.median(values),
            "stddev": statistics.stdev(values) if len(values) > 1 else 0,
            "p50": self.get_percentile(50, since, labels),
            "p90": self.get_percentile(90, since, labels),
            "p99": self.get_percentile(99, since, labels),
        }


class Timer(Histogram):
    """Duration tracking with context manager support"""

    def __init__(self, name: str, description: str = "", **kwargs):
        # Use latency buckets by default
        kwargs.setdefault("buckets", HistogramBuckets.LATENCY_MS)
        super().__init__(name, description, **kwargs)

    def time(self, labels: Optional[Dict[str, str]] = None):
        """Context manager for timing operations"""
        return TimerContext(self, labels)


class TimerContext:
    """Context manager for Timer"""

    def __init__(self, timer: Timer, labels: Optional[Dict[str, str]] = None):
        self.timer = timer
        self.labels = labels
        self.start_time: Optional[float] = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, *args):
        if self.start_time:
            elapsed_ms = (time.time() - self.start_time) * 1000
            self.timer.observe(elapsed_ms, self.labels)


class MetricsRegistry:
    """
    Central registry for all metrics.

    Usage:
        registry = MetricsRegistry()

        # Create metrics
        trades = registry.counter("trades_total", "Total trades executed")
        latency = registry.histogram("api_latency_ms", "API call latency")
        balance = registry.gauge("account_balance", "Current account balance")

        # Record
        trades.inc(labels={"platform": "polymarket", "outcome": "success"})
        latency.observe(45.2, labels={"endpoint": "/orders"})
        balance.set(10000.50, labels={"platform": "kalshi"})

        # Query
        print(registry.get_all())
    """

    def __init__(self):
        self._metrics: Dict[str, Metric] = {}
        self._lock = threading.Lock()

    def _register(self, metric: Metric) -> Metric:
        """Register a metric"""
        with self._lock:
            if metric.name in self._metrics:
                return self._metrics[metric.name]
            self._metrics[metric.name] = metric
            return metric

    def counter(self, name: str, description: str = "", **kwargs) -> Counter:
        """Create or get a counter"""
        return self._register(Counter(name, description, **kwargs))

    def gauge(self, name: str, description: str = "", **kwargs) -> Gauge:
        """Create or get a gauge"""
        return self._register(Gauge(name, description, **kwargs))

    def histogram(self, name: str, description: str = "", **kwargs) -> Histogram:
        """Create or get a histogram"""
        return self._register(Histogram(name, description, **kwargs))

    def timer(self, name: str, description: str = "", **kwargs) -> Timer:
        """Create or get a timer"""
        return self._register(Timer(name, description, **kwargs))

    def get(self, name: str) -> Optional[Metric]:
        """Get metric by name"""
        return self._metrics.get(name)

    def get_all(self) -> Dict[str, Metric]:
        """Get all registered metrics"""
        return dict(self._metrics)

    def snapshot(self) -> Dict[str, Any]:
        """Get current values of all metrics"""
        result = {}
        for name, metric in self._metrics.items():
            if isinstance(metric, Counter):
                result[name] = {"type": "counter", "value": metric._values}
            elif isinstance(metric, Gauge):
                latest = metric.get_latest()
                result[name] = {
                    "type": "gauge",
                    "value": latest.value if latest else None,
                }
            elif isinstance(metric, (Histogram, Timer)):
                result[name] = {
                    "type": metric.type.value,
                    "stats": metric.get_stats(),
                }
        return result


# Global default registry
default_registry = MetricsRegistry()


# Convenience functions using default registry
def counter(name: str, description: str = "", **kwargs) -> Counter:
    return default_registry.counter(name, description, **kwargs)


def gauge(name: str, description: str = "", **kwargs) -> Gauge:
    return default_registry.gauge(name, description, **kwargs)


def histogram(name: str, description: str = "", **kwargs) -> Histogram:
    return default_registry.histogram(name, description, **kwargs)


def timer(name: str, description: str = "", **kwargs) -> Timer:
    return default_registry.timer(name, description, **kwargs)
