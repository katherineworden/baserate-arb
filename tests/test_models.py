"""Tests for market models."""

import pytest
from datetime import datetime, timedelta

from src.models.market import (
    Market, Platform, BaseRate, BaseRateUnit,
    OrderBookLevel, MarketOrderBook, OpportunityAnalysis
)


class TestBaseRate:
    def test_absolute_rate(self):
        """Absolute rates don't change with time."""
        rate = BaseRate(
            rate=0.3,
            unit=BaseRateUnit.ABSOLUTE,
            reasoning="Test"
        )
        future = datetime.utcnow() + timedelta(days=365)
        assert rate.calculate_probability(future) == 0.3

    def test_per_year_rate(self):
        """Per-year rates compound over time."""
        rate = BaseRate(
            rate=0.1,  # 10% per year
            unit=BaseRateUnit.PER_YEAR,
            reasoning="Test"
        )

        # One year out: ~10%
        one_year = datetime.utcnow() + timedelta(days=365)
        prob_1y = rate.calculate_probability(one_year)
        assert 0.09 < prob_1y < 0.11

        # Half year: ~5%
        half_year = datetime.utcnow() + timedelta(days=182)
        prob_6m = rate.calculate_probability(half_year)
        assert 0.04 < prob_6m < 0.06

    def test_per_month_rate(self):
        """Per-month rates compound correctly."""
        rate = BaseRate(
            rate=0.05,  # 5% per month
            unit=BaseRateUnit.PER_MONTH,
            reasoning="Test"
        )

        # One month out
        one_month = datetime.utcnow() + timedelta(days=30)
        prob = rate.calculate_probability(one_month)
        assert 0.04 < prob < 0.06

    def test_per_event_rate(self):
        """Per-event rates use events_per_period."""
        rate = BaseRate(
            rate=0.02,  # 2% per event
            unit=BaseRateUnit.PER_EVENT,
            reasoning="Test",
            events_per_period=50  # 50 events per year
        )

        # Full year with 50 events
        one_year = datetime.utcnow() + timedelta(days=365)
        prob = rate.calculate_probability(one_year)
        # 1 - (1-0.02)^50 ≈ 0.636
        assert 0.6 < prob < 0.7

    def test_serialization(self):
        """Test to_dict and from_dict."""
        rate = BaseRate(
            rate=0.15,
            unit=BaseRateUnit.PER_YEAR,
            reasoning="Historical average",
            sources=["https://example.com"],
            events_per_period=10
        )

        data = rate.to_dict()
        restored = BaseRate.from_dict(data)

        assert restored.rate == rate.rate
        assert restored.unit == rate.unit
        assert restored.reasoning == rate.reasoning
        assert restored.sources == rate.sources


class TestMarket:
    def test_market_probability(self):
        """Market probability from yes price."""
        market = Market(
            id="test",
            platform=Platform.KALSHI,
            title="Test Market",
            description="Test",
            resolution_criteria="Test",
            resolution_date=datetime.utcnow() + timedelta(days=30),
            yes_price=35,
            no_price=65
        )
        assert market.market_probability() == 0.35

    def test_fair_probability_without_base_rate(self):
        """No fair prob without base rate."""
        market = Market(
            id="test",
            platform=Platform.KALSHI,
            title="Test",
            description="Test",
            resolution_criteria="Test",
            resolution_date=datetime.utcnow() + timedelta(days=30)
        )
        assert market.fair_probability() is None

    def test_fair_probability_with_base_rate(self):
        """Fair prob calculated from base rate."""
        market = Market(
            id="test",
            platform=Platform.KALSHI,
            title="Test",
            description="Test",
            resolution_criteria="Test",
            resolution_date=datetime.utcnow() + timedelta(days=365),
            yes_price=30,
            base_rate=BaseRate(
                rate=0.2,
                unit=BaseRateUnit.PER_YEAR,
                reasoning="Test"
            )
        )
        fair = market.fair_probability()
        assert fair is not None
        assert 0.15 < fair < 0.25

    def test_edge_calculation(self):
        """Edge = fair - market."""
        market = Market(
            id="test",
            platform=Platform.KALSHI,
            title="Test",
            description="Test",
            resolution_criteria="Test",
            resolution_date=datetime.utcnow() + timedelta(days=365),
            yes_price=20,
            base_rate=BaseRate(
                rate=0.3,
                unit=BaseRateUnit.ABSOLUTE,
                reasoning="Test"
            )
        )
        # Fair = 30%, Market = 20%, Edge = 10%
        edge = market.edge_yes()
        assert edge is not None
        assert abs(edge - 0.1) < 0.01

    def test_expected_value(self):
        """EV = fair * 100 / price."""
        market = Market(
            id="test",
            platform=Platform.KALSHI,
            title="Test",
            description="Test",
            resolution_criteria="Test",
            resolution_date=datetime.utcnow() + timedelta(days=365),
            yes_price=20,
            base_rate=BaseRate(
                rate=0.3,
                unit=BaseRateUnit.ABSOLUTE,
                reasoning="Test"
            )
        )
        # Fair = 30%, Price = 20 cents
        # EV = 0.3 * 100 / 20 = 1.5
        ev = market.expected_value_yes()
        assert ev is not None
        assert abs(ev - 1.5) < 0.01

    def test_kelly_fraction(self):
        """Kelly fraction calculation."""
        market = Market(
            id="test",
            platform=Platform.KALSHI,
            title="Test",
            description="Test",
            resolution_criteria="Test",
            resolution_date=datetime.utcnow() + timedelta(days=365),
            yes_price=20,
            base_rate=BaseRate(
                rate=0.3,
                unit=BaseRateUnit.ABSOLUTE,
                reasoning="Test"
            )
        )
        kelly = market.kelly_fraction_yes()
        assert kelly is not None
        assert kelly > 0  # Positive EV should give positive Kelly


class TestOrderBook:
    def test_best_ask_with_quantity(self):
        """Find best ask with minimum quantity."""
        book = MarketOrderBook(
            yes_asks=[
                OrderBookLevel(price=30, quantity=100),
                OrderBookLevel(price=31, quantity=500),
                OrderBookLevel(price=32, quantity=1000)
            ]
        )

        # Need at least 1000 contracts
        level = book.best_yes_ask(min_quantity=1000)
        assert level is not None
        assert level.price == 32  # First level with cumulative >= 1000

    def test_fill_price(self):
        """Calculate average fill price."""
        book = MarketOrderBook(
            yes_asks=[
                OrderBookLevel(price=30, quantity=100),
                OrderBookLevel(price=31, quantity=200),
                OrderBookLevel(price=32, quantity=700)
            ]
        )

        # Fill 500 contracts
        fill_price = book.fill_price_yes(500)
        assert fill_price is not None
        # 100*30 + 200*31 + 200*32 = 3000 + 6200 + 6400 = 15600 / 500 = 31.2
        assert abs(fill_price - 31.2) < 0.01


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
