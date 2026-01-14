"""Polymarket API client for fetching markets and order books."""

from datetime import datetime
from typing import Optional
import json
import re

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.models.market import (
    Market, Platform, MarketOrderBook, OrderBookLevel
)


# Category mappings for better filtering
# Polymarket uses tags/groupItemTitle, we map these to standard categories
CATEGORY_MAPPINGS = {
    # Politics
    "politics": ["politics", "election", "president", "congress", "senate", "governor", "mayor", "vote", "ballot", "democratic", "republican", "trump", "biden"],
    "us_politics": ["us politics", "united states", "america", "federal", "white house", "congress", "senate", "house of representatives"],
    "elections": ["election", "presidential", "midterm", "primary", "caucus", "vote", "ballot", "electoral"],

    # Economics & Markets
    "economics": ["economics", "economy", "gdp", "inflation", "fed", "federal reserve", "interest rate", "unemployment", "recession"],
    "crypto": ["crypto", "bitcoin", "ethereum", "btc", "eth", "cryptocurrency", "blockchain", "token", "defi", "nft"],
    "finance": ["finance", "stock", "market", "s&p", "nasdaq", "dow", "trading", "investment"],

    # Sports
    "sports": ["sports", "game", "match", "championship", "playoff", "tournament", "league"],
    "nfl": ["nfl", "football", "super bowl", "touchdown", "quarterback", "chiefs", "eagles", "cowboys"],
    "nba": ["nba", "basketball", "lakers", "celtics", "warriors", "playoffs"],
    "mlb": ["mlb", "baseball", "world series", "home run"],
    "soccer": ["soccer", "football", "premier league", "champions league", "world cup", "mls"],
    "mma": ["mma", "ufc", "fighting", "knockout", "submission"],

    # Entertainment
    "entertainment": ["entertainment", "movie", "film", "tv", "show", "celebrity", "actor", "actress", "award", "oscar", "emmy", "grammy"],
    "music": ["music", "album", "song", "artist", "billboard", "grammy", "concert"],

    # Science & Tech
    "tech": ["tech", "technology", "ai", "artificial intelligence", "software", "startup", "silicon valley", "google", "apple", "microsoft", "meta", "amazon"],
    "science": ["science", "research", "study", "discovery", "space", "nasa", "climate", "health"],
    "ai": ["ai", "artificial intelligence", "machine learning", "gpt", "openai", "anthropic", "claude", "chatgpt", "llm"],

    # Weather
    "weather": ["weather", "temperature", "rain", "snow", "storm", "hurricane", "tornado", "climate"],

    # World Events
    "world": ["world", "international", "global", "foreign", "war", "conflict", "treaty"],
    "geopolitics": ["geopolitics", "china", "russia", "ukraine", "europe", "asia", "middle east", "nato"],

    # Pop Culture
    "pop_culture": ["pop culture", "viral", "trend", "meme", "social media", "twitter", "tiktok", "instagram"],
}

# Tags that indicate markets NOT suitable for base rate analysis
NON_BASE_RATE_TAGS = [
    "will", "who will", "what will", "which", "when will",
    "predict", "forecast", "betting", "odds"
]

# Tags that indicate markets suitable for base rate analysis
BASE_RATE_FRIENDLY_TAGS = [
    "frequency", "how often", "historical", "rate of", "chance of",
    "probability", "likelihood", "typically", "usually", "average"
]


