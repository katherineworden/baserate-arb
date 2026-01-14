"""
Smart market filtering to find base-rate-amenable markets.

Problem: Many prediction markets are one-off events like
"Will China invade Taiwan before GTA VI?" - these are hard to
assign base rates to because there's no reference class.

This module identifies markets that ARE amenable to base rate analysis:
1. Recurring events (weather, sports, economic indicators)
2. Events with clear historical analogues
3. Time-bounded events with known frequency

Markets to AVOID:
- One-off geopolitical events
- Markets contingent on other markets
- Vague resolution criteria
- Very long time horizons with no precedent
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from src.models.market import Market


class MarketCategory(Enum):
    """Categories of markets by base-rate amenability."""
    EXCELLENT = "excellent"  # Clear historical base rate available
    GOOD = "good"  # Can estimate from similar events
    MARGINAL = "marginal"  # Rough estimate possible
    POOR = "poor"  # One-off or highly speculative
    UNKNOWN = "unknown"


@dataclass
class MarketClassification:
    """Classification of a market's suitability for base rate analysis."""
    market_id: str
    category: MarketCategory
    score: float  # 0-1, higher = more amenable
    reasoning: str
    suggested_strategy: Optional[str] = None  # e.g., "weather", "stock", "mention"
    keywords_matched: list[str] = None


# Keywords indicating good base-rate-amenable markets
EXCELLENT_KEYWORDS = [
    # Weather
    "temperature", "rain", "snow", "precipitation", "weather",
    "high temp", "low temp", "degrees",

    # Sports
    "win", "score", "points", "touchdown", "home run",
    "championship", "playoff", "super bowl", "nba finals",

    # Economic indicators (recurring)
    "jobs report", "unemployment", "gdp", "inflation", "cpi",
    "fed rate", "interest rate", "fomc",

    # Financial daily
    "spx", "s&p 500", "dow", "nasdaq", "close above", "close below",
    "up or down",

    # Recurring political events
    "state of the union", "press conference", "briefing",
    "mention", "say the word",
]

GOOD_KEYWORDS = [
    # Elections (have polling/historical data)
    "election", "win the", "electoral", "popular vote",
    "primary", "nomination",

    # Company events
    "earnings", "revenue", "guidance", "layoffs",

    # Award shows
    "oscar", "grammy", "emmy", "golden globe", "best picture",

    # Legal/regulatory
    "ruling", "verdict", "approve", "reject", "conviction",
]

# Keywords indicating poor base-rate markets
POOR_KEYWORDS = [
    # One-off geopolitical
    "invade", "war with", "nuclear", "assassinate",

    # Contingent on other unknowns
    "before gta", "before cyberpunk", "before starfield",
    "if trump", "if biden", "conditional on",

    # Very long horizon speculation
    "by 2030", "by 2040", "by 2050", "ever",

    # Vague or subjective
    "significant", "major", "substantial", "noticeable",
]

# Special patterns for specific strategies
STRATEGY_PATTERNS = {
    "weather": [
        r"temp.*\d+.*[°f]",
        r"rain\s+in",
        r"snow\s+in",
        r"precipitation",
    ],
    "stock": [
        r"sp.*up\s+or\s+down",
        r"close\s+(above|below)",
        r"end\s+(higher|lower)",
        r"(nasdaq|dow|s&p).*close",
    ],
    "mention": [
        r"say\s+(the\s+word|\")",
        r"mention\s+\"",
        r"(press conference|speech|address).*say",
    ],
    "sports": [
        r"(win|beat|defeat)",
        r"(score|points)\s+(over|under)",
        r"(championship|finals|super bowl)",
    ],
}


