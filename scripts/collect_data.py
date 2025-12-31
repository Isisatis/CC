#!/usr/bin/env python3
"""
Continuous data collection script for Polymarket.

This script runs continuously (or for a specified duration) collecting
market data at regular intervals for historical analysis.

Usage:
    python scripts/collect_data.py [options]

Options:
    --interval N     Collection interval in seconds (default: 60)
    --duration N     Run for N minutes then stop (default: infinite)
    --markets N      Number of top markets to track (default: 20)
    --verbose        Enable verbose logging
"""

import sys
import os
import argparse
import time
import signal
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api.client import PolymarketClient
from src.data.persistence import MarketDataStore
from src.data.models import Market, OrderBook, PriceSnapshot, Trade
from src.utils.logger import setup_logger


class DataCollector:
    """Continuous data collector for Polymarket."""

    def __init__(
        self,
        client: PolymarketClient,
        store: MarketDataStore,
        logger,
        interval: int = 60,
        top_n_markets: int = 20,
    ):
        self.client = client
        self.store = store
        self.logger = logger
        self.interval = interval
        self.top_n_markets = top_n_markets
        self.running = True
        self.tracked_tokens = []
        self.collection_count = 0

        # Handle graceful shutdown
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signal."""
        self.logger.info("Shutdown signal received, finishing current cycle...")
        self.running = False

    def initialize(self):
        """Initialize collector by fetching markets and selecting tokens to track."""
        self.logger.info("Initializing data collector...")

        # Fetch current markets
        markets_data = self.client.get_all_markets(active=True)
        markets = [Market.from_api_response(m) for m in markets_data]

        # Save markets to database
        self.store.save_markets(markets)
        self.logger.info(f"Loaded {len(markets)} active markets")

        # Sort by volume and select top markets
        markets_sorted = sorted(markets, key=lambda m: m.volume, reverse=True)
        top_markets = markets_sorted[:self.top_n_markets]

        # Extract token IDs to track
        self.tracked_tokens = []
        for market in top_markets:
            for token in market.tokens:
                token_id = token.get("token_id")
                if token_id:
                    self.tracked_tokens.append({
                        "token_id": token_id,
                        "outcome": token.get("outcome", ""),
                        "market_question": market.question[:50],
                    })

        self.logger.info(
            f"Tracking {len(self.tracked_tokens)} tokens from top {len(top_markets)} markets"
        )

    def collect_snapshot(self):
        """Collect a single data snapshot for all tracked tokens."""
        self.collection_count += 1
        timestamp = datetime.utcnow().isoformat()
        success_count = 0
        error_count = 0

        self.logger.debug(f"Collection #{self.collection_count} at {timestamp}")

        for token_info in self.tracked_tokens:
            token_id = token_info["token_id"]

            try:
                # Fetch order book
                book_data = self.client.get_order_book(token_id)
                order_book = OrderBook.from_api_response(token_id, book_data)
                self.store.save_order_book(order_book)

                # Save price snapshot
                price_snapshot = PriceSnapshot.from_order_book(order_book)
                self.store.save_price_snapshot(price_snapshot)

                success_count += 1

            except Exception as e:
                error_count += 1
                self.logger.debug(f"Error collecting {token_id[:16]}: {e}")

        # Fetch recent trades periodically
        if self.collection_count % 5 == 0:  # Every 5 intervals
            try:
                trades_data = self.client.get_trades(limit=100)
                trades = [Trade.from_api_response(t) for t in trades_data]
                self.store.save_trades(trades)
                self.logger.debug(f"Saved {len(trades)} trades")
            except Exception as e:
                self.logger.warning(f"Error fetching trades: {e}")

        self.logger.info(
            f"Collection #{self.collection_count}: "
            f"{success_count} success, {error_count} errors"
        )

    def run(self, duration_minutes: int = None):
        """
        Run the data collector.

        Args:
            duration_minutes: Run for this many minutes, or None for infinite
        """
        self.logger.info("=" * 50)
        self.logger.info("POLYMARKET DATA COLLECTOR")
        self.logger.info(f"Interval: {self.interval} seconds")
        self.logger.info(f"Tracking: {len(self.tracked_tokens)} tokens")
        if duration_minutes:
            self.logger.info(f"Duration: {duration_minutes} minutes")
        else:
            self.logger.info("Duration: Running until stopped (Ctrl+C)")
        self.logger.info("=" * 50)

        end_time = None
        if duration_minutes:
            end_time = datetime.utcnow() + timedelta(minutes=duration_minutes)

        while self.running:
            cycle_start = time.time()

            # Collect data
            self.collect_snapshot()

            # Check if we should stop
            if end_time and datetime.utcnow() >= end_time:
                self.logger.info("Duration reached, stopping...")
                break

            # Wait for next interval
            elapsed = time.time() - cycle_start
            sleep_time = max(0, self.interval - elapsed)

            if sleep_time > 0 and self.running:
                self.logger.debug(f"Sleeping for {sleep_time:.1f} seconds...")
                time.sleep(sleep_time)

        # Final summary
        self.print_summary()

    def print_summary(self):
        """Print collection summary."""
        stats = self.store.get_stats()

        self.logger.info("=" * 50)
        self.logger.info("COLLECTION SUMMARY")
        self.logger.info("=" * 50)
        self.logger.info(f"Total collections:     {self.collection_count}")
        self.logger.info(f"Markets in database:   {stats['total_markets']}")
        self.logger.info(f"Order book snapshots:  {stats['order_book_snapshots']}")
        self.logger.info(f"Price snapshots:       {stats['price_snapshots']}")
        self.logger.info(f"Trades recorded:       {stats['trades']}")
        self.logger.info(f"Database path:         {stats['database_path']}")
        self.logger.info("=" * 50)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Continuous Polymarket data collection"
    )
    parser.add_argument(
        "--interval", type=int, default=60,
        help="Collection interval in seconds (default: 60)"
    )
    parser.add_argument(
        "--duration", type=int, default=None,
        help="Run for N minutes then stop (default: run forever)"
    )
    parser.add_argument(
        "--markets", type=int, default=20,
        help="Number of top markets to track (default: 20)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose logging"
    )
    return parser.parse_args()


def main():
    """Main function."""
    args = parse_args()
    log_level = "DEBUG" if args.verbose else "INFO"
    logger = setup_logger("data_collector", level=log_level)

    try:
        # Initialize components
        client = PolymarketClient()
        store = MarketDataStore()

        # Create and run collector
        collector = DataCollector(
            client=client,
            store=store,
            logger=logger,
            interval=args.interval,
            top_n_markets=args.markets,
        )

        collector.initialize()
        collector.run(duration_minutes=args.duration)

        return 0

    except Exception as e:
        logger.error(f"Collector error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
