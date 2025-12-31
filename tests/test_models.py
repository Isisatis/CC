"""Unit tests for data models."""

import pytest
from datetime import datetime

from src.data.models import (
    Market,
    OrderBook,
    OrderBookLevel,
    Trade,
    PriceSnapshot,
)


class TestMarket:
    """Tests for Market model."""

    def test_create_market(self):
        """Test creating a Market instance."""
        market = Market(
            condition_id="test123",
            question="Will X happen?",
            description="Test market",
        )
        assert market.condition_id == "test123"
        assert market.question == "Will X happen?"
        assert market.active is True
        assert market.volume == 0.0

    def test_from_api_response(self, sample_market_data):
        """Test creating Market from API response."""
        market = Market.from_api_response(sample_market_data)

        assert market.condition_id == sample_market_data["condition_id"]
        assert market.question == sample_market_data["question"]
        assert market.volume == 1500000.50
        assert market.volume_24h == 75000.25
        assert len(market.tokens) == 2
        assert market.active is True

    def test_to_dict(self, sample_market_data):
        """Test converting Market to dictionary."""
        market = Market.from_api_response(sample_market_data)
        data = market.to_dict()

        assert isinstance(data, dict)
        assert data["condition_id"] == market.condition_id
        assert data["question"] == market.question
        assert "fetched_at" in data

    def test_to_json(self, sample_market_data):
        """Test converting Market to JSON."""
        market = Market.from_api_response(sample_market_data)
        json_str = market.to_json()

        assert isinstance(json_str, str)
        assert market.condition_id in json_str

    def test_handles_missing_fields(self):
        """Test that Market handles missing optional fields."""
        minimal_data = {
            "condition_id": "test123",
            "question": "Test?",
        }
        market = Market.from_api_response(minimal_data)

        assert market.condition_id == "test123"
        assert market.volume == 0.0
        assert market.tokens == []

    def test_handles_null_values(self):
        """Test that Market handles null values in response."""
        data = {
            "condition_id": "test123",
            "question": "Test?",
            "volume": None,
            "volume_24h": None,
            "liquidity": None,
        }
        market = Market.from_api_response(data)

        assert market.volume == 0.0
        assert market.volume_24h == 0.0
        assert market.liquidity == 0.0


class TestOrderBook:
    """Tests for OrderBook model."""

    def test_create_order_book(self):
        """Test creating an OrderBook instance."""
        book = OrderBook(
            token_id="token123",
            bids=[OrderBookLevel(price=0.45, size=100)],
            asks=[OrderBookLevel(price=0.55, size=100)],
        )
        assert book.token_id == "token123"
        assert len(book.bids) == 1
        assert len(book.asks) == 1

    def test_from_api_response(self, sample_order_book_data):
        """Test creating OrderBook from API response."""
        book = OrderBook.from_api_response("token123", sample_order_book_data)

        assert book.token_id == "token123"
        assert len(book.bids) == 3
        assert len(book.asks) == 3
        assert book.bids[0].price == 0.45
        assert book.asks[0].price == 0.47

    def test_best_bid(self, sample_order_book_data):
        """Test best_bid property."""
        book = OrderBook.from_api_response("token123", sample_order_book_data)
        assert book.best_bid == 0.45  # Highest bid

    def test_best_ask(self, sample_order_book_data):
        """Test best_ask property."""
        book = OrderBook.from_api_response("token123", sample_order_book_data)
        assert book.best_ask == 0.47  # Lowest ask

    def test_spread(self, sample_order_book_data):
        """Test spread calculation."""
        book = OrderBook.from_api_response("token123", sample_order_book_data)
        assert book.spread == pytest.approx(0.02, rel=1e-6)

    def test_spread_pct(self, sample_order_book_data):
        """Test spread percentage calculation."""
        book = OrderBook.from_api_response("token123", sample_order_book_data)
        expected_midpoint = (0.45 + 0.47) / 2
        expected_spread_pct = (0.02 / expected_midpoint) * 100
        assert book.spread_pct == pytest.approx(expected_spread_pct, rel=1e-4)

    def test_midpoint(self, sample_order_book_data):
        """Test midpoint calculation."""
        book = OrderBook.from_api_response("token123", sample_order_book_data)
        assert book.midpoint == pytest.approx(0.46, rel=1e-6)

    def test_total_bid_size(self, sample_order_book_data):
        """Test total bid size calculation."""
        book = OrderBook.from_api_response("token123", sample_order_book_data)
        assert book.total_bid_size == 8500.0  # 1000 + 2500 + 5000

    def test_total_ask_size(self, sample_order_book_data):
        """Test total ask size calculation."""
        book = OrderBook.from_api_response("token123", sample_order_book_data)
        assert book.total_ask_size == 5300.0  # 800 + 1500 + 3000

    def test_empty_order_book(self):
        """Test properties with empty order book."""
        book = OrderBook(token_id="token123", bids=[], asks=[])

        assert book.best_bid is None
        assert book.best_ask is None
        assert book.spread is None
        assert book.midpoint is None

    def test_to_dict(self, sample_order_book_data):
        """Test converting OrderBook to dictionary."""
        book = OrderBook.from_api_response("token123", sample_order_book_data)
        data = book.to_dict()

        assert data["token_id"] == "token123"
        assert data["best_bid"] == 0.45
        assert data["best_ask"] == 0.47
        assert "timestamp" in data


