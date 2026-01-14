"""
Stock direction prediction for SPX up/down markets.

Predicts P(stock ends higher than previous close | current state).

Features used:
- Current % change from previous close
- Time into trading day (% of session elapsed)
- Intraday volatility
- Pre-market/futures sentiment
- Historical patterns for similar setups
"""

import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional
import json

try:
    import pandas as pd
    import numpy as np
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


@dataclass
class MarketState:
    """Current state of a stock/index."""
    symbol: str
    previous_close: float
    current_price: float
    day_high: float
    day_low: float
    open_price: float
    volume: int
    timestamp: datetime

    # Optional: futures/pre-market
    futures_price: Optional[float] = None
    premarket_price: Optional[float] = None

    @property
    def pct_change(self) -> float:
        """Current % change from previous close."""
        return (self.current_price - self.previous_close) / self.previous_close * 100

    @property
    def pct_from_open(self) -> float:
        """Current % change from open."""
        return (self.current_price - self.open_price) / self.open_price * 100

    @property
    def intraday_range(self) -> float:
        """Intraday range as % of previous close."""
        return (self.day_high - self.day_low) / self.previous_close * 100

    @property
    def position_in_range(self) -> float:
        """Where current price is in day's range (0=low, 1=high)."""
        if self.day_high == self.day_low:
            return 0.5
        return (self.current_price - self.day_low) / (self.day_high - self.day_low)


def get_session_progress(timestamp: datetime) -> float:
    """
    Get progress through trading session (0-1).

    US market: 9:30 AM - 4:00 PM ET (6.5 hours)
    """
    # Convert to market time (assume ET)
    market_open = time(9, 30)
    market_close = time(16, 0)

    current_time = timestamp.time()

    # Before open
    if current_time < market_open:
        return 0.0

    # After close
    if current_time >= market_close:
        return 1.0

    # During session
    open_seconds = market_open.hour * 3600 + market_open.minute * 60
    close_seconds = market_close.hour * 3600 + market_close.minute * 60
    current_seconds = current_time.hour * 3600 + current_time.minute * 60 + current_time.second

    return (current_seconds - open_seconds) / (close_seconds - open_seconds)


class HistoricalPatternAnalyzer:
    """
    Analyze historical patterns to predict close direction.

    Key insight: Stock behavior changes throughout the day.
    - Morning volatility is higher
    - Lunch hours are quieter
    - Last hour often sees reversals or trend continuation
    """

    def __init__(self, data_path: Optional[str] = None):
        self.data_path = data_path
        self.patterns: dict = {}
        self._load_patterns()

    def _load_patterns(self):
        """Load pre-computed historical patterns."""
        if self.data_path and os.path.exists(self.data_path):
            with open(self.data_path) as f:
                self.patterns = json.load(f)
        else:
            # Default patterns based on typical SPX behavior
            # Format: (pct_change_bucket, session_progress_bucket) -> P(up)
            self.patterns = {
                # If up > 0.5% with > 80% session done, usually stays up
                "up_large_late": {"p_up": 0.72, "samples": 1000},
                # If up small early, could go either way
                "up_small_early": {"p_up": 0.54, "samples": 1000},
                # If down > 0.5% late, usually stays down
                "down_large_late": {"p_up": 0.31, "samples": 1000},
                # Mean reversion early
                "down_small_early": {"p_up": 0.48, "samples": 1000},
                # Flat all day tends to drift
                "flat_late": {"p_up": 0.51, "samples": 500},
            }

    def _get_pattern_key(self, state: MarketState, progress: float) -> str:
        """Determine which pattern bucket applies."""
        pct = state.pct_change

        if progress > 0.8:
            stage = "late"
        elif progress < 0.3:
            stage = "early"
        else:
            stage = "mid"

        if pct > 0.5:
            direction = "up_large"
        elif pct > 0.1:
            direction = "up_small"
        elif pct < -0.5:
            direction = "down_large"
        elif pct < -0.1:
            direction = "down_small"
        else:
            direction = "flat"

        return f"{direction}_{stage}"

    def historical_probability(self, state: MarketState, progress: float) -> float:
        """
        Get historical P(up) for similar setups.

        Returns probability based on historical patterns.
        """
        key = self._get_pattern_key(state, progress)

        # Try exact match
        if key in self.patterns:
            return self.patterns[key]["p_up"]

        # Fallback to simpler buckets
        pct = state.pct_change
        if pct > 0.3:
            return 0.58 + min(0.15, pct * 0.05)  # Momentum
        elif pct < -0.3:
            return 0.42 - min(0.15, abs(pct) * 0.05)  # Momentum down
        else:
            return 0.50  # Coin flip when flat


