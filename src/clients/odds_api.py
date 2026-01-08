"""The Odds API client for sportsbook reference data."""

from datetime import datetime
from typing import Optional
from dataclasses import dataclass

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


@dataclass
class SportsbookOdds:
    """Sportsbook odds for an event."""
    sport: str
    event_id: str
    home_team: str
    away_team: str
    commence_time: datetime

    # Odds from various books (American format converted to implied prob)
    bookmakers: dict[str, dict]  # {bookmaker: {outcome: implied_prob}}

    # Consensus/average odds
    consensus_home: Optional[float] = None
    consensus_away: Optional[float] = None
    consensus_draw: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "sport": self.sport,
            "event_id": self.event_id,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "commence_time": self.commence_time.isoformat(),
            "bookmakers": self.bookmakers,
            "consensus_home": self.consensus_home,
            "consensus_away": self.consensus_away,
            "consensus_draw": self.consensus_draw
        }


class OddsAPIClient:
    """Client for The Odds API - sportsbook odds aggregator."""

    BASE_URL = "https://api.the-odds-api.com/v4"

    # Available sports (subset of most popular)
    SPORTS = {
        "americanfootball_nfl": "NFL",
        "americanfootball_ncaaf": "NCAAF",
        "basketball_nba": "NBA",
        "basketball_ncaab": "NCAAB",
        "baseball_mlb": "MLB",
        "icehockey_nhl": "NHL",
        "soccer_epl": "EPL",
        "soccer_usa_mls": "MLS",
        "mma_mixed_martial_arts": "MMA/UFC",
        "tennis_atp_us_open": "US Open Tennis",
        "golf_pga_championship": "PGA Golf"
    }

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self._client = httpx.Client(timeout=30.0)
        self._remaining_requests: Optional[int] = None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _request(self, endpoint: str, params: Optional[dict] = None) -> dict | list:
        """Make request to Odds API."""
        if not self.api_key:
            raise ValueError("Odds API key required")

        url = f"{self.BASE_URL}{endpoint}"
        all_params = {"apiKey": self.api_key}
        if params:
            all_params.update(params)

        response = self._client.get(url, params=all_params)

        # Track remaining requests
        self._remaining_requests = int(
            response.headers.get("x-requests-remaining", 0)
        )

        response.raise_for_status()
        return response.json()

    def get_sports(self) -> list[dict]:
        """Get list of available sports."""
        return self._request("/sports")

    def get_odds(
        self,
        sport: str,
        regions: str = "us",
        markets: str = "h2h",
        odds_format: str = "american"
    ) -> list[dict]:
        """
        Get odds for a sport.

        Args:
            sport: Sport key (e.g., 'americanfootball_nfl')
            regions: Comma-separated regions (us, uk, eu, au)
            markets: Comma-separated markets (h2h, spreads, totals)
            odds_format: american or decimal
        """
        params = {
            "regions": regions,
            "markets": markets,
            "oddsFormat": odds_format
        }
        return self._request(f"/sports/{sport}/odds", params=params)

    def get_scores(self, sport: str, days_from: int = 1) -> list[dict]:
        """Get recent scores for a sport."""
        params = {"daysFrom": days_from}
        return self._request(f"/sports/{sport}/scores", params=params)

    @staticmethod
    def american_to_implied_prob(american_odds: int) -> float:
        """Convert American odds to implied probability."""
        if american_odds > 0:
            return 100 / (american_odds + 100)
        else:
            return abs(american_odds) / (abs(american_odds) + 100)

    @staticmethod
    def decimal_to_implied_prob(decimal_odds: float) -> float:
        """Convert decimal odds to implied probability."""
        return 1 / decimal_odds if decimal_odds > 0 else 0

    def parse_event_odds(self, raw: dict) -> SportsbookOdds:
        """Parse raw odds data into SportsbookOdds."""
        commence = raw.get("commence_time", "")
        if commence:
            commence_time = datetime.fromisoformat(
                commence.replace("Z", "+00:00")
            )
        else:
            commence_time = datetime.utcnow()

        bookmakers_data = {}
        all_home = []
        all_away = []
        all_draw = []

        for bookmaker in raw.get("bookmakers", []):
            book_name = bookmaker.get("title", "Unknown")
            book_odds = {}

            for market in bookmaker.get("markets", []):
                if market.get("key") == "h2h":
                    for outcome in market.get("outcomes", []):
                        name = outcome.get("name", "")
                        price = outcome.get("price", 0)

                        # Convert to implied probability
                        if isinstance(price, int) or (isinstance(price, float) and abs(price) > 10):
                            # American odds
                            prob = self.american_to_implied_prob(int(price))
                        else:
                            # Decimal odds
                            prob = self.decimal_to_implied_prob(price)

                        book_odds[name] = prob

                        # Track for consensus
                        if name == raw.get("home_team"):
                            all_home.append(prob)
                        elif name == raw.get("away_team"):
                            all_away.append(prob)
                        elif name.lower() == "draw":
                            all_draw.append(prob)

            bookmakers_data[book_name] = book_odds

        return SportsbookOdds(
            sport=raw.get("sport_key", ""),
            event_id=raw.get("id", ""),
            home_team=raw.get("home_team", ""),
            away_team=raw.get("away_team", ""),
            commence_time=commence_time,
            bookmakers=bookmakers_data,
            consensus_home=sum(all_home) / len(all_home) if all_home else None,
            consensus_away=sum(all_away) / len(all_away) if all_away else None,
            consensus_draw=sum(all_draw) / len(all_draw) if all_draw else None
        )

    def fetch_all_odds(
        self,
        sports: Optional[list[str]] = None,
        regions: str = "us"
    ) -> list[SportsbookOdds]:
        """Fetch odds for multiple sports."""
        if sports is None:
            sports = list(self.SPORTS.keys())[:3]  # Default to top 3

        all_odds = []
        for sport in sports:
            try:
                raw_odds = self.get_odds(sport, regions=regions)
                for raw in raw_odds:
                    odds = self.parse_event_odds(raw)
                    all_odds.append(odds)
            except Exception:
                continue  # Skip sports with errors

        return all_odds

    def find_matching_odds(
        self,
        market_title: str,
        sports: Optional[list[str]] = None
    ) -> Optional[SportsbookOdds]:
        """
        Try to find sportsbook odds matching a prediction market.

        This is a best-effort fuzzy match - prediction market titles
        don't always map cleanly to sports events.
        """
        title_lower = market_title.lower()

        # Determine which sports to check based on keywords
        sports_to_check = []
        if "nfl" in title_lower or "football" in title_lower:
            sports_to_check.append("americanfootball_nfl")
        if "nba" in title_lower or "basketball" in title_lower:
            sports_to_check.append("basketball_nba")
        if "mlb" in title_lower or "baseball" in title_lower:
            sports_to_check.append("baseball_mlb")
        if "nhl" in title_lower or "hockey" in title_lower:
            sports_to_check.append("icehockey_nhl")
        if "ufc" in title_lower or "mma" in title_lower:
            sports_to_check.append("mma_mixed_martial_arts")
        if "soccer" in title_lower or "premier league" in title_lower:
            sports_to_check.append("soccer_epl")

        if not sports_to_check:
            sports_to_check = sports or list(self.SPORTS.keys())[:3]

        for sport in sports_to_check:
            try:
                raw_odds = self.get_odds(sport)
                for raw in raw_odds:
                    home = raw.get("home_team", "").lower()
                    away = raw.get("away_team", "").lower()

                    # Check if team names appear in market title
                    if home in title_lower or away in title_lower:
                        return self.parse_event_odds(raw)
            except Exception:
                continue

        return None

    @property
    def remaining_requests(self) -> Optional[int]:
        """Get remaining API requests this month."""
        return self._remaining_requests

    def close(self):
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
