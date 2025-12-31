"""
Cross-Platform Scanner

Finds matching markets across Polymarket and Kalshi,
detects arbitrage opportunities, and manages unified analysis.
"""

import re
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple
from difflib import SequenceMatcher
from dataclasses import dataclass, field

from . import (
    Platform, UnifiedMarket, MarketPair, MarketStats, DynamicThresholds
)
from .polymarket_adapter import PolymarketAdapter
from .kalshi_adapter import KalshiAdapter

logger = logging.getLogger(__name__)


@dataclass
class ArbitrageOpportunity:
    """A detected arbitrage opportunity"""
    market_pair: MarketPair
    detected_at: datetime = field(default_factory=datetime.utcnow)

    # The arb
    arb_type: str = ""  # "yes_poly_no_kalshi" or "yes_kalshi_no_poly"
    buy_yes_on: str = ""
    buy_no_on: str = ""

    yes_price: float = 0.0
    no_price: float = 0.0
    total_cost: float = 0.0
    guaranteed_profit: float = 0.0
    profit_pct: float = 0.0

    # Execution considerations
    poly_liquidity: float = 0.0
    kalshi_liquidity: float = 0.0
    max_executable_size: float = 0.0  # Limited by thinner side

    # Fees
    estimated_fees: float = 0.0
    net_profit: float = 0.0
    net_profit_pct: float = 0.0

    @property
    def is_profitable_after_fees(self) -> bool:
        return self.net_profit > 0

    @property
    def urgency(self) -> str:
        if self.net_profit_pct >= 5:
            return "high"
        elif self.net_profit_pct >= 2:
            return "medium"
        return "low"


