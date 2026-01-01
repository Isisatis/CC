"""Unit tests for data persistence layer."""

import os
import pytest
import sqlite3
from datetime import datetime

from src.data.persistence import DataStore, MarketDataStore
from src.data.models import Market, OrderBook, OrderBookLevel, Trade, PriceSnapshot


class TestDataStore:
    """Tests for base DataStore class."""

    def test_creates_data_directory(self, temp_data_dir):
        """Test that DataStore creates data directory."""
        path = os.path.join(temp_data_dir, "nested", "data")
        store = DataStore(data_dir=path)

        assert os.path.exists(path)
        assert store.data_dir.exists()


class TestMarketDataStore:
    """Tests for MarketDataStore class."""

    @pytest.fixture
    def store(self, temp_data_dir):
        """Create a MarketDataStore with temp directory."""
        return MarketDataStore(data_dir=temp_data_dir)

    @pytest.fixture
    def sample_market(self, sample_market_data):
        """Create a sample Market object."""
        return Market.from_api_response(sample_market_data)

    def test_init_creates_tables(self, store):
        """Test that initialization creates required tables."""
        # Check tables exist by querying sqlite_master
        with store._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row["name"] for row in cursor.fetchall()}

        assert "markets" in tables
        assert "order_book_snapshots" in tables
        assert "trades" in tables
        assert "price_snapshots" in tables

    def test_init_creates_indexes(self, store):
        """Test that initialization creates indexes."""
        with store._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
            indexes = {row["name"] for row in cursor.fetchall()}

        assert "idx_order_book_token_time" in indexes
        assert "idx_trades_token_time" in indexes


class TestMarketDataStoreMarkets:
    """Tests for market-related operations."""

    @pytest.fixture
    def store(self, temp_data_dir):
        """Create a MarketDataStore with temp directory."""
        return MarketDataStore(data_dir=temp_data_dir)

    @pytest.fixture
    def sample_market(self, sample_market_data):
        """Create a sample Market object."""
        return Market.from_api_response(sample_market_data)

    def test_save_market(self, store, sample_market):
        """Test saving a single market."""
        store.save_market(sample_market)

        retrieved = store.get_market(sample_market.condition_id)
        assert retrieved is not None
        assert retrieved.question == sample_market.question

    def test_save_markets_batch(self, store, sample_market_data):
        """Test saving multiple markets."""
        markets = []
        for i in range(5):
            data = sample_market_data.copy()
            data["condition_id"] = f"condition_{i}"
            data["question"] = f"Question {i}?"
            markets.append(Market.from_api_response(data))

        count = store.save_markets(markets)

        assert count == 5
        all_markets = store.get_all_markets(active_only=False)
        assert len(all_markets) == 5

    def test_get_market_not_found(self, store):
        """Test getting a non-existent market returns None."""
        result = store.get_market("nonexistent")
        assert result is None

    def test_get_all_markets_active_only(self, store, sample_market_data):
        """Test filtering active markets."""
        # Save active market
        active_data = sample_market_data.copy()
        active_data["condition_id"] = "active"
        active_data["active"] = True
        store.save_market(Market.from_api_response(active_data))

        # Save inactive market
        inactive_data = sample_market_data.copy()
        inactive_data["condition_id"] = "inactive"
        inactive_data["active"] = False
        store.save_market(Market.from_api_response(inactive_data))

        active_markets = store.get_all_markets(active_only=True)
        all_markets = store.get_all_markets(active_only=False)

        assert len(active_markets) == 1
        assert len(all_markets) == 2

    def test_update_existing_market(self, store, sample_market):
        """Test that saving an existing market updates it."""
        store.save_market(sample_market)

        # Modify and save again
        sample_market.volume = 9999999.0
        store.save_market(sample_market)

        retrieved = store.get_market(sample_market.condition_id)
        assert retrieved.volume == 9999999.0


class TestMarketDataStoreOrderBooks:
    """Tests for order book operations."""

    @pytest.fixture
    def store(self, temp_data_dir):
        """Create a MarketDataStore with temp directory."""
        return MarketDataStore(data_dir=temp_data_dir)

    @pytest.fixture
    def sample_order_book(self, sample_order_book_data):
        """Create a sample OrderBook object."""
        return OrderBook.from_api_response("token123", sample_order_book_data)

    def test_save_order_book(self, store, sample_order_book):
        """Test saving an order book snapshot."""
        store.save_order_book(sample_order_book)

        history = store.get_order_book_history("token123", limit=10)
        assert len(history) == 1
        assert history[0]["token_id"] == "token123"

    def test_order_book_history_ordering(self, store, sample_order_book_data):
        """Test that order book history is ordered by timestamp."""
        for i in range(3):
            book = OrderBook.from_api_response("token123", sample_order_book_data)
            book.timestamp = f"2024-01-0{i+1}T00:00:00"
            store.save_order_book(book)

        history = store.get_order_book_history("token123")

        # Should be in descending order (newest first)
        timestamps = [h["timestamp"] for h in history]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_order_book_history_time_filter(self, store, sample_order_book_data):
        """Test filtering order book history by time range."""
        for i in range(5):
            book = OrderBook.from_api_response("token123", sample_order_book_data)
            book.timestamp = f"2024-01-0{i+1}T00:00:00"
            store.save_order_book(book)

        history = store.get_order_book_history(
            "token123",
            start_time="2024-01-02T00:00:00",
            end_time="2024-01-04T00:00:00",
        )

        assert len(history) == 3


