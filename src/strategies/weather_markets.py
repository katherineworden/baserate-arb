"""
Weather market arbitrage using Weather.gov/NWS API.

Weather markets (temperature, precipitation, etc.) are particularly amenable
to quantitative analysis because:
1. NWS provides free, high-quality probabilistic forecasts
2. Historical verification data is available
3. Forecast accuracy is well-studied

This module fetches NWS forecasts and compares to market prices.
"""

import httpx
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
import re


@dataclass
class TemperatureForecast:
    """Temperature forecast from NWS."""
    location: str
    valid_time: datetime
    high_temp: Optional[float] = None
    low_temp: Optional[float] = None
    # Probabilistic bounds (if available)
    high_temp_10th: Optional[float] = None  # 10% chance below this
    high_temp_90th: Optional[float] = None  # 10% chance above this


@dataclass
class PrecipitationForecast:
    """Precipitation forecast from NWS."""
    location: str
    valid_time: datetime
    probability: float  # 0-100 probability of precipitation
    amount_min: Optional[float] = None  # inches
    amount_max: Optional[float] = None
    precip_type: str = "rain"  # rain, snow, mixed


@dataclass
class WeatherMarket:
    """A weather-related prediction market."""
    market_id: str
    market_type: str  # "high_temp_over", "precip", "snow"
    location: str
    threshold: Optional[float] = None  # e.g., 80 for "High temp over 80F"
    resolution_date: datetime = None
    yes_price: float = 50


class NWSClient:
    """
    Client for National Weather Service API.

    NWS API is free and doesn't require authentication.
    Docs: https://www.weather.gov/documentation/services-web-api
    """

    BASE_URL = "https://api.weather.gov"

    def __init__(self):
        self._client = httpx.Client(
            timeout=30.0,
            headers={"User-Agent": "baserate-arb/1.0 (weather market analysis)"}
        )
        self._point_cache: dict[str, dict] = {}

    def _get_point(self, lat: float, lon: float) -> dict:
        """Get grid point info for coordinates."""
        cache_key = f"{lat},{lon}"
        if cache_key in self._point_cache:
            return self._point_cache[cache_key]

        resp = self._client.get(f"{self.BASE_URL}/points/{lat},{lon}")
        resp.raise_for_status()
        data = resp.json()

        self._point_cache[cache_key] = data["properties"]
        return data["properties"]

    def get_forecast(self, lat: float, lon: float) -> dict:
        """
        Get 7-day forecast for a location.

        Returns periods with temperature, precipitation probability, etc.
        """
        point = self._get_point(lat, lon)
        forecast_url = point["forecast"]

        resp = self._client.get(forecast_url)
        resp.raise_for_status()
        return resp.json()

    def get_hourly_forecast(self, lat: float, lon: float) -> dict:
        """Get hourly forecast (more detailed)."""
        point = self._get_point(lat, lon)
        hourly_url = point["forecastHourly"]

        resp = self._client.get(hourly_url)
        resp.raise_for_status()
        return resp.json()

    def get_gridpoint_forecast(self, lat: float, lon: float) -> dict:
        """
        Get raw gridpoint data with probabilistic info.

        This includes probability distributions, not just point estimates.
        """
        point = self._get_point(lat, lon)
        grid_url = point["forecastGridData"]

        resp = self._client.get(grid_url)
        resp.raise_for_status()
        return resp.json()

    def parse_temperature_forecast(
        self,
        lat: float,
        lon: float,
        target_date: datetime
    ) -> Optional[TemperatureForecast]:
        """
        Get temperature forecast for a specific date.

        Returns high/low temps with uncertainty bounds if available.
        """
        try:
            forecast = self.get_forecast(lat, lon)
            periods = forecast["properties"]["periods"]

            # Find the relevant period
            for period in periods:
                period_start = datetime.fromisoformat(
                    period["startTime"].replace("Z", "+00:00")
                )

                if period_start.date() == target_date.date():
                    # Daytime period for high, nighttime for low
                    if period["isDaytime"]:
                        return TemperatureForecast(
                            location=f"{lat},{lon}",
                            valid_time=target_date,
                            high_temp=period["temperature"]
                        )
                    else:
                        return TemperatureForecast(
                            location=f"{lat},{lon}",
                            valid_time=target_date,
                            low_temp=period["temperature"]
                        )

            return None
        except Exception as e:
            print(f"Error fetching temperature forecast: {e}")
            return None

    def parse_precip_forecast(
        self,
        lat: float,
        lon: float,
        target_date: datetime
    ) -> Optional[PrecipitationForecast]:
        """
        Get precipitation probability for a specific date.
        """
        try:
            forecast = self.get_forecast(lat, lon)
            periods = forecast["properties"]["periods"]

            max_prob = 0
            for period in periods:
                period_start = datetime.fromisoformat(
                    period["startTime"].replace("Z", "+00:00")
                )

                if period_start.date() == target_date.date():
                    # Extract probability from detailed forecast
                    prob = period.get("probabilityOfPrecipitation", {}).get("value", 0)
                    if prob and prob > max_prob:
                        max_prob = prob

            return PrecipitationForecast(
                location=f"{lat},{lon}",
                valid_time=target_date,
                probability=max_prob or 0
            )
        except Exception as e:
            print(f"Error fetching precip forecast: {e}")
            return None

    def close(self):
        self._client.close()


