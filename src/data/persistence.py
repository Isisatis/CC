"""Data persistence layer for storing market data."""

import os
import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from contextlib import contextmanager

from .models import Market, OrderBook, Trade, PriceSnapshot


class DataStore:
    """Base class for data storage operations."""

    def __init__(self, data_dir: str = None):
        """
        Initialize data store.

        Args:
            data_dir: Directory for storing data files
        """
        if data_dir is None:
            data_dir = os.path.join(
                Path(__file__).parent.parent.parent, "data"
            )
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)


class MarketDataStore(DataStore):
    """Store for market data with SQLite and CSV support."""

    def __init__(self, data_dir: str = None, db_name: str = "polymarket.db"):
        """
        Initialize market data store.

        Args:
            data_dir: Directory for storing data files
            db_name: SQLite database filename
        """
        super().__init__(data_dir)
        self.db_path = self.data_dir / db_name
        self._init_database()

    def _init_database(self):
        """Initialize SQLite database with required tables."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Markets table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS markets (
                    condition_id TEXT PRIMARY KEY,
                    question TEXT,
                    description TEXT,
                    market_slug TEXT,
                    end_date_iso TEXT,
                    active INTEGER,
                    closed INTEGER,
                    volume REAL,
                    volume_24h REAL,
                    liquidity REAL,
                    tokens TEXT,
                    tags TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    fetched_at TEXT
                )
            """)

            # Order book snapshots table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS order_book_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id TEXT,
                    best_bid REAL,
                    best_ask REAL,
                    spread REAL,
                    spread_pct REAL,
                    midpoint REAL,
                    total_bid_size REAL,
                    total_ask_size REAL,
                    bids_json TEXT,
                    asks_json TEXT,
                    timestamp TEXT,
                    UNIQUE(token_id, timestamp)
                )
            """)

            # Trades table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT,
                    token_id TEXT,
                    price REAL,
                    size REAL,
                    side TEXT,
                    maker TEXT,
                    taker TEXT,
                    timestamp TEXT,
                    transaction_hash TEXT,
                    PRIMARY KEY (id, token_id)
                )
            """)

            # Price snapshots table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS price_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id TEXT,
                    price REAL,
                    bid REAL,
                    ask REAL,
                    volume REAL,
                    timestamp TEXT,
                    UNIQUE(token_id, timestamp)
                )
            """)

            # Create indexes for common queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_order_book_token_time
                ON order_book_snapshots(token_id, timestamp)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_token_time
                ON trades(token_id, timestamp)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_price_token_time
                ON price_snapshots(token_id, timestamp)
            """)

            conn.commit()

    @contextmanager
    def _get_connection(self):
        """Get a database connection context manager."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ==================== Market Operations ====================

    def save_market(self, market: Market) -> None:
        """Save a market to the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO markets
                (condition_id, question, description, market_slug, end_date_iso,
                 active, closed, volume, volume_24h, liquidity, tokens, tags,
                 created_at, updated_at, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                market.condition_id,
                market.question,
                market.description,
                market.market_slug,
                market.end_date_iso,
                1 if market.active else 0,
                1 if market.closed else 0,
                market.volume,
                market.volume_24h,
                market.liquidity,
                json.dumps(market.tokens),
                json.dumps(market.tags),
                market.created_at,
                market.updated_at,
                market.fetched_at,
            ))
            conn.commit()

    def save_markets(self, markets: List[Market]) -> int:
        """Save multiple markets to the database."""
        count = 0
        for market in markets:
            self.save_market(market)
            count += 1
        return count

    def get_market(self, condition_id: str) -> Optional[Market]:
        """Get a market by condition ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM markets WHERE condition_id = ?",
                (condition_id,)
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_market(row)
        return None

    def get_all_markets(self, active_only: bool = True) -> List[Market]:
        """Get all markets from the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if active_only:
                cursor.execute("SELECT * FROM markets WHERE active = 1")
            else:
                cursor.execute("SELECT * FROM markets")
            return [self._row_to_market(row) for row in cursor.fetchall()]

    def _row_to_market(self, row: sqlite3.Row) -> Market:
        """Convert a database row to a Market object."""
        return Market(
            condition_id=row["condition_id"],
            question=row["question"],
            description=row["description"] or "",
            market_slug=row["market_slug"] or "",
            end_date_iso=row["end_date_iso"] or "",
            active=bool(row["active"]),
            closed=bool(row["closed"]),
            volume=row["volume"] or 0,
            volume_24h=row["volume_24h"] or 0,
            liquidity=row["liquidity"] or 0,
            tokens=json.loads(row["tokens"]) if row["tokens"] else [],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
            fetched_at=row["fetched_at"] or "",
        )

    # ==================== Order Book Operations ====================

    def save_order_book(self, order_book: OrderBook) -> None:
        """Save an order book snapshot to the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO order_book_snapshots
                (token_id, best_bid, best_ask, spread, spread_pct, midpoint,
                 total_bid_size, total_ask_size, bids_json, asks_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order_book.token_id,
                order_book.best_bid,
                order_book.best_ask,
                order_book.spread,
                order_book.spread_pct,
                order_book.midpoint,
                order_book.total_bid_size,
                order_book.total_ask_size,
                json.dumps([b.to_dict() for b in order_book.bids]),
                json.dumps([a.to_dict() for a in order_book.asks]),
                order_book.timestamp,
            ))
            conn.commit()

    def get_order_book_history(
        self,
        token_id: str,
        start_time: str = None,
        end_time: str = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Get order book history for a token."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM order_book_snapshots WHERE token_id = ?"
            params = [token_id]

            if start_time:
                query += " AND timestamp >= ?"
                params.append(start_time)
            if end_time:
                query += " AND timestamp <= ?"
                params.append(end_time)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    # ==================== Trade Operations ====================

    def save_trade(self, trade: Trade) -> None:
        """Save a trade to the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO trades
                (id, token_id, price, size, side, maker, taker, timestamp, transaction_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.id,
                trade.token_id,
                trade.price,
                trade.size,
                trade.side,
                trade.maker,
                trade.taker,
                trade.timestamp,
                trade.transaction_hash,
            ))
            conn.commit()

    def save_trades(self, trades: List[Trade]) -> int:
        """Save multiple trades to the database."""
        count = 0
        for trade in trades:
            self.save_trade(trade)
            count += 1
        return count

    def get_trades(
        self,
        token_id: str = None,
        start_time: str = None,
        end_time: str = None,
        limit: int = 1000,
    ) -> List[Trade]:
        """Get trades from the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM trades WHERE 1=1"
            params = []

            if token_id:
                query += " AND token_id = ?"
                params.append(token_id)
            if start_time:
                query += " AND timestamp >= ?"
                params.append(start_time)
            if end_time:
                query += " AND timestamp <= ?"
                params.append(end_time)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            return [
                Trade(
                    id=row["id"],
                    token_id=row["token_id"],
                    price=row["price"],
                    size=row["size"],
                    side=row["side"],
                    maker=row["maker"] or "",
                    taker=row["taker"] or "",
                    timestamp=row["timestamp"] or "",
                    transaction_hash=row["transaction_hash"] or "",
                )
                for row in cursor.fetchall()
            ]

    # ==================== Price Snapshot Operations ====================

    def save_price_snapshot(self, snapshot: PriceSnapshot) -> None:
        """Save a price snapshot to the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO price_snapshots
                (token_id, price, bid, ask, volume, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                snapshot.token_id,
                snapshot.price,
                snapshot.bid,
                snapshot.ask,
                snapshot.volume,
                snapshot.timestamp,
            ))
            conn.commit()

    def get_price_history(
        self,
        token_id: str,
        start_time: str = None,
        end_time: str = None,
        limit: int = 1000,
    ) -> List[PriceSnapshot]:
        """Get price history for a token."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM price_snapshots WHERE token_id = ?"
            params = [token_id]

            if start_time:
                query += " AND timestamp >= ?"
                params.append(start_time)
            if end_time:
                query += " AND timestamp <= ?"
                params.append(end_time)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            return [
                PriceSnapshot(
                    token_id=row["token_id"],
                    price=row["price"],
                    bid=row["bid"],
                    ask=row["ask"],
                    volume=row["volume"] or 0,
                    timestamp=row["timestamp"],
                )
                for row in cursor.fetchall()
            ]

    # ==================== CSV Export Operations ====================

    def export_markets_csv(self, filename: str = "markets.csv") -> str:
        """Export markets to CSV file."""
        filepath = self.data_dir / filename
        markets = self.get_all_markets(active_only=False)

        with open(filepath, "w", newline="") as f:
            if markets:
                fieldnames = [
                    "condition_id", "question", "market_slug", "active",
                    "closed", "volume", "volume_24h", "liquidity",
                    "end_date_iso", "fetched_at"
                ]
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for market in markets:
                    writer.writerow({
                        "condition_id": market.condition_id,
                        "question": market.question,
                        "market_slug": market.market_slug,
                        "active": market.active,
                        "closed": market.closed,
                        "volume": market.volume,
                        "volume_24h": market.volume_24h,
                        "liquidity": market.liquidity,
                        "end_date_iso": market.end_date_iso,
                        "fetched_at": market.fetched_at,
                    })

        return str(filepath)

    def export_trades_csv(
        self,
        token_id: str = None,
        filename: str = None,
    ) -> str:
        """Export trades to CSV file."""
        if filename is None:
            suffix = f"_{token_id[:8]}" if token_id else ""
            filename = f"trades{suffix}.csv"

        filepath = self.data_dir / filename
        trades = self.get_trades(token_id=token_id)

        with open(filepath, "w", newline="") as f:
            if trades:
                fieldnames = [
                    "id", "token_id", "price", "size", "side",
                    "timestamp", "transaction_hash"
                ]
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for trade in trades:
                    writer.writerow({
                        "id": trade.id,
                        "token_id": trade.token_id,
                        "price": trade.price,
                        "size": trade.size,
                        "side": trade.side,
                        "timestamp": trade.timestamp,
                        "transaction_hash": trade.transaction_hash,
                    })

        return str(filepath)

    def export_price_history_csv(
        self,
        token_id: str,
        filename: str = None,
    ) -> str:
        """Export price history to CSV file."""
        if filename is None:
            filename = f"prices_{token_id[:8]}.csv"

        filepath = self.data_dir / filename
        snapshots = self.get_price_history(token_id)

        with open(filepath, "w", newline="") as f:
            if snapshots:
                fieldnames = ["token_id", "price", "bid", "ask", "volume", "timestamp"]
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for snapshot in snapshots:
                    writer.writerow(snapshot.to_dict())

        return str(filepath)

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM markets")
            market_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM markets WHERE active = 1")
            active_market_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM order_book_snapshots")
            order_book_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM trades")
            trade_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM price_snapshots")
            price_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT token_id) FROM order_book_snapshots")
            unique_tokens = cursor.fetchone()[0]

            return {
                "total_markets": market_count,
                "active_markets": active_market_count,
                "order_book_snapshots": order_book_count,
                "trades": trade_count,
                "price_snapshots": price_count,
                "unique_tokens_tracked": unique_tokens,
                "database_path": str(self.db_path),
            }
