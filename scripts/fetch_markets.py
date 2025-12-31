#!/usr/bin/env python3
"""
Polymarket API Validation & Demo Script

Validates API connectivity and demonstrates all available functionality:
- Market discovery and filtering
- Order book analysis
- Liquidity metrics
- Trade history
"""

import sys
import os
import json
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api import (
    PolymarketClient,
    MarketFilter,
    MarketStatus,
)
from src.utils.logger import setup_logger

logger = setup_logger("fetch_markets", level="INFO")


def print_section(title: str):
    """Print a section header"""
    print(f"\n{'='*60}")
    print(f" {title}")
    print(f"{'='*60}\n")


def validate_connectivity(client: PolymarketClient) -> bool:
    """Check API connectivity"""
    print_section("API Connectivity Check")

    status = client.health_check()
    print(f"Gamma API: {'✓ Connected' if status['gamma'] else '✗ Failed'}")
    print(f"CLOB API:  {'✓ Connected' if status['clob'] else '✗ Failed'}")

    return status['gamma'] and status['clob']


def demo_market_discovery(client: PolymarketClient):
    """Demonstrate market discovery features"""
    print_section("Market Discovery")

    # Get active markets
    print("Fetching top 10 active markets by 24h volume...")
    active = client.get_active_markets(limit=10)
    print(f"Found {len(active)} active markets\n")

    for i, m in enumerate(active[:5], 1):
        print(f"{i}. {m.question[:70]}...")
        print(f"   ID: {m.id}")
        print(f"   Volume 24h: ${m.volume_24hr:,.0f}" if m.volume_24hr else "   Volume 24h: N/A")
        print(f"   Liquidity: ${m.liquidity:,.0f}" if m.liquidity else "   Liquidity: N/A")
        print(f"   Tokens: {len(m.tokens)}")
        print()


def demo_market_filtering(client: PolymarketClient):
    """Demonstrate filtering capabilities"""
    print_section("Market Filtering")

    # Filter by liquidity
    print("Markets with >$50k liquidity and >$10k 24h volume:")
    filter_high_liq = MarketFilter(
        status=MarketStatus.ACTIVE,
        min_liquidity=50000,
        min_volume_24h=10000,
        limit=5
    )
    high_liq = client.get_markets(filter_high_liq)
    for m in high_liq:
        print(f"  • {m.question[:60]}... (Liq: ${m.liquidity:,.0f})")
    print(f"\nTotal: {len(high_liq)} markets matching criteria")

    # Get closed markets
    print("\nRecently closed/resolved markets:")
    closed = client.get_closed_markets(limit=5)
    for m in closed[:3]:
        print(f"  • {m.question[:60]}...")
        if m.tokens:
            winners = [t for t in m.tokens if t.get("winner")]
            if winners:
                print(f"    Winner: {winners[0].get('outcome', 'Unknown')}")


def demo_order_book(client: PolymarketClient):
    """Demonstrate order book analysis"""
    print_section("Order Book Analysis")

    # Get a liquid market
    markets = client.get_active_markets(min_liquidity=10000, limit=1)
    if not markets:
        print("No liquid markets found")
        return

    market = markets[0]
    print(f"Market: {market.question[:70]}...")
    print()

    # Get order books for all tokens
    books = client.get_order_books_for_market(market)

    for token_id, book in books.items():
        outcome = next((t.get("outcome") for t in market.tokens if t.get("token_id") == token_id), "Unknown")
        print(f"Outcome: {outcome}")
        print(f"  Best Bid: {book.best_bid:.4f}" if book.best_bid else "  Best Bid: None")
        print(f"  Best Ask: {book.best_ask:.4f}" if book.best_ask else "  Best Ask: None")
        print(f"  Spread: {book.spread:.4f} ({book.spread_pct:.2f}%)" if book.spread else "  Spread: N/A")
        print(f"  Bid Depth: {book.total_bid_depth:,.0f} shares (${book.total_bid_value:,.0f})")
        print(f"  Ask Depth: {book.total_ask_depth:,.0f} shares (${book.total_ask_value:,.0f})")
        print()


