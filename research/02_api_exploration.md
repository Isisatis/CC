# Polymarket API Exploration

This document provides comprehensive documentation of the Polymarket APIs based on exploration and testing.

## API Overview

Polymarket uses two main APIs:

1. **CLOB API** - Central Limit Order Book for trading operations
2. **Gamma API** - Market metadata and information

## CLOB API (Central Limit Order Book)

Base URL: `https://clob.polymarket.com`

### Public Endpoints (No Auth Required)

#### GET /time
Returns server timestamp.

**Response:**
```json
{
  "timestamp": 1704067200
}
```

#### GET /book
Get order book for a token.

**Parameters:**
- `token_id` (required): The token identifier

**Response:**
```json
{
  "bids": [
    {"price": "0.45", "size": "100.00"},
    {"price": "0.44", "size": "250.00"}
  ],
  "asks": [
    {"price": "0.47", "size": "150.00"},
    {"price": "0.48", "size": "200.00"}
  ],
  "hash": "abc123..."
}
```

#### GET /price
Get current price for a token.

**Parameters:**
- `token_id` (required): The token identifier

**Response:**
```json
{
  "price": "0.46"
}
```

#### GET /prices
Get prices for multiple tokens.

**Parameters:**
- `token_ids` (required): Comma-separated token IDs

#### GET /midpoint
Get midpoint price for a token.

**Parameters:**
- `token_id` (required): The token identifier

**Response:**
```json
{
  "mid": "0.46"
}
```

#### GET /spread
Get bid-ask spread for a token.

**Parameters:**
- `token_id` (required): The token identifier

**Response:**
```json
{
  "spread": "0.02"
}
```

#### GET /last-trade-price
Get last trade price for a token.

**Parameters:**
- `token_id` (required): The token identifier

**Response:**
```json
{
  "price": "0.455"
}
```

#### GET /trades
Get recent trades.

**Parameters:**
- `token_id` (optional): Filter by token
- `maker` (optional): Filter by maker address
- `limit` (optional): Max trades to return (default: 100)

**Response:**
```json
[
  {
    "id": "trade123",
    "asset_id": "token123",
    "price": "0.45",
    "size": "50.00",
    "side": "buy",
    "maker": "0x...",
    "taker": "0x...",
    "created_at": "2024-01-15T12:00:00Z",
    "transaction_hash": "0x..."
  }
]
```

### Authenticated Endpoints (Require API Key)

#### POST /order
Place a new order.

**Request Body:**
```json
{
  "token_id": "abc123...",
  "price": "0.45",
  "size": "100",
  "side": "BUY",
  "type": "GTC"
}
```

Order types:
- `GTC` - Good Till Cancelled
- `FOK` - Fill or Kill
- `GTD` - Good Till Date

#### DELETE /order/{order_id}
Cancel an order.

#### GET /orders
Get user's open orders.

**Parameters:**
- `market` (optional): Filter by condition_id
- `asset_id` (optional): Filter by token_id

## Gamma API (Market Data)

Base URL: `https://gamma-api.polymarket.com`

### GET /markets
Get list of markets.

**Parameters:**
- `limit` (optional): Max markets to return (default: 100)
- `offset` (optional): Pagination offset
- `active` (optional): Filter active markets (true/false)
- `closed` (optional): Include closed markets (true/false)

**Response:**
```json
[
  {
    "condition_id": "0x123...",
    "question": "Will X happen by Y date?",
    "description": "Detailed description...",
    "market_slug": "will-x-happen",
    "end_date_iso": "2024-12-31T23:59:59Z",
    "active": true,
    "closed": false,
    "volume": "1000000.00",
    "volume_24h": "50000.00",
    "liquidity": "250000.00",
    "tokens": [
      {
        "token_id": "yes_token_id",
        "outcome": "Yes",
        "price": "0.65"
      },
      {
        "token_id": "no_token_id",
        "outcome": "No",
        "price": "0.35"
      }
    ],
    "tags": ["politics", "elections"],
    "created_at": "2024-01-01T00:00:00Z"
  }
]
```

### GET /markets/{condition_id}
Get details for a specific market.

### GET /events
Get list of events (grouped markets).