class PolymarketClient:
    """Client for Polymarket CLOB API."""

    # Polymarket endpoints
    GAMMA_API = "https://gamma-api.polymarket.com"
    CLOB_API = "https://clob.polymarket.com"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        api_passphrase: Optional[str] = None
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self._client = httpx.Client(timeout=30.0)

    def _get_headers(self) -> dict:
        """Get request headers."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["POLY-API-KEY"] = self.api_key
        return headers

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _gamma_request(
        self,
        endpoint: str,
        params: Optional[dict] = None
    ) -> dict | list:
        """Make request to Gamma API (market data)."""
        url = f"{self.GAMMA_API}{endpoint}"
        response = self._client.get(url, params=params, headers=self._get_headers())
        response.raise_for_status()
        return response.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _clob_request(
        self,
        endpoint: str,
        params: Optional[dict] = None
    ) -> dict | list:
        """Make request to CLOB API (order book)."""
        url = f"{self.CLOB_API}{endpoint}"
        response = self._client.get(url, params=params, headers=self._get_headers())
        response.raise_for_status()
        return response.json()

    def get_markets(
        self,
        active: bool = True,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0
    ) -> list[dict]:
        """Get markets from Gamma API."""
        params = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": str(closed).lower()
        }
        return self._gamma_request("/markets", params=params)

    def get_market(self, condition_id: str) -> dict:
        """Get a single market by condition ID."""
        return self._gamma_request(f"/markets/{condition_id}")

    def get_events(
        self,
        active: bool = True,
        limit: int = 100,
        offset: int = 0
    ) -> list[dict]:
        """Get events (groups of related markets)."""
        params = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower()
        }
        return self._gamma_request("/events", params=params)

    def get_event(self, event_slug: str) -> dict:
        """Get event by slug."""
        events = self._gamma_request("/events", {"slug": event_slug})
        if events:
            return events[0] if isinstance(events, list) else events
        return {}

    def get_orderbook(self, token_id: str) -> dict:
        """Get order book for a token (YES or NO side of a market)."""
        return self._clob_request("/book", params={"token_id": token_id})

    def get_price(self, token_id: str) -> dict:
        """Get current price for a token."""
        return self._clob_request("/price", params={"token_id": token_id})

    def get_midpoint(self, token_id: str) -> dict:
        """Get midpoint price for a token."""
        return self._clob_request("/midpoint", params={"token_id": token_id})

    def search_markets(
        self,
        query: str,
        active: bool = True,
        limit: int = 50
    ) -> list[dict]:
        """Search markets by query string."""
        # Fetch markets and filter
        all_markets = self.get_markets(active=active, limit=500)
        query_lower = query.lower()

        matching = []
        for m in all_markets:
            question = m.get("question", "").lower()
            description = m.get("description", "").lower()
            if query_lower in question or query_lower in description:
                matching.append(m)
                if len(matching) >= limit:
                    break

        return matching

    def parse_market(self, raw: dict) -> Market:
        """Parse raw Polymarket market data into Market model."""
        # Parse end date
        end_date = raw.get("endDate") or raw.get("end_date_iso")
        if end_date:
            if isinstance(end_date, str):
                # Handle various date formats
                try:
                    resolution_date = datetime.fromisoformat(
                        end_date.replace("Z", "+00:00")
                    )
                except ValueError:
                    resolution_date = datetime.utcnow()
            else:
                resolution_date = datetime.utcnow()
        else:
            resolution_date = datetime.utcnow()

        # Get prices - Polymarket uses 0-1 scale, convert to cents
        # outcomePrices is typically a string like "[0.45, 0.55]"
        outcome_prices = raw.get("outcomePrices", "[0.5, 0.5]")
        if isinstance(outcome_prices, str):
            try:
                prices = json.loads(outcome_prices)
            except json.JSONDecodeError:
                prices = [0.5, 0.5]
        else:
            prices = outcome_prices or [0.5, 0.5]

        yes_price = float(prices[0]) * 100 if prices else 50
        no_price = float(prices[1]) * 100 if len(prices) > 1 else 100 - yes_price

        # Get token IDs for order book fetching
        tokens = raw.get("tokens", [])
        clob_token_ids = raw.get("clobTokenIds", [])

        condition_id = raw.get("conditionId", raw.get("id", ""))

        return Market(
            id=condition_id,
            platform=Platform.POLYMARKET,
            title=raw.get("question", ""),
            description=raw.get("description", ""),
            resolution_criteria=raw.get("resolutionSource", "") or raw.get("description", ""),
            resolution_date=resolution_date,
            category=raw.get("category", "") or raw.get("groupItemTitle", ""),
            yes_price=yes_price,
            no_price=no_price,
            volume=float(raw.get("volume", 0) or 0),
            liquidity=float(raw.get("liquidity", 0) or 0),
            url=f"https://polymarket.com/event/{raw.get('slug', condition_id)}",
            last_updated=datetime.utcnow()
        )

    def parse_orderbook(self, raw: dict, side: str = "yes") -> MarketOrderBook:
        """Parse raw orderbook data from CLOB."""
        orderbook = MarketOrderBook()

        bids = raw.get("bids", [])
        asks = raw.get("asks", [])

        # CLOB format: [{"price": "0.50", "size": "100"}, ...]
        if side == "yes":
            for bid in bids:
                price = float(bid.get("price", 0)) * 100
                size = int(float(bid.get("size", 0)))
                orderbook.yes_bids.append(OrderBookLevel(price=price, quantity=size))
            for ask in asks:
                price = float(ask.get("price", 0)) * 100
                size = int(float(ask.get("size", 0)))
                orderbook.yes_asks.append(OrderBookLevel(price=price, quantity=size))
        else:
            for bid in bids:
                price = float(bid.get("price", 0)) * 100
                size = int(float(bid.get("size", 0)))
                orderbook.no_bids.append(OrderBookLevel(price=price, quantity=size))
            for ask in asks:
                price = float(ask.get("price", 0)) * 100
                size = int(float(ask.get("size", 0)))
                orderbook.no_asks.append(OrderBookLevel(price=price, quantity=size))

        return orderbook

    def fetch_markets_with_prices(
        self,
        active: bool = True,
        min_liquidity: float = 0,
        limit: int = 50
    ) -> list[Market]:
        """Fetch markets with current prices."""
        raw_markets = self.get_markets(active=active, limit=limit * 2)

        markets = []
        for raw in raw_markets:
            liquidity = float(raw.get("liquidity", 0) or 0)
            if liquidity < min_liquidity:
                continue

            market = self.parse_market(raw)
            markets.append(market)

            if len(markets) >= limit:
                break

        return markets

    def fetch_market_with_orderbook(self, condition_id: str) -> Optional[Market]:
        """Fetch a single market with its order book."""
        try:
            raw = self.get_market(condition_id)
            if not raw:
                return None

            market = self.parse_market(raw)

            # Try to get order book using CLOB token IDs
            clob_ids = raw.get("clobTokenIds", [])
            if clob_ids and len(clob_ids) >= 1:
                try:
                    yes_book = self.get_orderbook(clob_ids[0])
                    market.order_book = self.parse_orderbook(yes_book, "yes")

                    if len(clob_ids) >= 2:
                        no_book = self.get_orderbook(clob_ids[1])
                        no_parsed = self.parse_orderbook(no_book, "no")
                        market.order_book.no_bids = no_parsed.no_bids
                        market.order_book.no_asks = no_parsed.no_asks
                except Exception:
                    pass  # Order book fetch failed

            return market
        except Exception:
            return None

    def classify_market_category(self, market_data: dict) -> list[str]:
        """
        Classify a market into categories based on its content.

        Args:
            market_data: Raw market data from API

        Returns:
            List of matching category names
        """
        # Combine all text for matching
        question = market_data.get("question", "").lower()
        description = market_data.get("description", "").lower()
        tags = market_data.get("tags", [])
        group_title = market_data.get("groupItemTitle", "").lower()

        all_text = f"{question} {description} {group_title}"
        if isinstance(tags, list):
            all_text += " " + " ".join(str(t).lower() for t in tags)

        matched_categories = []
        for category, keywords in CATEGORY_MAPPINGS.items():
            for keyword in keywords:
                if keyword in all_text:
                    matched_categories.append(category)
                    break

        return matched_categories

    def is_base_rate_amenable(self, market_data: dict) -> tuple[bool, str]:
        """
        Check if a market is amenable to base rate analysis.

        Returns:
            Tuple of (is_amenable, reason)
        """
        question = market_data.get("question", "").lower()
        description = market_data.get("description", "").lower()

        # Check for base-rate-friendly patterns
        for tag in BASE_RATE_FRIENDLY_TAGS:
            if tag in question or tag in description:
                return True, f"Contains base-rate-friendly term: {tag}"

        # Check for patterns that suggest historical reference class
        historical_patterns = [
            r"how many times",
            r"how often",
            r"frequency of",
            r"rate of",
            r"\d+ or more",
            r"at least \d+",
            r"more than \d+",
            r"exceed \d+",
        ]
        for pattern in historical_patterns:
            if re.search(pattern, question) or re.search(pattern, description):
                return True, f"Contains historical pattern: {pattern}"

        # Markets with clear quantitative thresholds are often base-rate-amenable
        quantitative_patterns = [
            r"\$[\d,]+",  # Dollar amounts
            r"\d+%",  # Percentages
            r"\d+ degrees",  # Temperature
            r"\d+ inches",  # Measurements
        ]
        for pattern in quantitative_patterns:
            if re.search(pattern, question) or re.search(pattern, description):
                return True, f"Contains quantitative threshold: {pattern}"

        # Check for non-base-rate patterns (e.g., "who will win")
        non_br_patterns = [
            r"who will (win|be|become)",
            r"which (team|player|candidate)",
            r"what will .+ (say|do|announce)",
        ]
        for pattern in non_br_patterns:
            if re.search(pattern, question):
                return False, f"Contains non-base-rate pattern: {pattern}"

        # Default: might be amenable, needs review
        return True, "No clear indicators, may need manual review"

    def get_markets_by_category(
        self,
        categories: list[str],
        active: bool = True,
        min_liquidity: float = 0,
        limit: int = 50,
        base_rate_only: bool = False
    ) -> list[Market]:
        """
        Fetch markets filtered by category.

        Args:
            categories: List of category names to filter by
            active: Only active markets
            min_liquidity: Minimum liquidity threshold
            limit: Maximum number of markets to return
            base_rate_only: Only return markets amenable to base rate analysis

        Returns:
            List of Market objects matching the criteria
        """
        # Normalize categories to lowercase
        categories = [c.lower() for c in categories]

        # Fetch more markets than needed for filtering
        raw_markets = self.get_markets(active=active, limit=limit * 5)

        markets = []
        for raw in raw_markets:
            # Check liquidity
            liquidity = float(raw.get("liquidity", 0) or 0)
            if liquidity < min_liquidity:
                continue

            # Check category match
            market_categories = self.classify_market_category(raw)
            if not any(cat in categories for cat in market_categories):
                # Also check if any category is a substring match
                all_cat_text = " ".join(market_categories)
                if not any(cat in all_cat_text for cat in categories):
                    continue

            # Check base rate amenability if requested
            if base_rate_only:
                is_amenable, _ = self.is_base_rate_amenable(raw)
                if not is_amenable:
                    continue

            market = self.parse_market(raw)
            # Store the detected categories
            market.tags = market_categories
            markets.append(market)

            if len(markets) >= limit:
                break

        return markets

    def get_markets_by_tags(
        self,
        tags: list[str],
        active: bool = True,
        limit: int = 50
    ) -> list[Market]:
        """
        Fetch markets by Polymarket's native tags.

        Args:
            tags: List of tags to search for
            active: Only active markets
            limit: Maximum number of markets

        Returns:
            List of matching markets
        """
        tags_lower = [t.lower() for t in tags]
        raw_markets = self.get_markets(active=active, limit=limit * 3)

        markets = []
        for raw in raw_markets:
            market_tags = raw.get("tags", [])
            if isinstance(market_tags, list):
                market_tags_lower = [str(t).lower() for t in market_tags]
                if any(tag in market_tags_lower for tag in tags_lower):
                    markets.append(self.parse_market(raw))
                    if len(markets) >= limit:
                        break

        return markets

    def get_available_categories(self) -> dict[str, int]:
        """
        Get a count of markets in each category.

        Returns:
            Dict mapping category name to market count
        """
        raw_markets = self.get_markets(active=True, limit=500)

        category_counts = {}
        for raw in raw_markets:
            categories = self.classify_market_category(raw)
            for cat in categories:
                category_counts[cat] = category_counts.get(cat, 0) + 1

        return dict(sorted(category_counts.items(), key=lambda x: x[1], reverse=True))

    def close(self):
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
