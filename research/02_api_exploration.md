# Polymarket API Exploration

## API Endpoints

### CLOB API (Central Limit Order Book)
Base URL: `https://clob.polymarket.com`

Key endpoints to explore:
- `/markets` - Get all markets
- `/order` - Place orders
- `/order/:id` - Get order details
- `/orders` - Get user orders
- `/trades` - Get trade history
- `/book` - Get order book for a market

### Gamma API
Base URL: `https://gamma-api.polymarket.com`

Market data and information:
- `/markets` - Market metadata
- `/events` - Event information
- Market statistics and volume

## Authentication

Research needed:
- API key generation process
- Request signing
- Wallet integration for trading

## Data to Collect

1. **Market Data**
   - Active markets
   - Market volumes
   - Current prices
   - Liquidity depth

2. **Historical Data**
   - Price history
   - Trade history
   - Volume patterns
   - Market outcomes

3. **Order Book Data**
   - Bid/ask spreads
   - Depth analysis
   - Liquidity distribution

## Rate Limits

Document API rate limits and implement proper throttling.

## Testing Strategy

1. Start with read-only endpoints
2. Test data retrieval and parsing
3. Build data collection pipeline
4. Test order placement on testnet (if available)