# Major city coordinates for common markets
CITY_COORDS = {
    "new_york": (40.7128, -74.0060),
    "nyc": (40.7128, -74.0060),
    "los_angeles": (34.0522, -118.2437),
    "la": (34.0522, -118.2437),
    "chicago": (41.8781, -87.6298),
    "houston": (29.7604, -95.3698),
    "phoenix": (33.4484, -112.0740),
    "miami": (25.7617, -80.1918),
    "denver": (39.7392, -104.9903),
    "seattle": (47.6062, -122.3321),
    "boston": (42.3601, -71.0589),
    "atlanta": (33.7490, -84.3880),
    "san_francisco": (37.7749, -122.4194),
    "sf": (37.7749, -122.4194),
    "washington_dc": (38.9072, -77.0369),
    "dc": (38.9072, -77.0369),
}


class WeatherMarketAnalyzer:
    """
    Analyze weather markets using NWS forecasts.

    Compares NWS probabilistic forecasts to market prices.
    """

    def __init__(self):
        self.nws = NWSClient()

    def _get_coords(self, location: str) -> Optional[tuple[float, float]]:
        """Get coordinates for a location string."""
        loc_lower = location.lower().replace(" ", "_").replace(",", "")

        # Check known cities
        for city, coords in CITY_COORDS.items():
            if city in loc_lower:
                return coords

        # Try to parse lat,lon
        try:
            parts = location.split(",")
            if len(parts) == 2:
                return float(parts[0]), float(parts[1])
        except ValueError:
            pass

        return None

    def analyze_temp_over_market(
        self,
        market: WeatherMarket,
        threshold: float
    ) -> Optional[dict]:
        """
        Analyze a "High temp over X" market.

        Uses NWS forecast and historical accuracy to estimate probability.
        """
        coords = self._get_coords(market.location)
        if not coords:
            return {"error": f"Unknown location: {market.location}"}

        forecast = self.nws.parse_temperature_forecast(
            coords[0], coords[1], market.resolution_date
        )

        if not forecast or not forecast.high_temp:
            return {"error": "Could not get temperature forecast"}

        predicted_high = forecast.high_temp

        # Simple model: assume forecast error is ~3°F std dev
        # P(actual > threshold) depends on how far threshold is from forecast
        forecast_error_std = 3.0
        z_score = (threshold - predicted_high) / forecast_error_std

        # Use normal CDF approximation
        # P(X > threshold) = 1 - Phi(z_score)
        import math
        p_over = 1 - 0.5 * (1 + math.erf(z_score / math.sqrt(2)))

        fair_price = p_over * 100

        return {
            "market_id": market.market_id,
            "location": market.location,
            "threshold": threshold,
            "forecast_high": predicted_high,
            "p_over_threshold": p_over,
            "fair_price": fair_price,
            "market_price": market.yes_price,
            "edge": fair_price - market.yes_price,
            "forecast_source": "NWS",
            "signal": "BUY_YES" if fair_price - market.yes_price > 5 else
                     ("BUY_NO" if market.yes_price - fair_price > 5 else "HOLD")
        }

    def analyze_precip_market(
        self,
        market: WeatherMarket
    ) -> Optional[dict]:
        """
        Analyze a precipitation probability market.

        NWS directly provides probability of precipitation.
        """
        coords = self._get_coords(market.location)
        if not coords:
            return {"error": f"Unknown location: {market.location}"}

        forecast = self.nws.parse_precip_forecast(
            coords[0], coords[1], market.resolution_date
        )

        if not forecast:
            return {"error": "Could not get precipitation forecast"}

        # NWS PoP is already a probability
        fair_price = forecast.probability

        return {
            "market_id": market.market_id,
            "location": market.location,
            "nws_probability": forecast.probability,
            "fair_price": fair_price,
            "market_price": market.yes_price,
            "edge": fair_price - market.yes_price,
            "forecast_source": "NWS",
            "signal": "BUY_YES" if fair_price - market.yes_price > 5 else
                     ("BUY_NO" if market.yes_price - fair_price > 5 else "HOLD")
        }

    def analyze_market(self, market: WeatherMarket) -> dict:
        """Analyze any weather market based on type."""
        if market.market_type == "high_temp_over":
            return self.analyze_temp_over_market(market, market.threshold)
        elif market.market_type == "precip":
            return self.analyze_precip_market(market)
        else:
            return {"error": f"Unknown market type: {market.market_type}"}

    def close(self):
        self.nws.close()


def parse_weather_market_title(title: str) -> Optional[WeatherMarket]:
    """
    Parse a market title to extract weather market parameters.

    Examples:
    - "Will NYC high temperature exceed 80°F on Jan 15?"
    - "Rain in Chicago on January 20, 2025?"
    """
    title_lower = title.lower()

    # Temperature markets
    temp_pattern = r"(high|low)\s+temp.*?(\d+)\s*[°]?f"
    temp_match = re.search(temp_pattern, title_lower)

    if temp_match:
        temp_type = temp_match.group(1)
        threshold = float(temp_match.group(2))

        # Find location
        location = None
        for city in CITY_COORDS:
            if city.replace("_", " ") in title_lower:
                location = city
                break

        if location:
            return WeatherMarket(
                market_id="",
                market_type=f"{temp_type}_temp_over",
                location=location,
                threshold=threshold
            )

    # Precipitation markets
    if any(w in title_lower for w in ["rain", "precipitation", "snow"]):
        location = None
        for city in CITY_COORDS:
            if city.replace("_", " ") in title_lower:
                location = city
                break

        if location:
            return WeatherMarket(
                market_id="",
                market_type="precip",
                location=location
            )

    return None