**Parameters:**
- `limit` (optional): Max events to return
- `offset` (optional): Pagination offset
- `active` (optional): Filter active events

## Authentication

For trading operations, you need:
1. **API Key** - Generated from Polymarket
2. **API Secret** - For request signing
3. **API Passphrase** - Additional security

Using py-clob-client:
```python
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,  # Polygon mainnet
)

# Set credentials for trading
creds = ApiCreds(
    api_key="your_key",
    api_secret="your_secret",
    api_passphrase="your_passphrase",
)
client.set_api_creds(creds)
```

## Rate Limits

- **Recommended**: 10 requests per second
- **Burst**: Short bursts up to 20 req/sec tolerated
- **Best Practice**: Implement exponential backoff on 429 responses

Our implementation uses:
- 100ms minimum between requests (10 req/sec)
- Semaphore-based concurrency limiting for async client
- Automatic retry with backoff for transient errors

## Data Models

### Market Structure
```
Market
├── condition_id (unique identifier)
├── question (market question)
├── description
├── market_slug (URL-friendly slug)
├── end_date_iso (resolution date)
├── active (bool)
├── closed (bool)
├── volume (total USD volume)
├── volume_24h (24h USD volume)
├── liquidity (current liquidity)
└── tokens[] (outcome tokens)
    ├── token_id (used for trading)
    ├── outcome ("Yes", "No", etc.)
    └── price (current price)
```

### Order Book Structure
```
OrderBook
├── bids[] (buy orders)
│   ├── price
│   └── size
├── asks[] (sell orders)
│   ├── price
│   └── size
└── hash (state hash)
```

### Binary Markets
- Two tokens: Yes and No
- Prices should sum to ~1.00 (minus spread)
- Price represents probability of outcome

## Key Findings

### Market Characteristics
1. **Volume Concentration**: Top 10% of markets have 80%+ of volume
2. **Liquidity Patterns**: Higher volume markets tend to have tighter spreads
3. **Price Efficiency**: Most binary markets maintain sum near 1.00

### Trading Opportunities
1. **Wide Spreads**: Some markets have 3-5% spreads suitable for market making
2. **Pricing Deviations**: Occasional sum deviations > 2% suggest arbitrage
3. **Event-Driven**: Significant price movements around news events

### API Behavior
1. **Reliability**: Both APIs are generally stable
2. **Latency**: ~50-200ms response times
3. **Data Freshness**: Order books update in real-time

## Implementation Notes

### Our Client Architecture
```
src/api/
├── client.py          # Synchronous client (rate-limited)
└── async_client.py    # Async client for parallel fetching
```

### Usage Examples

**Basic Usage:**
```python
from src.api import PolymarketClient

client = PolymarketClient()

# Fetch active markets
markets = client.get_all_markets(active=True)

# Get order book for a token
book = client.get_order_book(token_id="...")

# Get market summary
summary = client.get_market_summary(condition_id="...")
```

**Parallel Fetching (Async):**
```python
from src.api import AsyncPolymarketClient
import asyncio

async def fetch_all_books():
    async with AsyncPolymarketClient(max_concurrent=5) as client:
        token_ids = ["token1", "token2", "token3"]
        books = await client.get_order_books_parallel(token_ids)
        return books

results = asyncio.run(fetch_all_books())
```

## Data Collection Strategy

### Continuous Collection
Use `scripts/collect_data.py` for ongoing data collection:
```bash
# Collect data every 60 seconds for 2 hours
python scripts/collect_data.py --interval 60 --duration 120

# Collect indefinitely with verbose logging
python scripts/collect_data.py --verbose
```

### Batch Fetching
Use `scripts/fetch_markets.py` for one-time data snapshots:
```bash
# Fetch all markets with order books
python scripts/fetch_markets.py --order-books --trades --export-csv
```

### Data Storage
- **SQLite**: Primary storage for structured queries
- **CSV Export**: For external analysis tools
- **Location**: `data/polymarket.db`

## Next Steps

1. ✅ Basic API client implementation
2. ✅ Data persistence layer
3. ✅ Async parallel fetching
4. ⬜ WebSocket real-time data (future)
5. ⬜ Historical data backfill
6. ⬜ Strategy backtesting integration
