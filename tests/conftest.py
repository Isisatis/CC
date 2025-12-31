"""Pytest fixtures and configuration."""

import os
import sys
import tempfile
import pytest

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def temp_db_path():
    """Create a temporary database path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        yield f.name
    # Cleanup
    if os.path.exists(f.name):
        os.unlink(f.name)


@pytest.fixture
def temp_data_dir():
    """Create a temporary data directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_market_data():
    """Sample market data from API."""
    return {
        "condition_id": "0x" + "a" * 64,
        "question": "Will Bitcoin reach $100k?",
        "description": "Market for BTC price prediction",
        "market_slug": "btc-100k",
        "end_date_iso": "2025-12-31T23:59:59Z",
        "active": True,
        "closed": False,
        "volume": "1500000.50",
        "volume_24h": "75000.25",
        "liquidity": "250000.00",
        "tokens": [
            {
                "token_id": "0x" + "b" * 64,
                "outcome": "Yes",
                "price": "0.65",
            },
            {
                "token_id": "0x" + "c" * 64,
                "outcome": "No",
                "price": "0.35",
            },
        ],
        "tags": ["crypto", "bitcoin"],
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-06-15T12:00:00Z",
    }


@pytest.fixture
def sample_order_book_data():
    """Sample order book data from API."""
    return {
        "bids": [
            {"price": "0.45", "size": "1000.00"},
            {"price": "0.44", "size": "2500.00"},
            {"price": "0.43", "size": "5000.00"},
        ],
        "asks": [
            {"price": "0.47", "size": "800.00"},
            {"price": "0.48", "size": "1500.00"},
            {"price": "0.50", "size": "3000.00"},
        ],
        "hash": "abc123",
    }


@pytest.fixture
def sample_trade_data():
    """Sample trade data from API."""
    return {
        "id": "trade_123456",
        "asset_id": "0x" + "b" * 64,
        "price": "0.46",
        "size": "500.00",
        "side": "buy",
        "maker": "0x" + "d" * 40,
        "taker": "0x" + "e" * 40,
        "created_at": "2024-06-15T10:30:00Z",
        "transaction_hash": "0x" + "f" * 64,
    }
