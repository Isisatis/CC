"""Async Polymarket API client for parallel data fetching."""

import os
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime
import aiohttp
from dotenv import load_dotenv

load_dotenv()


class AsyncPolymarketClient:
    """
    Async client for interacting with Polymarket APIs.

    Use this client when you need to fetch data for many tokens/markets
    in parallel while respecting rate limits.
    """

    CLOB_BASE_URL = "https://clob.polymarket.com"
    GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

    def __init__(
        self,
        clob_url: str = None,
        gamma_url: str = None,
        max_concurrent: int = 5,  # Max concurrent requests
        rate_limit: float = 0.1,  # Min seconds between request batches
    ):
        """
        Initialize async Polymarket client.

        Args:
            clob_url: CLOB API base URL
            gamma_url: Gamma API base URL
            max_concurrent: Maximum concurrent requests
            rate_limit: Minimum seconds between request batches
        """
        self.clob_url = clob_url or self.CLOB_BASE_URL
        self.gamma_url = gamma_url or self.GAMMA_BASE_URL
        self.max_concurrent = max_concurrent
        self.rate_limit = rate_limit
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                }
            )
        return self._session

    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def _request(
        self,
        method: str,
        url: str,
        params: Dict = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """Make an async HTTP request with rate limiting."""
        async with self._semaphore:
            session = await self._get_session()
            async with session.request(
                method=method,
                url=url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                response.raise_for_status()
                return await response.json()

    # ==================== Parallel Fetch Methods ====================

    async def get_order_books_parallel(
        self,
        token_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Fetch order books for multiple tokens in parallel.

        Args:
            token_ids: List of token identifiers

        Returns:
            List of order book results with token_id included
        """
        async def fetch_one(token_id: str) -> Dict[str, Any]:
            try:
                url = f"{self.clob_url}/book"
                params = {"token_id": token_id}
                result = await self._request("GET", url, params=params)
                return {"token_id": token_id, "success": True, **result}
            except Exception as e:
                return {"token_id": token_id, "success": False, "error": str(e)}

        # Create tasks for all token_ids
        tasks = [fetch_one(tid) for tid in token_ids]

        # Execute in batches respecting rate limits
        results = []
        batch_size = self.max_concurrent

        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            batch_results = await asyncio.gather(*batch)
            results.extend(batch_results)

            # Rate limiting between batches
            if i + batch_size < len(tasks):
                await asyncio.sleep(self.rate_limit)

        return results

    async def get_prices_parallel(
        self,
        token_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Fetch prices for multiple tokens in parallel.

        Args:
            token_ids: List of token identifiers

        Returns:
            List of price results
        """
        async def fetch_one(token_id: str) -> Dict[str, Any]:
            try:
                url = f"{self.clob_url}/price"
                params = {"token_id": token_id}
                result = await self._request("GET", url, params=params)
                return {"token_id": token_id, "success": True, **result}
            except Exception as e:
                return {"token_id": token_id, "success": False, "error": str(e)}

        tasks = [fetch_one(tid) for tid in token_ids]
        results = []
        batch_size = self.max_concurrent

        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            batch_results = await asyncio.gather(*batch)
            results.extend(batch_results)

            if i + batch_size < len(tasks):
                await asyncio.sleep(self.rate_limit)

        return results

    async def get_spreads_parallel(
        self,
        token_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Fetch spreads for multiple tokens in parallel.

        Args:
            token_ids: List of token identifiers

        Returns:
            List of spread results
        """
        async def fetch_one(token_id: str) -> Dict[str, Any]:
            try:
                url = f"{self.clob_url}/spread"
                params = {"token_id": token_id}
                result = await self._request("GET", url, params=params)
                return {"token_id": token_id, "success": True, **result}
            except Exception as e:
                return {"token_id": token_id, "success": False, "error": str(e)}

        tasks = [fetch_one(tid) for tid in token_ids]
        results = []
        batch_size = self.max_concurrent

        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            batch_results = await asyncio.gather(*batch)
            results.extend(batch_results)

            if i + batch_size < len(tasks):
                await asyncio.sleep(self.rate_limit)

        return results

    async def get_full_market_data_parallel(
        self,
        token_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Fetch complete market data (order book + spread + price) for tokens.

        Args:
            token_ids: List of token identifiers

        Returns:
            List of complete market data for each token
        """
        async def fetch_full(token_id: str) -> Dict[str, Any]:
            result = {"token_id": token_id, "timestamp": datetime.utcnow().isoformat()}

            try:
                # Fetch order book
                url = f"{self.clob_url}/book"
                book = await self._request("GET", url, params={"token_id": token_id})
                result["order_book"] = book
            except Exception as e:
                result["order_book_error"] = str(e)

            try:
                # Fetch spread
                url = f"{self.clob_url}/spread"
                spread = await self._request("GET", url, params={"token_id": token_id})
                result["spread"] = spread
            except Exception as e:
                result["spread_error"] = str(e)

            try:
                # Fetch midpoint
                url = f"{self.clob_url}/midpoint"
                midpoint = await self._request("GET", url, params={"token_id": token_id})
                result["midpoint"] = midpoint
            except Exception as e:
                result["midpoint_error"] = str(e)

            return result

        tasks = [fetch_full(tid) for tid in token_ids]
        results = []
        batch_size = self.max_concurrent

        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            batch_results = await asyncio.gather(*batch)
            results.extend(batch_results)

            if i + batch_size < len(tasks):
                await asyncio.sleep(self.rate_limit)

        return results


# Convenience function for running async code
def run_async(coro):
    """Run an async coroutine from synchronous code."""
    return asyncio.get_event_loop().run_until_complete(coro)


# Example usage
async def example_parallel_fetch():
    """Example of parallel data fetching."""
    async with AsyncPolymarketClient(max_concurrent=5) as client:
        # Example token IDs (replace with real ones)
        token_ids = ["token1", "token2", "token3"]

        # Fetch order books in parallel
        results = await client.get_order_books_parallel(token_ids)

        for result in results:
            if result["success"]:
                print(f"Token {result['token_id']}: Got order book")
            else:
                print(f"Token {result['token_id']}: Error - {result['error']}")