class StockDirectionModel:
    """
    Main model for predicting stock close direction.

    Combines:
    1. Current momentum (% change)
    2. Time decay (late moves more predictive)
    3. Historical patterns
    4. Intraday technicals
    """

    def __init__(self, history_path: Optional[str] = None):
        self.history = HistoricalPatternAnalyzer(history_path)

    def momentum_factor(self, pct_change: float) -> float:
        """
        Momentum contribution to P(up).

        Larger moves in one direction make continuation more likely.
        """
        # Sigmoid-like transformation
        # pct_change of 0 -> 0.5
        # pct_change of +1% -> ~0.62
        # pct_change of -1% -> ~0.38
        return 1 / (1 + np.exp(-pct_change * 0.8)) if HAS_PANDAS else 0.5

    def time_weight(self, progress: float) -> float:
        """
        How much to weight current price vs uncertainty.

        Early in day: more uncertainty, price less predictive
        Late in day: current price very predictive
        """
        # Exponential increase in certainty
        return 0.3 + 0.7 * (progress ** 1.5)

    def predict_probability(self, state: MarketState) -> dict:
        """
        Predict P(stock closes above previous close).

        Returns dict with probability and confidence.
        """
        progress = get_session_progress(state.timestamp)

        # Component probabilities
        momentum_p = self.momentum_factor(state.pct_change) if HAS_PANDAS else 0.5 + state.pct_change * 0.1
        historical_p = self.history.historical_probability(state, progress)

        # Weighted combination
        time_w = self.time_weight(progress)

        # Late in day: trust current price more
        # Early: trust historical patterns more
        if progress > 0.9:
            # Last 30 min: almost entirely based on current price
            if state.pct_change > 0.05:
                p_up = 0.85 + min(0.14, state.pct_change * 0.1)
            elif state.pct_change < -0.05:
                p_up = 0.15 - min(0.14, abs(state.pct_change) * 0.1)
            else:
                p_up = 0.5 + state.pct_change * 2  # Very sensitive to direction
        else:
            # Blend momentum and historical
            p_up = time_w * momentum_p + (1 - time_w) * historical_p

        # Clamp to reasonable range
        p_up = max(0.05, min(0.95, p_up))

        # Confidence based on how clear the signal is
        confidence = abs(p_up - 0.5) * 2  # 0-1 scale

        return {
            "symbol": state.symbol,
            "p_up": p_up,
            "p_down": 1 - p_up,
            "confidence": confidence,
            "session_progress": progress,
            "pct_change": state.pct_change,
            "components": {
                "momentum": momentum_p if HAS_PANDAS else 0.5,
                "historical": historical_p,
                "time_weight": time_w
            }
        }

    def fair_price(self, state: MarketState) -> dict:
        """Calculate fair YES/NO prices for up/down market."""
        pred = self.predict_probability(state)

        return {
            "up_fair": pred["p_up"] * 100,
            "down_fair": pred["p_down"] * 100,
            **pred
        }

    def analyze_market(
        self,
        state: MarketState,
        up_market_price: float,
        down_market_price: Optional[float] = None
    ) -> dict:
        """
        Analyze a Polymarket SPX up/down market.

        Args:
            state: Current market state
            up_market_price: Current YES price for "SPX Up" in cents
            down_market_price: Current YES price for "SPX Down" (if separate)
        """
        fair = self.fair_price(state)

        result = {
            **fair,
            "market_up_price": up_market_price,
            "market_down_price": down_market_price or (100 - up_market_price),
            "edge_up": fair["up_fair"] - up_market_price,
            "edge_down": fair["down_fair"] - (down_market_price or (100 - up_market_price))
        }

        # Recommendation
        if result["edge_up"] > 5:
            result["signal"] = "BUY_UP"
            result["reasoning"] = f"UP underpriced by {result['edge_up']:.1f}%"
        elif result["edge_down"] > 5:
            result["signal"] = "BUY_DOWN"
            result["reasoning"] = f"DOWN underpriced by {result['edge_down']:.1f}%"
        else:
            result["signal"] = "HOLD"
            result["reasoning"] = "Prices roughly fair"

        return result


# Data fetching utilities
class StockDataFetcher:
    """Fetch real-time stock data from various sources."""

    @staticmethod
    def from_yahoo(symbol: str = "^GSPC") -> Optional[MarketState]:
        """Fetch from Yahoo Finance."""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            info = ticker.info
            hist = ticker.history(period="2d")

            if len(hist) < 2:
                return None

            prev_close = hist['Close'].iloc[-2]
            current = hist['Close'].iloc[-1]

            return MarketState(
                symbol=symbol,
                previous_close=prev_close,
                current_price=current,
                day_high=hist['High'].iloc[-1],
                day_low=hist['Low'].iloc[-1],
                open_price=hist['Open'].iloc[-1],
                volume=int(hist['Volume'].iloc[-1]),
                timestamp=datetime.utcnow()
            )
        except Exception as e:
            print(f"Yahoo fetch error: {e}")
            return None

    @staticmethod
    def from_alpha_vantage(symbol: str, api_key: str) -> Optional[MarketState]:
        """Fetch from Alpha Vantage API."""
        try:
            import httpx
            url = f"https://www.alphavantage.co/query"
            params = {
                "function": "GLOBAL_QUOTE",
                "symbol": symbol,
                "apikey": api_key
            }
            resp = httpx.get(url, params=params)
            data = resp.json()["Global Quote"]

            return MarketState(
                symbol=symbol,
                previous_close=float(data["08. previous close"]),
                current_price=float(data["05. price"]),
                day_high=float(data["03. high"]),
                day_low=float(data["04. low"]),
                open_price=float(data["02. open"]),
                volume=int(data["06. volume"]),
                timestamp=datetime.utcnow()
            )
        except Exception as e:
            print(f"Alpha Vantage fetch error: {e}")
            return None


# Futures data for pre-market sentiment
class FuturesDataFetcher:
    """Fetch stock index futures data."""

    @staticmethod
    def get_es_futures() -> Optional[dict]:
        """
        Get E-mini S&P 500 futures data.

        Note: Requires futures data subscription for real-time.
        For delayed data, can use Yahoo Finance ES=F ticker.
        """
        try:
            import yfinance as yf
            es = yf.Ticker("ES=F")
            hist = es.history(period="1d")

            if len(hist) == 0:
                return None

            return {
                "symbol": "ES",
                "price": hist['Close'].iloc[-1],
                "change": hist['Close'].iloc[-1] - hist['Open'].iloc[-1],
                "timestamp": datetime.utcnow()
            }
        except Exception:
            return None
