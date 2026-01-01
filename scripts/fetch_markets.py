#!/usr/bin/env python3
"""
Script to fetch and store Polymarket market data.

This script fetches market data from Polymarket APIs and stores it
in both SQLite database and CSV files for analysis.

Usage:
    python scripts/fetch_markets.py [options]

Options:
    --all           Fetch all markets (not just active)
    --order-books   Also fetch order books for each market
    --trades        Also fetch recent trades
    --export-csv    Export data to CSV files
    --limit N       Limit number of markets to fetch
    --verbose       Enable verbose logging
"""

import sys
import os
import argparse
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api.client import PolymarketClient
from src.data.persistence import MarketDataStore
from src.data.models import Market, OrderBook, Trade, PriceSnapshot
from src.utils.logger import setup_logger
from src.utils.config import load_config


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Fetch and store Polymarket market data"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Fetch all markets (including inactive)"
    )
    parser.add_argument(
        "--order-books", action="store_true",
        help="Fetch order books for each market"
    )
    parser.add_argument(
        "--trades", action="store_true",
        help="Fetch recent trades"
    )
    parser.add_argument(
        "--export-csv", action="store_true",
        help="Export data to CSV files"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Limit number of markets to fetch"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose logging"
    )
    return parser.parse_args()


def fetch_markets(client: PolymarketClient, store: MarketDataStore,
                  logger, active_only: bool = True, limit: int = None) -> int:
    """
    Fetch and store markets.

    Returns:
        Number of markets fetched
    """
    logger.info(f"Fetching {'active' if active_only else 'all'} markets...")

    try:
        if limit:
            markets_data = client.get_markets(active=active_only, limit=limit)
        else:
            markets_data = client.get_all_markets(active=active_only)

        logger.info(f"Retrieved {len(markets_data)} markets from API")

        markets = []
        for data in markets_data:
            try:
                market = Market.from_api_response(data)
                markets.append(market)
            except Exception as e:
                logger.warning(f"Error parsing market data: {e}")

        count = store.save_markets(markets)
        logger.info(f"Saved {count} markets to database")
        return count

    except Exception as e:
        logger.error(f"Error fetching markets: {e}")
        raise


def fetch_order_books(client: PolymarketClient, store: MarketDataStore,
                      logger, limit: int = None) -> int:
    """
    Fetch order books for stored markets.

    Returns:
        Number of order book snapshots saved
    """
    logger.info("Fetching order books for markets...")

    markets = store.get_all_markets(active_only=True)
    if limit:
        markets = markets[:limit]

    count = 0
    for market in markets:
        tokens = market.tokens
        for token in tokens:
            token_id = token.get("token_id")
            if not token_id:
                continue

            try:
                book_data = client.get_order_book(token_id)
                order_book = OrderBook.from_api_response(token_id, book_data)
                store.save_order_book(order_book)

                # Also save price snapshot
                price_snapshot = PriceSnapshot.from_order_book(order_book)
                store.save_price_snapshot(price_snapshot)

                count += 1
                logger.debug(f"Saved order book for {token_id[:16]}...")

            except Exception as e:
                logger.warning(f"Error fetching order book for {token_id[:16]}: {e}")

    logger.info(f"Saved {count} order book snapshots")
    return count


def fetch_trades(client: PolymarketClient, store: MarketDataStore,
                 logger, limit: int = None) -> int:
    """
    Fetch recent trades.

    Returns:
        Number of trades saved
    """
    logger.info("Fetching recent trades...")

    try:
        # Fetch global recent trades
        trades_data = client.get_trades(limit=limit or 100)
        logger.info(f"Retrieved {len(trades_data)} trades from API")

        trades = []
        for data in trades_data:
            try:
                trade = Trade.from_api_response(data)
                trades.append(trade)
            except Exception as e:
                logger.warning(f"Error parsing trade data: {e}")

        count = store.save_trades(trades)
        logger.info(f"Saved {count} trades to database")
        return count

    except Exception as e:
        logger.error(f"Error fetching trades: {e}")
        raise


def display_summary(store: MarketDataStore, logger):
    """Display a summary of stored data."""
    stats = store.get_stats()

    logger.info("=" * 50)
    logger.info("DATA SUMMARY")
    logger.info("=" * 50)
    logger.info(f"Total markets:         {stats['total_markets']}")
    logger.info(f"Active markets:        {stats['active_markets']}")
    logger.info(f"Order book snapshots:  {stats['order_book_snapshots']}")
    logger.info(f"Trades:                {stats['trades']}")
    logger.info(f"Price snapshots:       {stats['price_snapshots']}")
    logger.info(f"Unique tokens tracked: {stats['unique_tokens_tracked']}")
    logger.info(f"Database path:         {stats['database_path']}")
    logger.info("=" * 50)


def main():
    """Main function to fetch and display markets."""
    args = parse_args()
    log_level = "DEBUG" if args.verbose else "INFO"
    logger = setup_logger("fetch_markets", level=log_level)

    logger.info("=" * 50)
    logger.info("POLYMARKET DATA FETCHER")
    logger.info(f"Started at: {datetime.utcnow().isoformat()}")
    logger.info("=" * 50)

    try:
        # Load configuration
        config = load_config()
        logger.debug("Configuration loaded")

        # Initialize client and store
        client = PolymarketClient()
        store = MarketDataStore()

        # Check API health
        logger.info("Checking API connectivity...")
        if client.health_check():
            logger.info("API connection successful")
        else:
            logger.warning("API health check failed, proceeding anyway...")

        # Fetch markets
        fetch_markets(
            client, store, logger,
            active_only=not args.all,
            limit=args.limit
        )

        # Fetch order books if requested
        if args.order_books:
            fetch_order_books(client, store, logger, limit=args.limit)

        # Fetch trades if requested
        if args.trades:
            fetch_trades(client, store, logger, limit=args.limit)

        # Export to CSV if requested
        if args.export_csv:
            logger.info("Exporting data to CSV...")
            markets_csv = store.export_markets_csv()
            logger.info(f"Exported markets to: {markets_csv}")

            if args.trades:
                trades_csv = store.export_trades_csv()
                logger.info(f"Exported trades to: {trades_csv}")

        # Display summary
        display_summary(store, logger)

        logger.info("Data fetch completed successfully!")
        return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130

    except Exception as e:
        logger.error(f"Error during data fetch: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
