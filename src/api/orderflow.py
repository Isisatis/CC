"""
Polymarket WebSocket Client & Order Flow Tracker

Real-time order book updates and order flow detection.

HOW ORDER FLOW INFERENCE WORKS:
===============================
Polymarket's WebSocket sends order book STATE updates, not individual order events.
We infer order flow by comparing consecutive book states:

1. PLACEMENTS: Detected when...
   - New price level appears → new order at that price
   - Existing level size INCREASES → order(s) added

2. CANCELLATIONS: Detected when...
   - Price level disappears AND no matching trade → cancelled
   - Level size DECREASES AND no matching trade → partial cancel

3. FILLS: Detected when...
   - Level size decreases AND there's a trade at that price → filled
   - Cross-reference book changes with trade feed to distinguish

The limitation: We see aggregate size per price level, not individual orders.
So $10k at 0.50 becoming $15k means $5k added, but we don't know by whom
unless we correlate with trade data that includes maker addresses.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Callable, Set, Tuple
from collections import defaultdict, deque
import threading

try:
    import websockets
    from websockets.client import WebSocketClientProtocol
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

from .models import (
    OrderSide, RawOrderBookLevel, OrderBookSnapshot, OrderBookDelta,
    LargeOrder, WhaleTrade, WalletActivity, MarketWhaleActivity,
    OrderFlowEvent, OrderFlowSummary, IlliquidMarketSignal
)

logger = logging.getLogger(__name__)


# ============================================================================
# Order Book Tracker (Polling-based)
# ============================================================================

class OrderBookTracker:
    """
    Tracks order book changes over time via polling.
    Detects new orders, cancellations, and large order activity.
    """

    def __init__(self, clob_client):
        """
        Args:
            clob_client: CLOBAPIClient instance
        """
        self.clob = clob_client
        self.snapshots: Dict[str, List[OrderBookSnapshot]] = defaultdict(list)
        self.max_history = 100  # Max snapshots to keep per token

    def take_snapshot(self, token_id: str) -> OrderBookSnapshot:
        """Take a snapshot of current order book state"""
        book = self.clob.get_order_book(token_id)

        snapshot = OrderBookSnapshot(
            token_id=token_id,
            timestamp=datetime.utcnow(),
            bids=[RawOrderBookLevel(price=b.price, size=b.size) for b in book.bids],
            asks=[RawOrderBookLevel(price=a.price, size=a.size) for a in book.asks],
        )
        snapshot.snapshot_hash = snapshot.compute_hash()

        # Store in history
        self.snapshots[token_id].append(snapshot)
        if len(self.snapshots[token_id]) > self.max_history:
            self.snapshots[token_id] = self.snapshots[token_id][-self.max_history:]

        return snapshot

    def get_latest_snapshot(self, token_id: str) -> Optional[OrderBookSnapshot]:
        """Get most recent snapshot for a token"""
        if token_id in self.snapshots and self.snapshots[token_id]:
            return self.snapshots[token_id][-1]
        return None

    def compute_delta(
        self,
        old: OrderBookSnapshot,
        new: OrderBookSnapshot
    ) -> OrderBookDelta:
        """Compute changes between two snapshots"""
        delta = OrderBookDelta(
            token_id=new.token_id,
            timestamp=new.timestamp,
        )

        if old.timestamp and new.timestamp:
            delta.time_delta_ms = int((new.timestamp - old.timestamp).total_seconds() * 1000)

        # Price changes
        if old.best_bid is not None and new.best_bid is not None:
            delta.bid_price_change = new.best_bid - old.best_bid
        if old.best_ask is not None and new.best_ask is not None:
            delta.ask_price_change = new.best_ask - old.best_ask
        if old.mid_price is not None and new.mid_price is not None:
            delta.mid_price_change = new.mid_price - old.mid_price
        if old.spread_abs is not None and new.spread_abs is not None:
            delta.spread_change = new.spread_abs - old.spread_abs

        # Size changes
        delta.bid_size_change = new.total_bid_size - old.total_bid_size
        delta.ask_size_change = new.total_ask_size - old.total_ask_size
        delta.bid_notional_change = new.total_bid_notional - old.total_bid_notional
        delta.ask_notional_change = new.total_ask_notional - old.total_ask_notional

        # Level-by-level comparison
        old_bid_prices = {b.price: b for b in old.bids}
        new_bid_prices = {b.price: b for b in new.bids}
        old_ask_prices = {a.price: a for a in old.asks}
        new_ask_prices = {a.price: a for a in new.asks}

        # New and removed bid levels
        for price, level in new_bid_prices.items():
            if price not in old_bid_prices:
                delta.new_bid_levels.append(level)
            elif old_bid_prices[price].size != level.size:
                delta.modified_bid_levels.append({
                    "price": price,
                    "old_size": old_bid_prices[price].size,
                    "new_size": level.size,
                    "size_change": level.size - old_bid_prices[price].size,
                })

        for price, level in old_bid_prices.items():
            if price not in new_bid_prices:
                delta.removed_bid_levels.append(level)

        # New and removed ask levels
        for price, level in new_ask_prices.items():
            if price not in old_ask_prices:
                delta.new_ask_levels.append(level)
            elif old_ask_prices[price].size != level.size:
                delta.modified_ask_levels.append({
                    "price": price,
                    "old_size": old_ask_prices[price].size,
                    "new_size": level.size,
                    "size_change": level.size - old_ask_prices[price].size,
                })

        for price, level in old_ask_prices.items():
            if price not in new_ask_prices:
                delta.removed_ask_levels.append(level)

        return delta

    def detect_changes(self, token_id: str) -> Optional[OrderBookDelta]:
        """
        Take new snapshot and compute delta from previous.
        Returns None if no previous snapshot exists.
        """
        old = self.get_latest_snapshot(token_id)
        new = self.take_snapshot(token_id)

        if old is None:
            return None

        # Skip if no actual change
        if old.snapshot_hash == new.snapshot_hash:
            return None

        return self.compute_delta(old, new)


# ============================================================================
# Order Flow Analyzer (Infers placements/cancellations)
# ============================================================================

class OrderFlowAnalyzer:
    """
    Analyzes order book changes to infer order flow events.

    This class takes book state changes and trade data to determine:
    - New order placements (where, how much)
    - Order cancellations (where, how much)
    - Fills vs cancellations (by cross-referencing trades)

    Usage:
        analyzer = OrderFlowAnalyzer()

        # When you get a book update:
        events = analyzer.process_book_update(token_id, new_book_state)

        # When you get a trade:
        analyzer.record_trade(token_id, price, size, side, timestamp)
    """

    def __init__(self, trade_window_ms: int = 1000):
        """
        Args:
            trade_window_ms: Time window to correlate trades with book changes
        """
        self.trade_window_ms = trade_window_ms

        # Current book state per token: {token_id: {price: size}}
        self._bid_books: Dict[str, Dict[float, float]] = defaultdict(dict)
        self._ask_books: Dict[str, Dict[float, float]] = defaultdict(dict)

        # Recent trades for correlation: {token_id: deque of (timestamp, price, size, side)}
        self._recent_trades: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))

        # Callbacks
        self.on_placement: Optional[Callable[[OrderFlowEvent], None]] = None
        self.on_cancellation: Optional[Callable[[OrderFlowEvent], None]] = None
        self.on_fill: Optional[Callable[[OrderFlowEvent], None]] = None

        # Stats
        self._stats = defaultdict(lambda: {
            "placements": 0, "cancellations": 0, "fills": 0,
            "placement_volume": 0.0, "cancel_volume": 0.0, "fill_volume": 0.0
        })

    def record_trade(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        timestamp: Optional[datetime] = None
    ):
        """Record a trade for correlation with book changes"""
        ts = timestamp or datetime.utcnow()
        self._recent_trades[token_id].append((ts, price, size, side))

    def _find_matching_trade(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        timestamp: datetime
    ) -> Optional[Tuple[datetime, float, float, str]]:
        """Find a recent trade that could explain a book size decrease"""
        window_start = timestamp - timedelta(milliseconds=self.trade_window_ms)

        for trade in self._recent_trades[token_id]:
            trade_ts, trade_price, trade_size, trade_side = trade
            if trade_ts < window_start:
                continue

            # Match: same price, opposite side (maker side), similar size
            if abs(trade_price - price) < 0.0001:
                # For a bid decrease, we look for a sell trade (taker sold into bid)
                # For an ask decrease, we look for a buy trade (taker bought the ask)
                expected_side = "SELL" if side == "bid" else "BUY"
                if trade_side.upper() == expected_side:
                    if abs(trade_size - size) < size * 0.1:  # Within 10%
                        return trade

        return None

    def process_book_update(
        self,
        token_id: str,
        bids: List[Tuple[float, float]],  # [(price, size), ...]
        asks: List[Tuple[float, float]],
        timestamp: Optional[datetime] = None
    ) -> List[OrderFlowEvent]:
        """
        Process a new book state and emit order flow events.

        Args:
            token_id: Token being updated
            bids: List of (price, size) tuples for bid side
            asks: List of (price, size) tuples for ask side
            timestamp: When this update was received

        Returns:
            List of inferred OrderFlowEvent objects
        """
        ts = timestamp or datetime.utcnow()
        events = []

        # Convert to dicts
        new_bids = {price: size for price, size in bids}
        new_asks = {price: size for price, size in asks}

        old_bids = self._bid_books[token_id]
        old_asks = self._ask_books[token_id]

        # Analyze bid side changes
        events.extend(self._analyze_side_changes(
            token_id, "bid", old_bids, new_bids, ts
        ))

        # Analyze ask side changes
        events.extend(self._analyze_side_changes(
            token_id, "ask", old_asks, new_asks, ts
        ))

        # Update stored state
        self._bid_books[token_id] = new_bids
        self._ask_books[token_id] = new_asks

        return events

    def _analyze_side_changes(
        self,
        token_id: str,
        side: str,
        old_book: Dict[float, float],
        new_book: Dict[float, float],
        timestamp: datetime
    ) -> List[OrderFlowEvent]:
        """Analyze changes on one side of the book"""
        events = []

        all_prices = set(old_book.keys()) | set(new_book.keys())

        for price in all_prices:
            old_size = old_book.get(price, 0)
            new_size = new_book.get(price, 0)
            size_change = new_size - old_size

            if abs(size_change) < 0.01:  # Ignore tiny changes
                continue

            if size_change > 0:
                # SIZE INCREASED = NEW ORDER PLACEMENT
                event = OrderFlowEvent(
                    event_type="new_order",
                    token_id=token_id,
                    timestamp=timestamp,
                    side=side,
                    price=price,
                    size=size_change,
                )
                events.append(event)
                self._stats[token_id]["placements"] += 1
                self._stats[token_id]["placement_volume"] += event.notional

                if self.on_placement:
                    self.on_placement(event)

            else:
                # SIZE DECREASED = CANCEL or FILL
                decrease = abs(size_change)

                # Check if there's a matching trade
                matching_trade = self._find_matching_trade(
                    token_id, price, decrease, side, timestamp
                )

                if matching_trade:
                    # FILL - trade explains the decrease
                    event = OrderFlowEvent(
                        event_type="fill",
                        token_id=token_id,
                        timestamp=timestamp,
                        side=side,
                        price=price,
                        size=decrease,
                    )
                    self._stats[token_id]["fills"] += 1
                    self._stats[token_id]["fill_volume"] += event.notional

                    if self.on_fill:
                        self.on_fill(event)
                else:
                    # CANCELLATION - no trade, order was pulled
                    event = OrderFlowEvent(
                        event_type="cancel",
                        token_id=token_id,
                        timestamp=timestamp,
                        side=side,
                        price=price,
                        size=decrease,
                    )
                    self._stats[token_id]["cancellations"] += 1
                    self._stats[token_id]["cancel_volume"] += event.notional

                    if self.on_cancellation:
                        self.on_cancellation(event)

                events.append(event)

        return events

    def get_stats(self, token_id: str) -> Dict[str, Any]:
        """Get order flow statistics for a token"""
        return dict(self._stats[token_id])

    def get_current_book(self, token_id: str) -> Dict[str, Dict[float, float]]:
        """Get current order book state"""
        return {
            "bids": dict(self._bid_books[token_id]),
            "asks": dict(self._ask_books[token_id]),
        }

    def reset(self, token_id: Optional[str] = None):
        """Reset state for a token or all tokens"""
        if token_id:
            self._bid_books[token_id] = {}
            self._ask_books[token_id] = {}
            self._recent_trades[token_id].clear()
            self._stats[token_id] = {
                "placements": 0, "cancellations": 0, "fills": 0,
                "placement_volume": 0.0, "cancel_volume": 0.0, "fill_volume": 0.0
            }
        else:
            self._bid_books.clear()
            self._ask_books.clear()
            self._recent_trades.clear()
            self._stats.clear()


# ============================================================================
# WebSocket Client (Real-time)
# ============================================================================

class PolymarketWebSocket:
    """
    WebSocket client for real-time Polymarket data.

    Polymarket uses WebSocket for:
    - Order book updates
    - Trade notifications
    - Price updates
    """

    # Known WebSocket endpoints
    WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    def __init__(self):
        if not HAS_WEBSOCKETS:
            raise ImportError("websockets package required. Install with: pip install websockets")

        self.ws: Optional[WebSocketClientProtocol] = None
        self.subscribed_tokens: Set[str] = set()
        self.running = False

        # Callbacks
        self.on_book_update: Optional[Callable] = None
        self.on_trade: Optional[Callable] = None
        self.on_price_update: Optional[Callable] = None
        self.on_error: Optional[Callable] = None

        # Message queue for processing
        self._message_queue: asyncio.Queue = None
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0

    async def connect(self):
        """Establish WebSocket connection"""
        try:
            self.ws = await websockets.connect(
                self.WS_URL,
                ping_interval=30,
                ping_timeout=10,
            )
            self.running = True
            self._reconnect_delay = 1.0
            logger.info(f"Connected to Polymarket WebSocket")
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            raise

    async def disconnect(self):
        """Close WebSocket connection"""
        self.running = False
        if self.ws:
            await self.ws.close()
            self.ws = None
        logger.info("Disconnected from Polymarket WebSocket")

    async def subscribe(self, token_ids: List[str]):
        """Subscribe to order book updates for tokens"""
        if not self.ws:
            raise RuntimeError("Not connected")

        for token_id in token_ids:
            if token_id not in self.subscribed_tokens:
                msg = {
                    "type": "subscribe",
                    "channel": "book",
                    "assets_ids": [token_id],
                }
                await self.ws.send(json.dumps(msg))
                self.subscribed_tokens.add(token_id)
                logger.debug(f"Subscribed to {token_id}")

    async def unsubscribe(self, token_ids: List[str]):
        """Unsubscribe from tokens"""
        if not self.ws:
            return

        for token_id in token_ids:
            if token_id in self.subscribed_tokens:
                msg = {
                    "type": "unsubscribe",
                    "channel": "book",
                    "assets_ids": [token_id],
                }
                await self.ws.send(json.dumps(msg))
                self.subscribed_tokens.discard(token_id)

    async def listen(self):
        """Listen for incoming messages"""
        if not self.ws:
            raise RuntimeError("Not connected")

        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON: {e}")
                except Exception as e:
                    logger.error(f"Error handling message: {e}")
                    if self.on_error:
                        self.on_error(e)

        except websockets.ConnectionClosed as e:
            logger.warning(f"WebSocket connection closed: {e}")
            await self._handle_disconnect()

    async def _handle_message(self, data: Dict[str, Any]):
        """Process incoming WebSocket message"""
        msg_type = data.get("type") or data.get("event_type")

        if msg_type == "book":
            # Order book update
            if self.on_book_update:
                self.on_book_update(data)

        elif msg_type == "trade":
            # Trade notification
            if self.on_trade:
                self.on_trade(data)

        elif msg_type == "price_change":
            # Price update
            if self.on_price_update:
                self.on_price_update(data)

        elif msg_type == "subscribed":
            logger.debug(f"Subscription confirmed: {data}")

        elif msg_type == "error":
            logger.error(f"WebSocket error: {data}")
            if self.on_error:
                self.on_error(data)

    async def _handle_disconnect(self):
        """Handle disconnection with exponential backoff reconnect"""
        if not self.running:
            return

        logger.info(f"Attempting reconnect in {self._reconnect_delay}s...")
        await asyncio.sleep(self._reconnect_delay)
        self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

        try:
            await self.connect()
            # Resubscribe to previous tokens
            if self.subscribed_tokens:
                tokens = list(self.subscribed_tokens)
                self.subscribed_tokens.clear()
                await self.subscribe(tokens)
            # Resume listening
            asyncio.create_task(self.listen())
        except Exception as e:
            logger.error(f"Reconnection failed: {e}")
            await self._handle_disconnect()

    async def run(self, token_ids: List[str]):
        """Connect, subscribe, and listen"""
        await self.connect()
        await self.subscribe(token_ids)
        await self.listen()


# ============================================================================
# Whale Detector
# ============================================================================

class WhaleDetector:
    """
    Detects and tracks whale activity across markets.
    """

    def __init__(
        self,
        clob_client,
        large_order_threshold: float = 1000,  # $1k
        whale_threshold: float = 5000,  # $5k
    ):
        self.clob = clob_client
        self.large_order_threshold = large_order_threshold
        self.whale_threshold = whale_threshold

        # Tracking state
        self.wallet_activity: Dict[str, WalletActivity] = {}
        self.known_whales: Set[str] = set()

    def scan_order_book(
        self,
        snapshot: OrderBookSnapshot,
        market_id: Optional[str] = None
    ) -> List[LargeOrder]:
        """Scan order book for large orders"""
        large_orders = []

        for side, levels, total in [
            ("bid", snapshot.bids, snapshot.total_bid_notional),
            ("ask", snapshot.asks, snapshot.total_ask_notional),
        ]:
            for level in levels:
                notional = level.notional
                if notional >= self.large_order_threshold:
                    pct_of_book = (notional / total * 100) if total > 0 else 0

                    distance_bps = None
                    if snapshot.mid_price:
                        distance_bps = abs(level.price - snapshot.mid_price) / snapshot.mid_price * 10000

                    order = LargeOrder(
                        token_id=snapshot.token_id,
                        market_id=market_id,
                        side=side,
                        price=level.price,
                        size=level.size,
                        notional=notional,
                        pct_of_book=pct_of_book,
                        pct_of_level=100.0,  # We don't have individual order breakdown
                        distance_from_mid_bps=distance_bps,
                        is_whale=notional >= self.whale_threshold,
                        is_iceberg_candidate=self._check_iceberg(level.size),
                    )
                    large_orders.append(order)

        return large_orders

    def _check_iceberg(self, size: float) -> bool:
        """Check if order size suggests iceberg (hidden size)"""
        # Round numbers often indicate algorithmic/iceberg orders
        if size >= 1000 and size % 100 == 0:
            return True
        if size >= 10000 and size % 1000 == 0:
            return True
        return False

    def analyze_trades(
        self,
        trades: List[Dict[str, Any]],
        token_id: str,
        market_id: Optional[str] = None,
        market_question: Optional[str] = None,
    ) -> List[WhaleTrade]:
        """Analyze trades for whale activity"""
        whale_trades = []

        for t in trades:
            size = float(t.get("size", 0))
            price = float(t.get("price", 0))
            notional = size * price

            if notional >= self.large_order_threshold:
                timestamp = t.get("timestamp")
                if isinstance(timestamp, (int, float)):
                    timestamp = datetime.fromtimestamp(timestamp)
                elif isinstance(timestamp, str):
                    try:
                        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    except:
                        timestamp = datetime.utcnow()
                else:
                    timestamp = datetime.utcnow()

                whale_trade = WhaleTrade(
                    trade_id=t.get("id"),
                    token_id=token_id,
                    market_id=market_id,
                    market_question=market_question,
                    timestamp=timestamp,
                    side=t.get("side", "unknown"),
                    price=price,
                    size=size,
                    notional=notional,
                    maker_address=t.get("maker_address"),
                    taker_address=t.get("owner"),
                )
                whale_trades.append(whale_trade)

                # Track wallet activity
                for addr in [whale_trade.maker_address, whale_trade.taker_address]:
                    if addr:
                        self._update_wallet_activity(addr, whale_trade)

        return whale_trades

    def _update_wallet_activity(self, address: str, trade: WhaleTrade):
        """Update wallet activity tracking"""
        if address not in self.wallet_activity:
            self.wallet_activity[address] = WalletActivity(address=address)

        activity = self.wallet_activity[address]
        activity.total_trades += 1
        activity.total_volume += trade.notional
        activity.last_seen = trade.timestamp

        if activity.first_seen is None:
            activity.first_seen = trade.timestamp

        if trade.side.upper() == "BUY":
            activity.total_buy_volume += trade.notional
        else:
            activity.total_sell_volume += trade.notional

        if trade.market_id and trade.market_id not in activity.markets_traded:
            activity.markets_traded.append(trade.market_id)

        if trade.market_id:
            activity.market_volumes[trade.market_id] = (
                activity.market_volumes.get(trade.market_id, 0) + trade.notional
            )

        activity.recent_trades.append(trade)
        if len(activity.recent_trades) > 50:
            activity.recent_trades = activity.recent_trades[-50:]

        if activity.largest_trade is None or trade.notional > activity.largest_trade.notional:
            activity.largest_trade = trade

        if activity.total_trades > 0:
            activity.avg_trade_size = activity.total_volume / activity.total_trades

        if activity.is_whale:
            self.known_whales.add(address)

    def get_wallet_activity(self, address: str) -> Optional[WalletActivity]:
        """Get activity for a specific wallet"""
        return self.wallet_activity.get(address)

    def get_whales(self) -> List[WalletActivity]:
        """Get all known whale wallets"""
        return [
            self.wallet_activity[addr]
            for addr in self.known_whales
            if addr in self.wallet_activity
        ]

    def get_market_whale_activity(
        self,
        snapshot: OrderBookSnapshot,
        trades: List[Dict[str, Any]],
        market_id: str,
        market_question: Optional[str] = None,
        hours: int = 24,
    ) -> MarketWhaleActivity:
        """Get complete whale activity summary for a market"""
        # Scan order book
        large_orders = self.scan_order_book(snapshot, market_id)
        large_bids = [o for o in large_orders if o.side == "bid"]
        large_asks = [o for o in large_orders if o.side == "ask"]

        # Analyze trades
        whale_trades = self.analyze_trades(
            trades, snapshot.token_id, market_id, market_question
        )

        # Find active whales
        active_whales = set()
        whale_buy = 0.0
        whale_sell = 0.0

        for t in whale_trades:
            for addr in [t.maker_address, t.taker_address]:
                if addr and addr in self.known_whales:
                    active_whales.add(addr)

            if t.is_whale:
                if t.side.upper() == "BUY":
                    whale_buy += t.notional
                else:
                    whale_sell += t.notional

        return MarketWhaleActivity(
            market_id=market_id,
            token_id=snapshot.token_id,
            market_question=market_question,
            analysis_period_hours=hours,
            large_bids=large_bids,
            large_asks=large_asks,
            whale_trades=whale_trades,
            active_whales=list(active_whales),
            whale_buy_volume=whale_buy,
            whale_sell_volume=whale_sell,
        )


# ============================================================================
# Illiquid Market Signal Detector
# ============================================================================

class SignalDetector:
    """
    Detects trading signals in illiquid markets.
    """

    def __init__(
        self,
        book_imbalance_threshold: float = 0.5,  # 50% imbalance
        spread_compression_bps: float = 100,  # 1% spread improvement
        large_order_pct: float = 10,  # 10% of book
    ):
        self.book_imbalance_threshold = book_imbalance_threshold
        self.spread_compression_bps = spread_compression_bps
        self.large_order_pct = large_order_pct

    def detect_signals(
        self,
        snapshot: OrderBookSnapshot,
        delta: Optional[OrderBookDelta] = None,
        whale_activity: Optional[MarketWhaleActivity] = None,
        market_id: Optional[str] = None,
        market_question: Optional[str] = None,
    ) -> List[IlliquidMarketSignal]:
        """Detect all signals for a market"""
        signals = []

        # Book imbalance signal
        if snapshot.imbalance is not None:
            if abs(snapshot.imbalance) >= self.book_imbalance_threshold:
                side = "buy" if snapshot.imbalance > 0 else "sell"
                signals.append(IlliquidMarketSignal(
                    signal_type="book_imbalance",
                    token_id=snapshot.token_id,
                    market_id=market_id,
                    market_question=market_question,
                    description=f"Order book heavily imbalanced toward {side} side ({snapshot.imbalance:.1%})",
                    severity="medium" if abs(snapshot.imbalance) < 0.7 else "high",
                    confidence=min(0.9, abs(snapshot.imbalance)),
                    trigger_data={
                        "imbalance": snapshot.imbalance,
                        "bid_notional": snapshot.total_bid_notional,
                        "ask_notional": snapshot.total_ask_notional,
                    },
                    mid_price=snapshot.mid_price,
                    spread_bps=snapshot.spread_bps,
                    book_imbalance=snapshot.imbalance,
                    suggested_side=side,
                ))

        # Spread compression signal
        if delta and delta.spread_change:
            if delta.spread_change < 0 and abs(delta.spread_change) > 0:
                old_spread_bps = (snapshot.spread_bps or 0) - (delta.spread_change * 10000 / (snapshot.mid_price or 1))
                compression = abs(delta.spread_change) / (snapshot.mid_price or 1) * 10000

                if compression >= self.spread_compression_bps:
                    signals.append(IlliquidMarketSignal(
                        signal_type="spread_compression",
                        token_id=snapshot.token_id,
                        market_id=market_id,
                        market_question=market_question,
                        description=f"Spread compressed by {compression:.0f} bps",
                        severity="medium",
                        confidence=0.7,
                        trigger_data={
                            "spread_change": delta.spread_change,
                            "compression_bps": compression,
                        },
                        mid_price=snapshot.mid_price,
                        spread_bps=snapshot.spread_bps,
                        book_imbalance=snapshot.imbalance,
                    ))

        # Large order signal
        if delta and delta.largest_new_order:
            order = delta.largest_new_order
            pct = order["notional"] / (snapshot.total_bid_notional + snapshot.total_ask_notional) * 100

            if pct >= self.large_order_pct:
                signals.append(IlliquidMarketSignal(
                    signal_type="large_order",
                    token_id=snapshot.token_id,
                    market_id=market_id,
                    market_question=market_question,
                    description=f"Large {order['side']} order: ${order['notional']:,.0f} ({pct:.1f}% of book)",
                    severity="high" if pct >= 20 else "medium",
                    confidence=0.8,
                    trigger_data=order,
                    mid_price=snapshot.mid_price,
                    spread_bps=snapshot.spread_bps,
                    book_imbalance=snapshot.imbalance,
                    suggested_side=order["side"],
                ))

        # Whale entry signal
        if whale_activity and whale_activity.has_whale_activity:
            if abs(whale_activity.whale_flow_bias or 0) >= 0.6:
                side = "buy" if whale_activity.whale_flow > 0 else "sell"
                signals.append(IlliquidMarketSignal(
                    signal_type="whale_entry",
                    token_id=snapshot.token_id,
                    market_id=market_id,
                    market_question=market_question,
                    description=f"Whale activity: ${abs(whale_activity.whale_flow):,.0f} net {side} flow",
                    severity="high",
                    confidence=0.85,
                    trigger_data={
                        "whale_buy": whale_activity.whale_buy_volume,
                        "whale_sell": whale_activity.whale_sell_volume,
                        "whale_flow": whale_activity.whale_flow,
                        "active_whales": whale_activity.active_whales,
                    },
                    mid_price=snapshot.mid_price,
                    spread_bps=snapshot.spread_bps,
                    book_imbalance=snapshot.imbalance,
                    suggested_side=side,
                ))

        return signals