def classify_market(market: Market) -> MarketClassification:
    """
    Classify a market by its amenability to base rate analysis.

    Returns classification with score, reasoning, and suggested strategy.
    """
    title = market.title.lower()
    description = (market.description or "").lower()
    criteria = (market.resolution_criteria or "").lower()

    full_text = f"{title} {description} {criteria}"

    # Check for poor indicators first (disqualifying)
    poor_matches = [kw for kw in POOR_KEYWORDS if kw in full_text]
    if poor_matches:
        return MarketClassification(
            market_id=market.id,
            category=MarketCategory.POOR,
            score=0.1,
            reasoning=f"Contains speculative/one-off indicators: {poor_matches[:3]}",
            keywords_matched=poor_matches
        )

    # Check for excellent indicators
    excellent_matches = [kw for kw in EXCELLENT_KEYWORDS if kw in full_text]
    good_matches = [kw for kw in GOOD_KEYWORDS if kw in full_text]

    # Check for specific strategy patterns
    strategy = None
    for strat, patterns in STRATEGY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, full_text):
                strategy = strat
                break
        if strategy:
            break

    # Score calculation
    score = 0.5  # Base score

    score += len(excellent_matches) * 0.15
    score += len(good_matches) * 0.08

    if strategy:
        score += 0.2  # Bonus for matching a specific strategy

    # Time horizon check - shorter is better for accuracy
    if "tomorrow" in full_text or "today" in full_text:
        score += 0.1
    elif "this week" in full_text:
        score += 0.05
    elif "2030" in full_text or "2040" in full_text:
        score -= 0.2

    score = max(0, min(1, score))

    # Determine category
    if score >= 0.7:
        category = MarketCategory.EXCELLENT
    elif score >= 0.5:
        category = MarketCategory.GOOD
    elif score >= 0.3:
        category = MarketCategory.MARGINAL
    else:
        category = MarketCategory.POOR

    # Build reasoning
    all_matches = excellent_matches + good_matches
    if all_matches:
        reasoning = f"Matches base-rate keywords: {all_matches[:5]}"
    else:
        reasoning = "No strong indicators of base-rate amenability"

    if strategy:
        reasoning += f". Suggested strategy: {strategy}"

    return MarketClassification(
        market_id=market.id,
        category=category,
        score=score,
        reasoning=reasoning,
        suggested_strategy=strategy,
        keywords_matched=all_matches
    )


def filter_markets_for_analysis(
    markets: list[Market],
    min_score: float = 0.5,
    strategies: Optional[list[str]] = None
) -> list[tuple[Market, MarketClassification]]:
    """
    Filter markets to find those amenable to base rate analysis.

    Args:
        markets: List of markets to filter
        min_score: Minimum classification score
        strategies: Only include markets matching these strategies

    Returns:
        List of (market, classification) tuples, sorted by score
    """
    results = []

    for market in markets:
        classification = classify_market(market)

        if classification.score < min_score:
            continue

        if strategies and classification.suggested_strategy not in strategies:
            continue

        results.append((market, classification))

    # Sort by score descending
    results.sort(key=lambda x: x[1].score, reverse=True)

    return results


def get_strategy_markets(
    markets: list[Market],
    strategy: str
) -> list[Market]:
    """Get markets suitable for a specific strategy."""
    results = filter_markets_for_analysis(
        markets,
        min_score=0.3,
        strategies=[strategy]
    )
    return [m for m, _ in results]


# Summary of which markets work best for each approach
STRATEGY_SUMMARY = """
## Market Strategy Guide

### Base Rate Analysis (this tool's main approach)
BEST FOR:
- Recurring events with historical frequency
- Weather markets
- Economic indicator releases
- Sports season stats

AVOID:
- One-off geopolitical events
- Long-horizon speculation
- Contingent markets

### Weather Markets (use weather_markets.py)
BEST FOR:
- Temperature over/under
- Rain/precipitation probability
- Snow amounts
- Any market tied to NWS-forecasted metrics

### Stock Direction (use stock_direction.py)
BEST FOR:
- "SPX up or down today"
- Index close above/below
- Daily stock movements
- Markets during trading hours

### Mention Markets (use mention_markets.py)
BEST FOR:
- "Will X say Y in speech"
- Press conference word mentions
- Live event transcripts

REQUIRES:
- Real-time transcript access
- Active monitoring during events

### Markets That Are Hard to Analyze
- "Will X invade Y" - no base rate
- "Before Z happens" where Z is unknown
- Very low probability events (<1%)
- Highly conditional markets
"""
