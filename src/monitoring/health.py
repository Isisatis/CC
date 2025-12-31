"""
System Health Monitoring

Tracks connection health, API latency, and system status
for both Polymarket and Kalshi.

Monitors:
- API endpoint availability
- WebSocket connection status
- Request latency (per endpoint)
- Error rates
- Rate limit status
"""

import time
import asyncio
import logging
from enum import Enum
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Any, Callable
from dataclasses import dataclass, field
from threading import Lock, RLock
import statistics

from .metrics import MetricsRegistry, Counter, Gauge, Histogram, Timer

logger = logging.getLogger(__name__)


class ConnectionStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class EndpointHealth:
    """Health status for a single API endpoint"""
    endpoint: str
    platform: str
    status: ConnectionStatus = ConnectionStatus.UNKNOWN
    last_check: Optional[datetime] = None
    last_success: Optional[datetime] = None
    last_error: Optional[str] = None
    latency_ms: Optional[float] = None
    success_rate_1m: float = 1.0  # Last minute success rate
    error_count_1m: int = 0

    @property
    def is_healthy(self) -> bool:
        return (
            self.status == ConnectionStatus.CONNECTED
            and self.success_rate_1m > 0.95
        )


@dataclass
class PlatformHealth:
    """Aggregate health for a platform"""
    platform: str
    status: HealthStatus = HealthStatus.UNKNOWN
    endpoints: Dict[str, EndpointHealth] = field(default_factory=dict)
    websocket_status: ConnectionStatus = ConnectionStatus.UNKNOWN
    websocket_last_message: Optional[datetime] = None
    rate_limit_remaining: Optional[int] = None
    rate_limit_reset: Optional[datetime] = None

    def update_status(self):
        """Compute overall platform health from endpoints"""
        if not self.endpoints:
            self.status = HealthStatus.UNKNOWN
            return

        healthy_count = sum(1 for e in self.endpoints.values() if e.is_healthy)
        total = len(self.endpoints)

        if healthy_count == total:
            self.status = HealthStatus.HEALTHY
        elif healthy_count > 0:
            self.status = HealthStatus.DEGRADED
        else:
            self.status = HealthStatus.UNHEALTHY


class LatencyTracker:
    """
    Tracks latency for API calls with rolling window statistics.
    """

    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self._samples: Dict[str, List[float]] = {}  # endpoint -> latencies
        self._lock = RLock()  # Reentrant for get_all_stats -> get_stats

    def record(self, endpoint: str, latency_ms: float):
        """Record a latency sample"""
        with self._lock:
            if endpoint not in self._samples:
                self._samples[endpoint] = []
            samples = self._samples[endpoint]
            samples.append(latency_ms)
            if len(samples) > self.window_size:
                samples.pop(0)

    def get_stats(self, endpoint: str) -> Dict[str, float]:
        """Get latency statistics for endpoint"""
        with self._lock:
            samples = self._samples.get(endpoint, [])
            if not samples:
                return {}

            return {
                "count": len(samples),
                "min": min(samples),
                "max": max(samples),
                "mean": statistics.mean(samples),
                "median": statistics.median(samples),
                "p95": sorted(samples)[int(len(samples) * 0.95)] if len(samples) >= 20 else max(samples),
                "p99": sorted(samples)[int(len(samples) * 0.99)] if len(samples) >= 100 else max(samples),
            }

    def get_all_stats(self) -> Dict[str, Dict[str, float]]:
        """Get stats for all endpoints"""
        with self._lock:
            return {ep: self.get_stats(ep) for ep in self._samples}