def demo_liquidity_metrics(client: PolymarketClient):
    """Demonstrate liquidity analysis"""
    print_section("Liquidity Analysis")

    markets = client.get_active_markets(min_liquidity=20000, limit=3)
    if not markets:
        print("No liquid markets found")
        return

    for market in markets:
        print(f"Market: {market.question[:60]}...")

        liq = client.get_market_liquidity(market)
        for token_id, metrics in liq.items():
            outcome = next((t.get("outcome") for t in market.tokens if t.get("token_id") == token_id), "Unknown")
            print(f"\n  {outcome}:")
            print(f"    Liquidity Score: {metrics.liquidity_score:.1f}/100")
            print(f"    Depth Score: {metrics.depth_score:.1f}/100")
            print(f"    Spread Score: {metrics.spread_score:.1f}/100")
            print(f"    Spread: {metrics.spread_bps:.0f} bps" if metrics.spread_bps else "    Spread: N/A")
            print(f"    Tradeable: {'Yes' if metrics.is_tradeable else 'No'}")

            if metrics.impact_buy_100:
                print(f"    Price Impact ($100 buy): {metrics.impact_buy_100:.2%}")
            if metrics.volume_24h:
                print(f"    24h Volume: ${metrics.volume_24h:,.0f}")
        print()


def demo_trade_history(client: PolymarketClient):
    """Demonstrate trade history"""
    print_section("Recent Trades")

    markets = client.get_active_markets(min_volume=5000, limit=1)
    if not markets:
        print("No active markets with recent volume")
        return

    market = markets[0]
    print(f"Market: {market.question[:70]}...")
    print()

    # Get first token
    token_id = market.tokens[0].get("token_id") if market.tokens else None
    if not token_id:
        print("No token ID found")
        return

    trades = client.get_trades(token_id=token_id, limit=10)
    print(f"Recent trades for {market.tokens[0].get('outcome', 'Unknown')}:")
    print(f"{'Time':<20} {'Side':<6} {'Price':<10} {'Size':<12} {'Value':<10}")
    print("-" * 60)

    for t in trades.trades[:10]:
        time_str = t.datetime.strftime("%Y-%m-%d %H:%M") if t.datetime else "Unknown"
        print(f"{time_str:<20} {t.side:<6} {t.price:<10.4f} {t.size:<12,.0f} ${t.value:<10,.2f}")

    if trades.trades:
        print(f"\nVWAP: {trades.vwap:.4f}")
        print(f"Total Volume: ${trades.total_volume:,.2f}")


def demo_events(client: PolymarketClient):
    """Demonstrate event access"""
    print_section("Market Events")

    events = client.get_events(active_only=True, limit=5)
    print(f"Top {len(events)} active events by volume:\n")

    for e in events:
        print(f"• {e.title}")
        print(f"  Markets: {len(e.markets)}")
        if e.volume:
            print(f"  Volume: ${e.volume:,.0f}")
        print()


def main():
    """Main validation function"""
    print("\n" + "="*60)
    print(" POLYMARKET API VALIDATION & DEMO")
    print(f" {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)

    client = PolymarketClient()

    # Check connectivity first
    if not validate_connectivity(client):
        logger.error("API connectivity check failed")
        return 1

    try:
        demo_market_discovery(client)
        demo_market_filtering(client)
        demo_order_book(client)
        demo_liquidity_metrics(client)
        demo_trade_history(client)
        demo_events(client)

        print_section("Validation Complete")
        print("All API endpoints validated successfully!")
        print("\nAvailable functionality:")
        print("  • Market discovery (active/closed/resolved)")
        print("  • Market filtering by liquidity, volume, status")
        print("  • Order book access with depth analysis")
        print("  • Liquidity metrics (scores, price impact)")
        print("  • Trade history with VWAP calculation")
        print("  • Event grouping access")

    except Exception as e:
        logger.error(f"Validation failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