class CrossPlatformScanner:
    """
    Scans across Polymarket and Kalshi to find:
    1. Matching markets (same event on both platforms)
    2. Arbitrage opportunities (price differences)
    3. Unified market analysis
    """

    def __init__(
        self,
        polymarket: Optional[PolymarketAdapter] = None,
        kalshi: Optional[KalshiAdapter] = None,
        match_threshold: float = 0.7,  # Similarity threshold for matching
    ):
        self.polymarket = polymarket or PolymarketAdapter()
        self.kalshi = kalshi or KalshiAdapter()
        self.match_threshold = match_threshold

        # Caches
        self._poly_markets: List[UnifiedMarket] = []
        self._kalshi_markets: List[UnifiedMarket] = []
        self._pairs: List[MarketPair] = []
        self._last_refresh: Optional[datetime] = None

    def _similarity(self, a: str, b: str) -> float:
        """Calculate similarity between two strings"""
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def _normalize_for_matching(self, text: str) -> str:
        """Normalize text for matching"""
        text = text.lower()
        # Remove common variations
        text = re.sub(r'\b(will|the|a|an|be|in|on|at|to|for|by|of)\b', '', text)
        text = re.sub(r'[^\w\s]', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _find_best_match(
        self,
        market: UnifiedMarket,
        candidates: List[UnifiedMarket]
    ) -> Tuple[Optional[UnifiedMarket], float]:
        """Find best matching market from candidates"""
        normalized = self._normalize_for_matching(market.question)

        best_match = None
        best_score = 0.0

        for candidate in candidates:
            candidate_normalized = self._normalize_for_matching(candidate.question)

            # Calculate similarity
            score = self._similarity(normalized, candidate_normalized)

            # Boost if event slugs match
            if market.event_slug and candidate.event_slug:
                if self._similarity(market.event_slug, candidate.event_slug) > 0.8:
                    score += 0.2

            if score > best_score:
                best_score = score
                best_match = candidate

        return best_match, best_score

    def refresh_markets(self, limit: int = 200):
        """Fetch fresh market data from both platforms"""
        logger.info("Refreshing markets from both platforms...")

        try:
            self._poly_markets = self.polymarket.get_markets(active_only=True, limit=limit)
            logger.info(f"Fetched {len(self._poly_markets)} Polymarket markets")
        except Exception as e:
            logger.error(f"Failed to fetch Polymarket markets: {e}")
            self._poly_markets = []

        try:
            self._kalshi_markets = self.kalshi.get_markets(active_only=True, limit=limit)
            logger.info(f"Fetched {len(self._kalshi_markets)} Kalshi markets")
        except Exception as e:
            logger.error(f"Failed to fetch Kalshi markets: {e}")
            self._kalshi_markets = []

        self._last_refresh = datetime.utcnow()

    def find_matching_markets(self) -> List[MarketPair]:
        """Find markets that exist on both platforms"""
        if not self._poly_markets or not self._kalshi_markets:
            self.refresh_markets()

        pairs = []
        matched_kalshi_ids = set()

        for poly_market in self._poly_markets:
            best_match, score = self._find_best_match(poly_market, self._kalshi_markets)

            if best_match and score >= self.match_threshold:
                if best_match.platform_id not in matched_kalshi_ids:
                    pair = MarketPair(
                        event_description=poly_market.question,
                        polymarket=poly_market,
                        kalshi=best_match,
                        match_confidence=score,
                    )
                    pairs.append(pair)
                    matched_kalshi_ids.add(best_match.platform_id)

        self._pairs = pairs
        logger.info(f"Found {len(pairs)} matching market pairs")
        return pairs

    def find_arbitrage(
        self,
        min_profit_pct: float = 1.0,  # Minimum profit to consider
        include_fees: bool = True,
    ) -> List[ArbitrageOpportunity]:
        """
        Find arbitrage opportunities between platforms.

        An arb exists when you can buy Yes on one platform and No on the other
        for a total cost less than $1 (guaranteed $1 payout).
        """
        if not self._pairs:
            self.find_matching_markets()

        opportunities = []

        for pair in self._pairs:
            arb_data = pair.arb_opportunity
            if not arb_data:
                continue

            # Estimate fees (rough: ~2% on each side)
            poly_fee = 0.02 if pair.polymarket else 0
            kalshi_fee = 0.02 if pair.kalshi else 0
            total_fees = arb_data["total_cost"] * (poly_fee + kalshi_fee)

            net_profit = arb_data["guaranteed_profit"] - total_fees
            net_profit_pct = (net_profit / arb_data["total_cost"]) * 100

            if not include_fees or net_profit_pct >= min_profit_pct:
                opp = ArbitrageOpportunity(
                    market_pair=pair,
                    arb_type=arb_data["type"],
                    buy_yes_on=arb_data["buy_yes_on"],
                    buy_no_on=arb_data["buy_no_on"],
                    yes_price=arb_data.get("poly_yes_price") or arb_data.get("kalshi_yes_price", 0),
                    no_price=arb_data.get("kalshi_no_price") or arb_data.get("poly_no_price", 0),
                    total_cost=arb_data["total_cost"],
                    guaranteed_profit=arb_data["guaranteed_profit"],
                    profit_pct=arb_data["profit_pct"],
                    poly_liquidity=pair.polymarket.total_liquidity if pair.polymarket else 0,
                    kalshi_liquidity=pair.kalshi.total_liquidity if pair.kalshi else 0,
                    estimated_fees=total_fees,
                    net_profit=net_profit,
                    net_profit_pct=net_profit_pct,
                )
                opportunities.append(opp)

        # Sort by profit
        opportunities.sort(key=lambda x: x.net_profit_pct, reverse=True)
        logger.info(f"Found {len(opportunities)} arbitrage opportunities")

        return opportunities

    def get_price_differences(
        self,
        min_diff_bps: float = 100,  # Minimum difference to report
    ) -> List[Dict[str, Any]]:
        """
        Get all price differences between platforms.
        Even non-arb differences might indicate information asymmetry.
        """
        if not self._pairs:
            self.find_matching_markets()

        differences = []

        for pair in self._pairs:
            diff_bps = pair.price_diff_bps
            if diff_bps is not None and abs(diff_bps) >= min_diff_bps:
                differences.append({
                    "event": pair.event_description[:60],
                    "poly_yes": pair.polymarket.yes_price if pair.polymarket else None,
                    "kalshi_yes": pair.kalshi.yes_price if pair.kalshi else None,
                    "diff_bps": diff_bps,
                    "higher_on": "polymarket" if diff_bps > 0 else "kalshi",
                    "match_confidence": pair.match_confidence,
                })

        differences.sort(key=lambda x: abs(x["diff_bps"]), reverse=True)
        return differences

    def scan(
        self,
        refresh: bool = True,
        min_arb_profit: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Full scan: refresh markets, find pairs, detect arbs.

        Returns comprehensive report.
        """
        if refresh:
            self.refresh_markets()

        pairs = self.find_matching_markets()
        arbs = self.find_arbitrage(min_profit_pct=min_arb_profit)
        diffs = self.get_price_differences()

        return {
            "scan_time": datetime.utcnow().isoformat(),
            "polymarket_count": len(self._poly_markets),
            "kalshi_count": len(self._kalshi_markets),
            "matched_pairs": len(pairs),
            "arbitrage_opportunities": len(arbs),
            "price_differences": len(diffs),
            "arbs": arbs,
            "top_differences": diffs[:20],
            "pairs": pairs,
        }


def format_arb_opportunity(opp: ArbitrageOpportunity) -> str:
    """Format an arbitrage opportunity for display"""
    lines = [
        f"{'='*60}",
        f"ARBITRAGE OPPORTUNITY",
        f"{'='*60}",
        f"",
        f"Event: {opp.market_pair.event_description[:60]}...",
        f"Match Confidence: {opp.market_pair.match_confidence:.0%}",
        f"",
        f"Strategy:",
        f"  Buy YES on {opp.buy_yes_on.upper()} @ {opp.yes_price:.4f}",
        f"  Buy NO on {opp.buy_no_on.upper()} @ {opp.no_price:.4f}",
        f"",
        f"Economics:",
        f"  Total Cost: ${opp.total_cost:.4f}",
        f"  Guaranteed Payout: $1.00",
        f"  Gross Profit: ${opp.guaranteed_profit:.4f} ({opp.profit_pct:.2f}%)",
        f"  Est. Fees: ${opp.estimated_fees:.4f}",
        f"  Net Profit: ${opp.net_profit:.4f} ({opp.net_profit_pct:.2f}%)",
        f"",
        f"Urgency: {opp.urgency.upper()}",
        f"Profitable After Fees: {'YES' if opp.is_profitable_after_fees else 'NO'}",
    ]
    return "\n".join(lines)