class HealthMonitor:
    """
    Central health monitoring for the trading system.

    Usage:
        monitor = HealthMonitor()

        # Record API call
        start = time.time()
        response = api.call(...)
        monitor.record_api_call(
            platform="polymarket",
            endpoint="/orders",
            latency_ms=(time.time() - start) * 1000,
            success=response.ok,
            error=None if response.ok else response.error
        )

        # Get health status
        health = monitor.get_platform_health("polymarket")
        print(f"Polymarket: {health.status}")

        # Get overall system health
        system = monitor.get_system_health()
    """

    def __init__(self, metrics_registry: Optional[MetricsRegistry] = None):
        self.registry = metrics_registry or MetricsRegistry()

        # Platform health tracking
        self._platforms: Dict[str, PlatformHealth] = {
            "polymarket": PlatformHealth(platform="polymarket"),
            "kalshi": PlatformHealth(platform="kalshi"),
        }

        # Latency tracking
        self.latency = LatencyTracker()

        # Error tracking (last N errors per platform)
        self._errors: Dict[str, List[Dict[str, Any]]] = {
            "polymarket": [],
            "kalshi": [],
        }
        self._max_errors = 50

        # Metrics
        self._api_calls = self.registry.counter(
            "api_calls_total",
            "Total API calls",
            labels=["platform", "endpoint", "status"]
        )
        self._api_latency = self.registry.histogram(
            "api_latency_ms",
            "API call latency in milliseconds"
        )
        self._api_errors = self.registry.counter(
            "api_errors_total",
            "Total API errors"
        )
        self._ws_messages = self.registry.counter(
            "websocket_messages_total",
            "Total WebSocket messages received"
        )
        self._ws_status = self.registry.gauge(
            "websocket_connected",
            "WebSocket connection status (1=connected, 0=disconnected)"
        )

        self._lock = Lock()

    def record_api_call(
        self,
        platform: str,
        endpoint: str,
        latency_ms: float,
        success: bool,
        error: Optional[str] = None,
        status_code: Optional[int] = None,
    ):
        """Record an API call result"""
        with self._lock:
            # Update metrics
            status = "success" if success else "error"
            self._api_calls.inc(labels={
                "platform": platform,
                "endpoint": endpoint,
                "status": status,
            })
            self._api_latency.observe(latency_ms, labels={
                "platform": platform,
                "endpoint": endpoint,
            })

            if not success:
                self._api_errors.inc(labels={"platform": platform})

            # Update latency tracker
            self.latency.record(f"{platform}:{endpoint}", latency_ms)

            # Update endpoint health
            ph = self._platforms.get(platform)
            if ph:
                if endpoint not in ph.endpoints:
                    ph.endpoints[endpoint] = EndpointHealth(
                        endpoint=endpoint,
                        platform=platform,
                    )

                ep = ph.endpoints[endpoint]
                ep.last_check = datetime.utcnow()
                ep.latency_ms = latency_ms

                if success:
                    ep.last_success = datetime.utcnow()
                    ep.status = ConnectionStatus.CONNECTED
                else:
                    ep.last_error = error
                    ep.error_count_1m += 1
                    if status_code == 429:
                        ep.status = ConnectionStatus.RATE_LIMITED
                    else:
                        ep.status = ConnectionStatus.ERROR

                    # Record error
                    self._errors[platform].append({
                        "timestamp": datetime.utcnow().isoformat(),
                        "endpoint": endpoint,
                        "error": error,
                        "status_code": status_code,
                    })
                    if len(self._errors[platform]) > self._max_errors:
                        self._errors[platform].pop(0)

                ph.update_status()

    def record_websocket_status(
        self,
        platform: str,
        connected: bool,
        message_received: bool = False,
    ):
        """Record WebSocket connection status"""
        with self._lock:
            ph = self._platforms.get(platform)
            if ph:
                ph.websocket_status = (
                    ConnectionStatus.CONNECTED if connected
                    else ConnectionStatus.DISCONNECTED
                )
                if message_received:
                    ph.websocket_last_message = datetime.utcnow()
                    self._ws_messages.inc(labels={"platform": platform})

            self._ws_status.set(
                1 if connected else 0,
                labels={"platform": platform}
            )

    def record_rate_limit(
        self,
        platform: str,
        remaining: int,
        reset_at: Optional[datetime] = None,
    ):
        """Record rate limit status from API headers"""
        with self._lock:
            ph = self._platforms.get(platform)
            if ph:
                ph.rate_limit_remaining = remaining
                ph.rate_limit_reset = reset_at

    def get_platform_health(self, platform: str) -> Optional[PlatformHealth]:
        """Get health status for a platform"""
        return self._platforms.get(platform)

    def get_system_health(self) -> Dict[str, Any]:
        """Get overall system health summary"""
        with self._lock:
            platforms = {}
            overall_status = HealthStatus.HEALTHY

            for name, ph in self._platforms.items():
                ph.update_status()
                platforms[name] = {
                    "status": ph.status.value,
                    "websocket": ph.websocket_status.value,
                    "endpoints": {
                        ep: {
                            "status": eh.status.value,
                            "latency_ms": eh.latency_ms,
                            "last_success": eh.last_success.isoformat() if eh.last_success else None,
                        }
                        for ep, eh in ph.endpoints.items()
                    },
                    "rate_limit_remaining": ph.rate_limit_remaining,
                }

                if ph.status == HealthStatus.UNHEALTHY:
                    overall_status = HealthStatus.UNHEALTHY
                elif ph.status == HealthStatus.DEGRADED and overall_status == HealthStatus.HEALTHY:
                    overall_status = HealthStatus.DEGRADED

            return {
                "status": overall_status.value,
                "timestamp": datetime.utcnow().isoformat(),
                "platforms": platforms,
                "latency": self.latency.get_all_stats(),
            }

    def get_recent_errors(
        self,
        platform: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get recent errors"""
        if platform:
            return self._errors.get(platform, [])[-limit:]
        else:
            all_errors = []
            for p, errors in self._errors.items():
                for e in errors:
                    e["platform"] = p
                    all_errors.append(e)
            return sorted(all_errors, key=lambda x: x["timestamp"])[-limit:]

    def is_healthy(self) -> bool:
        """Quick check if system is healthy"""
        return all(
            ph.status in (HealthStatus.HEALTHY, HealthStatus.DEGRADED)
            for ph in self._platforms.values()
        )


class ConnectionProbe:
    """
    Periodic health probes to check API availability.

    Usage:
        probe = ConnectionProbe(monitor)

        # Add probe targets
        probe.add_target("polymarket", "https://gamma-api.polymarket.com/markets?limit=1")
        probe.add_target("kalshi", "https://trading-api.kalshi.com/trade-api/v2/exchange/status")

        # Start probing
        await probe.start(interval_seconds=30)
    """

    def __init__(self, monitor: HealthMonitor):
        self.monitor = monitor
        self._targets: Dict[str, str] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def add_target(self, platform: str, url: str):
        """Add a probe target"""
        self._targets[platform] = url

    async def probe_once(self, platform: str, url: str) -> bool:
        """Execute single probe"""
        import aiohttp

        start = time.time()
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as response:
                    latency = (time.time() - start) * 1000
                    success = response.status < 400

                    self.monitor.record_api_call(
                        platform=platform,
                        endpoint="/probe",
                        latency_ms=latency,
                        success=success,
                        status_code=response.status,
                    )
                    return success

        except Exception as e:
            latency = (time.time() - start) * 1000
            self.monitor.record_api_call(
                platform=platform,
                endpoint="/probe",
                latency_ms=latency,
                success=False,
                error=str(e),
            )
            return False

    async def probe_all(self) -> Dict[str, bool]:
        """Probe all targets"""
        results = {}
        for platform, url in self._targets.items():
            results[platform] = await self.probe_once(platform, url)
        return results

    async def start(self, interval_seconds: float = 30):
        """Start periodic probing"""
        self._running = True
        while self._running:
            await self.probe_all()
            await asyncio.sleep(interval_seconds)

    def stop(self):
        """Stop probing"""
        self._running = False