class TestMarketDataStoreTrades:
    """Tests for trade operations."""

    @pytest.fixture
    def store(self, temp_data_dir):
        """Create a MarketDataStore with temp directory."""
        return MarketDataStore(data_dir=temp_data_dir)

    @pytest.fixture
    def sample_trade(self, sample_trade_data):
        """Create a sample Trade object."""
        return Trade.from_api_response(sample_trade_data)

    def test_save_trade(self, store, sample_trade):
        """Test saving a single trade."""
        store.save_trade(sample_trade)

        trades = store.get_trades(token_id=sample_trade.token_id)
        assert len(trades) == 1
        assert trades[0].id == sample_trade.id

    def test_save_trades_batch(self, store, sample_trade_data):
        """Test saving multiple trades."""
        trades = []
        for i in range(10):
            data = sample_trade_data.copy()
            data["id"] = f"trade_{i}"
            trades.append(Trade.from_api_response(data))

        count = store.save_trades(trades)

        assert count == 10
        all_trades = store.get_trades(limit=100)
        assert len(all_trades) == 10

    def test_duplicate_trade_ignored(self, store, sample_trade):
        """Test that duplicate trades are ignored."""
        store.save_trade(sample_trade)
        store.save_trade(sample_trade)  # Same trade again

        trades = store.get_trades()
        assert len(trades) == 1

    def test_get_trades_with_token_filter(self, store, sample_trade_data):
        """Test filtering trades by token_id."""
        for i, token in enumerate(["token_a", "token_b", "token_a"]):
            data = sample_trade_data.copy()
            data["id"] = f"trade_{i}"
            data["asset_id"] = token
            store.save_trade(Trade.from_api_response(data))

        trades_a = store.get_trades(token_id="token_a")
        trades_b = store.get_trades(token_id="token_b")

        assert len(trades_a) == 2
        assert len(trades_b) == 1


class TestMarketDataStorePriceSnapshots:
    """Tests for price snapshot operations."""

    @pytest.fixture
    def store(self, temp_data_dir):
        """Create a MarketDataStore with temp directory."""
        return MarketDataStore(data_dir=temp_data_dir)

    def test_save_price_snapshot(self, store):
        """Test saving a price snapshot."""
        snapshot = PriceSnapshot(
            token_id="token123",
            price=0.55,
            bid=0.54,
            ask=0.56,
        )
        store.save_price_snapshot(snapshot)

        history = store.get_price_history("token123")
        assert len(history) == 1
        assert history[0].price == 0.55

    def test_price_history_ordering(self, store):
        """Test that price history is ordered by timestamp."""
        for i in range(3):
            snapshot = PriceSnapshot(
                token_id="token123",
                price=0.50 + i * 0.01,
                timestamp=f"2024-01-0{i+1}T00:00:00",
            )
            store.save_price_snapshot(snapshot)

        history = store.get_price_history("token123")

        # Should be in descending order
        timestamps = [s.timestamp for s in history]
        assert timestamps == sorted(timestamps, reverse=True)


class TestMarketDataStoreExport:
    """Tests for CSV export functionality."""

    @pytest.fixture
    def store(self, temp_data_dir):
        """Create a MarketDataStore with temp directory."""
        return MarketDataStore(data_dir=temp_data_dir)

    def test_export_markets_csv(self, store, sample_market_data):
        """Test exporting markets to CSV."""
        for i in range(3):
            data = sample_market_data.copy()
            data["condition_id"] = f"market_{i}"
            store.save_market(Market.from_api_response(data))

        csv_path = store.export_markets_csv("test_markets.csv")

        assert os.path.exists(csv_path)

        # Check contents
        with open(csv_path, "r") as f:
            lines = f.readlines()
        assert len(lines) == 4  # Header + 3 markets

    def test_export_trades_csv(self, store, sample_trade_data):
        """Test exporting trades to CSV."""
        for i in range(5):
            data = sample_trade_data.copy()
            data["id"] = f"trade_{i}"
            store.save_trade(Trade.from_api_response(data))

        csv_path = store.export_trades_csv(filename="test_trades.csv")

        assert os.path.exists(csv_path)

        with open(csv_path, "r") as f:
            lines = f.readlines()
        assert len(lines) == 6  # Header + 5 trades

    def test_export_empty_table(self, store):
        """Test exporting empty table creates file with header only."""
        csv_path = store.export_markets_csv("empty.csv")

        assert os.path.exists(csv_path)


class TestMarketDataStoreStats:
    """Tests for statistics functionality."""

    @pytest.fixture
    def store(self, temp_data_dir):
        """Create a MarketDataStore with temp directory."""
        return MarketDataStore(data_dir=temp_data_dir)

    def test_get_stats_empty(self, store):
        """Test stats on empty database."""
        stats = store.get_stats()

        assert stats["total_markets"] == 0
        assert stats["active_markets"] == 0
        assert stats["trades"] == 0
        assert "database_path" in stats

    def test_get_stats_with_data(self, store, sample_market_data, sample_trade_data):
        """Test stats with data."""
        # Add some markets
        for i in range(3):
            data = sample_market_data.copy()
            data["condition_id"] = f"market_{i}"
            store.save_market(Market.from_api_response(data))

        # Add some trades
        for i in range(5):
            data = sample_trade_data.copy()
            data["id"] = f"trade_{i}"
            store.save_trade(Trade.from_api_response(data))

        stats = store.get_stats()

        assert stats["total_markets"] == 3
        assert stats["active_markets"] == 3
        assert stats["trades"] == 5
