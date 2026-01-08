"""Kalshi API client for fetching markets and order books."""

import hashlib
import base64
import time
from datetime import datetime
from typing import Optional
from pathlib import Path

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.models.market import (
    Market, Platform, MarketOrderBook, OrderBookLevel
)


class KalshiClient:
    """Client for Kalshi Exchange API."""

    BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
    DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"

    def __init__(
        self,
        api_key: Optional[str] = None,
        private_key_path: Optional[str] = None,
        use_demo: bool = False
    ):
        self.api_key = api_key
        self.private_key_path = private_key_path
        self.base_url = self.DEMO_URL if use_demo else self.BASE_URL
        self._client = httpx.Client(timeout=30.0)
        self._token: Optional[str] = None
        self._token_expiry: float = 0

    def _load_private_key(self) -> Optional[bytes]:
        """Load private key for authentication."""
        if not self.private_key_path:
            return None
        path = Path(self.private_key_path)
        if path.exists():
            return path.read_bytes()
        return None

    def _sign_request(self, timestamp: int, method: str, path: str) -> str:
        """Sign request using RSA private key."""
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding, utils

            private_key_bytes = self._load_private_key()
            if not private_key_bytes:
                return ""

            private_key = serialization.load_pem_private_key(
                private_key_bytes, password=None
            )

            message = f"{timestamp}{method}{path}".encode()
            digest = hashlib.sha256(message).digest()

            signature = private_key.sign(
                digest,
                padding.PKCS1v15(),
                utils.Prehashed(hashes.SHA256())
            )
            return base64.b64encode(signature).decode()
        except ImportError:
            return ""
        except Exception:
            return ""

    def _get_headers(self, method: str = "GET", path: str = "") -> dict:
        """Get request headers with authentication."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        if self.api_key and self.private_key_path:
            timestamp = int(time.time() * 1000)
            signature = self._sign_request(timestamp, method, path)
            if signature:
                headers["KALSHI-ACCESS-KEY"] = self.api_key
                headers["KALSHI-ACCESS-SIGNATURE"] = signature
                headers["KALSHI-ACCESS-TIMESTAMP"] = str(timestamp)

        return headers

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None
    ) -> dict:
        """Make authenticated request to Kalshi API."""
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers(method, endpoint)

        response = self._client.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_data
        )
        response.raise_for_status()
        return response.json()

    def get_events(
        self,
        status: str = "open",
        series_ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None
    ) -> dict:
        """Get events (event groups containing markets)."""
        params = {
            "status": status,
            "limit": limit
        }
        if series_ticker:
            params["series_ticker"] = series_ticker
        if cursor:
            params["cursor"] = cursor

        return self._request("GET", "/events", params=params)

    def get_markets(
        self,
        event_ticker: Optional[str] = None,
        series_ticker: Optional[str] = None,
        status: str = "open",
        limit: int = 100,
        cursor: Optional[str] = None
    ) -> dict:
        """Get markets, optionally filtered by event or series."""
        params = {
            "status": status,
            "limit": limit
        }
        if event_ticker:
            params["event_ticker"] = event_ticker
        if series_ticker:
            params["series_ticker"] = series_ticker
        if cursor:
            params["cursor"] = cursor

        return self._request("GET", "/markets", params=params)

    def get_market(self, ticker: str) -> dict:
        """Get a single market by ticker."""
        return self._request("GET", f"/markets/{ticker}")

    def get_orderbook(self, ticker: str, depth: int = 10) -> dict:
        """Get order book for a market."""
        params = {"depth": depth}
        return self._request("GET", f"/markets/{ticker}/orderbook", params=params)

    def get_series(self, series_ticker: str) -> dict:
        """Get series information."""
        return self._request("GET", f"/series/{series_ticker}")

    def search_markets(
        self,
        query: str,
        status: str = "open",
        limit: int = 50
    ) -> list[dict]:
        """Search markets by query string."""
        # Kalshi doesn't have a direct search endpoint,
        # so we fetch and filter client-side
        all_markets = []
        cursor = None
        query_lower = query.lower()

        while len(all_markets) < limit:
            result = self.get_markets(status=status, limit=100, cursor=cursor)
            markets = result.get("markets", [])

            for m in markets:
                title = m.get("title", "").lower()
                subtitle = m.get("subtitle", "").lower()
                if query_lower in title or query_lower in subtitle:
                    all_markets.append(m)
                    if len(all_markets) >= limit:
                        break

            cursor = result.get("cursor")
            if not cursor or not markets:
                break

        return all_markets

    def parse_market(self, raw: dict) -> Market:
        """Parse raw Kalshi market data into Market model."""
        # Parse resolution date
        close_time = raw.get("close_time") or raw.get("expiration_time")
        if close_time:
            if isinstance(close_time, str):
                resolution_date = datetime.fromisoformat(
                    close_time.replace("Z", "+00:00")
                )
            else:
                resolution_date = datetime.fromtimestamp(close_time / 1000)
        else:
            resolution_date = datetime.utcnow()

        # Get prices (Kalshi uses cents)
        yes_price = raw.get("yes_ask", raw.get("last_price", 50))
        no_price = raw.get("no_ask", 100 - yes_price)

        # Build resolution criteria from rules
        rules = raw.get("rules_primary", "") or raw.get("rules", "")
        settlement = raw.get("settlement_timer_seconds", 0)
        criteria = rules
        if settlement:
            criteria += f"\n\nSettlement: {settlement // 3600} hours after close"

        ticker = raw.get("ticker", "")

        return Market(
            id=ticker,
            platform=Platform.KALSHI,
            title=raw.get("title", ""),
            description=raw.get("subtitle", "") or raw.get("title", ""),
            resolution_criteria=criteria,
            resolution_date=resolution_date,
            category=raw.get("category", "") or raw.get("series_ticker", ""),
            yes_price=yes_price,
            no_price=no_price,
            volume=raw.get("volume", 0),
            liquidity=raw.get("liquidity", 0),
            url=f"https://kalshi.com/markets/{ticker}",
            last_updated=datetime.utcnow()
        )

    def parse_orderbook(self, raw: dict) -> MarketOrderBook:
        """Parse raw orderbook data."""
        orderbook = MarketOrderBook()

        # Kalshi orderbook format: {"yes": [[price, qty], ...], "no": [[price, qty], ...]}
        yes_data = raw.get("orderbook", {}).get("yes", [])
        no_data = raw.get("orderbook", {}).get("no", [])

        for price, qty in yes_data:
            orderbook.yes_asks.append(OrderBookLevel(price=price, quantity=qty))

        for price, qty in no_data:
            orderbook.no_asks.append(OrderBookLevel(price=price, quantity=qty))

        return orderbook

    def fetch_markets_with_books(
        self,
        event_ticker: Optional[str] = None,
        series_ticker: Optional[str] = None,
        min_volume: float = 0,
        limit: int = 50
    ) -> list[Market]:
        """Fetch markets and their order books."""
        result = self.get_markets(
            event_ticker=event_ticker,
            series_ticker=series_ticker,
            limit=limit
        )

        markets = []
        for raw in result.get("markets", []):
            if raw.get("volume", 0) < min_volume:
                continue

            market = self.parse_market(raw)

            # Fetch orderbook
            try:
                book_raw = self.get_orderbook(market.id)
                market.order_book = self.parse_orderbook(book_raw)
            except Exception:
                pass  # Order book fetch failed, continue without it

            markets.append(market)

        return markets

    def close(self):
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