class TestTrade:
    """Tests for Trade model."""

    def test_create_trade(self):
        """Test creating a Trade instance."""
        trade = Trade(
            id="trade123",
            token_id="token456",
            price=0.50,
            size=100.0,
            side="buy",
        )
        assert trade.id == "trade123"
        assert trade.price == 0.50
        assert trade.side == "buy"

    def test_from_api_response(self, sample_trade_data):
        """Test creating Trade from API response."""
        trade = Trade.from_api_response(sample_trade_data)

        assert trade.id == "trade_123456"
        assert trade.price == 0.46
        assert trade.size == 500.0
        assert trade.side == "buy"

    def test_to_dict(self, sample_trade_data):
        """Test converting Trade to dictionary."""
        trade = Trade.from_api_response(sample_trade_data)
        data = trade.to_dict()

        assert data["id"] == trade.id
        assert data["price"] == trade.price
        assert data["side"] == trade.side


class TestPriceSnapshot:
    """Tests for PriceSnapshot model."""

    def test_create_price_snapshot(self):
        """Test creating a PriceSnapshot instance."""
        snapshot = PriceSnapshot(
            token_id="token123",
            price=0.50,
            bid=0.48,
            ask=0.52,
        )
        assert snapshot.token_id == "token123"
        assert snapshot.price == 0.50

    def test_from_order_book(self, sample_order_book_data):
        """Test creating PriceSnapshot from OrderBook."""
        book = OrderBook.from_api_response("token123", sample_order_book_data)
        snapshot = PriceSnapshot.from_order_book(book)

        assert snapshot.token_id == "token123"
        assert snapshot.price == book.midpoint
        assert snapshot.bid == book.best_bid
        assert snapshot.ask == book.best_ask

    def test_to_dict(self):
        """Test converting PriceSnapshot to dictionary."""
        snapshot = PriceSnapshot(
            token_id="token123",
            price=0.50,
            bid=0.48,
            ask=0.52,
        )
        data = snapshot.to_dict()

        assert data["token_id"] == "token123"
        assert data["price"] == 0.50
        assert "timestamp" in data


class TestOrderBookLevel:
    """Tests for OrderBookLevel model."""

    def test_create_level(self):
        """Test creating an OrderBookLevel."""
        level = OrderBookLevel(price=0.50, size=1000.0)
        assert level.price == 0.50
        assert level.size == 1000.0

    def test_to_dict(self):
        """Test converting OrderBookLevel to dictionary."""
        level = OrderBookLevel(price=0.50, size=1000.0)
        data = level.to_dict()

        assert data == {"price": 0.50, "size": 1000.0}
