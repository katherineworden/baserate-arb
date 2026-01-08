"""Storage for markets and base rates."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.models.market import Market, BaseRate, Platform


class MarketStorage:
    """Persistent storage for markets and base rates."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

        self.markets_file = self.data_dir / "markets.json"
        self.base_rates_file = self.data_dir / "base_rates.json"

        self._markets: dict[str, Market] = {}
        self._base_rates: dict[str, BaseRate] = {}

        self._load()

    def _load(self):
        """Load data from disk."""
        if self.markets_file.exists():
            try:
                with open(self.markets_file) as f:
                    data = json.load(f)
                    for market_data in data:
                        market = Market.from_dict(market_data)
                        self._markets[market.id] = market
            except Exception as e:
                print(f"Error loading markets: {e}")

        if self.base_rates_file.exists():
            try:
                with open(self.base_rates_file) as f:
                    data = json.load(f)
                    for market_id, rate_data in data.items():
                        self._base_rates[market_id] = BaseRate.from_dict(rate_data)
            except Exception as e:
                print(f"Error loading base rates: {e}")

    def _save_markets(self):
        """Save markets to disk."""
        data = [m.to_dict() for m in self._markets.values()]
        with open(self.markets_file, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def _save_base_rates(self):
        """Save base rates to disk."""
        data = {mid: br.to_dict() for mid, br in self._base_rates.items()}
        with open(self.base_rates_file, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def save_market(self, market: Market):
        """Save or update a market."""
        self._markets[market.id] = market
        self._save_markets()

    def save_markets(self, markets: list[Market]):
        """Save or update multiple markets."""
        for market in markets:
            self._markets[market.id] = market
        self._save_markets()

    def get_market(self, market_id: str) -> Optional[Market]:
        """Get a market by ID."""
        market = self._markets.get(market_id)
        if market:
            # Attach base rate if exists
            if market_id in self._base_rates:
                market.base_rate = self._base_rates[market_id]
        return market

    def get_markets(
        self,
        platform: Optional[Platform] = None,
        category: Optional[str] = None,
        has_base_rate: Optional[bool] = None
    ) -> list[Market]:
        """Get markets with optional filters."""
        markets = list(self._markets.values())

        if platform:
            markets = [m for m in markets if m.platform == platform]

        if category:
            markets = [m for m in markets if category.lower() in m.category.lower()]

        # Attach base rates
        for market in markets:
            if market.id in self._base_rates:
                market.base_rate = self._base_rates[market.id]

        if has_base_rate is not None:
            if has_base_rate:
                markets = [m for m in markets if m.base_rate is not None]
            else:
                markets = [m for m in markets if m.base_rate is None]

        return markets

    def save_base_rate(self, market_id: str, base_rate: BaseRate):
        """Save or update a base rate."""
        self._base_rates[market_id] = base_rate
        self._save_base_rates()

        # Update market if exists
        if market_id in self._markets:
            self._markets[market_id].base_rate = base_rate
            self._save_markets()

    def get_base_rate(self, market_id: str) -> Optional[BaseRate]:
        """Get base rate for a market."""
        return self._base_rates.get(market_id)

    def delete_market(self, market_id: str):
        """Delete a market and its base rate."""
        if market_id in self._markets:
            del self._markets[market_id]
            self._save_markets()

        if market_id in self._base_rates:
            del self._base_rates[market_id]
            self._save_base_rates()

    def clear_all(self):
        """Clear all stored data."""
        self._markets = {}
        self._base_rates = {}
        self._save_markets()
        self._save_base_rates()

    @property
    def market_count(self) -> int:
        """Get total number of stored markets."""
        return len(self._markets)

    @property
    def base_rate_count(self) -> int:
        """Get number of markets with base rates."""
        return len(self._base_rates)


class WatchlistStorage:
    """Storage for market watchlists."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.watchlist_file = self.data_dir / "watchlist.json"
        self._watchlist: set[str] = set()
        self._load()

    def _load(self):
        """Load watchlist from disk."""
        if self.watchlist_file.exists():
            try:
                with open(self.watchlist_file) as f:
                    self._watchlist = set(json.load(f))
            except Exception:
                self._watchlist = set()

    def _save(self):
        """Save watchlist to disk."""
        with open(self.watchlist_file, "w") as f:
            json.dump(list(self._watchlist), f, indent=2)

    def add(self, market_id: str):
        """Add market to watchlist."""
        self._watchlist.add(market_id)
        self._save()

    def remove(self, market_id: str):
        """Remove market from watchlist."""
        self._watchlist.discard(market_id)
        self._save()

    def contains(self, market_id: str) -> bool:
        """Check if market is in watchlist."""
        return market_id in self._watchlist

    def get_all(self) -> list[str]:
        """Get all market IDs in watchlist."""
        return list(self._watchlist)

    def clear(self):
        """Clear the watchlist."""
        self._watchlist = set()
        self._save()
