"""Example trading strategy for demonstration purposes"""

from typing import Dict, Any, Optional
from .base import BaseStrategy, Signal


class SimpleSpreadStrategy(BaseStrategy):
    """
    Example strategy that looks for wide spreads in the order book
    This is a placeholder for demonstration purposes
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("SimpleSpreadStrategy", config)
        self.min_spread = config.get("min_spread", 0.05)  # 5% minimum spread
        self.target_profit = config.get("target_profit", 0.02)  # 2% target

    def analyze(self, market_data: Dict[str, Any]) -> Optional[Signal]:
        """
        Analyze market for spread opportunities

        Args:
            market_data: Must contain 'order_book' with 'bids' and 'asks'

        Returns:
            Signal if opportunity found
        """
        order_book = market_data.get("order_book")
        if not order_book:
            return None

        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])

        if not bids or not asks:
            return None

        # Get best bid and ask
        best_bid = float(bids[0]["price"]) if bids else 0
        best_ask = float(asks[0]["price"]) if asks else 0

        if best_bid == 0 or best_ask == 0:
            return None

        # Calculate spread
        spread = (best_ask - best_bid) / best_bid

        if spread >= self.min_spread:
            # Wide spread detected - opportunity for market making
            market_id = market_data.get("market_id")

            # Place buy order slightly above best bid
            target_price = best_bid * 1.01  # 1% above best bid
            confidence = min(spread / self.min_spread, 1.0)

            return Signal(
                market_id=market_id,
                side="buy",
                price=target_price,
                size=self.config.get("order_size", 100),
                confidence=confidence,
                metadata={"spread": spread, "best_bid": best_bid, "best_ask": best_ask},
            )

        return None

    def should_enter(self, market_data: Dict[str, Any]) -> bool:
        """Check if we should enter a position"""
        signal = self.analyze(market_data)
        return signal is not None and self.validate_signal(signal)

    def should_exit(self, market_data: Dict[str, Any]) -> bool:
        """Check if we should exit current position"""
        market_id = market_data.get("market_id")
        position = self.get_position(market_id)

        if position == 0:
            return False

        # Exit if we've reached target profit or if spread has narrowed
        order_book = market_data.get("order_book", {})
        bids = order_book.get("bids", [])

        if not bids:
            return False

        current_price = float(bids[0]["price"])
        # TODO: Track entry price and calculate P&L
        # For now, use simple exit logic
        return False
