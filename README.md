# Base Rate Arbitrage Scanner

Find mispriced prediction markets by comparing market prices to historical base rates.

## How It Works

1. **Fetch Markets** - Pull active markets from Kalshi and/or Polymarket
2. **Research Base Rates** - LLM agent researches historical probabilities for events
3. **Calculate Fair Value** - Adjusts base rates for time remaining until resolution
4. **Find Edge** - Compares fair probability to market price, calculates expected value
5. **Size Positions** - Uses Kelly criterion to recommend position sizes

## Features

- **Multi-platform support**: Kalshi and Polymarket
- **Time-adjusted base rates**: Properly handles per-year, per-month, per-event rates
- **LLM-powered research**: Uses Claude to find and analyze historical data
- **Kelly criterion**: Optimal position sizing recommendations
- **Web UI**: Sortable, filterable table of opportunities
- **CLI**: Command-line interface for automation
- **Sportsbook integration**: Reference odds from The Odds API

## Installation

```bash
# Clone the repo
git clone https://github.com/yourusername/baserate-arb.git
cd baserate-arb

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your API keys
```

## Configuration

Required:
- `ANTHROPIC_API_KEY` - For base rate research agent

Optional:
- `KALSHI_API_KEY` / `KALSHI_PRIVATE_KEY_PATH` - For Kalshi market data
- `POLYMARKET_API_KEY` / etc - For Polymarket (public API works without auth)
- `ODDS_API_KEY` - For sportsbook reference odds

## Usage

### Web UI

```bash
python run.py
# Open http://127.0.0.1:8000
```

### CLI

```bash
# Fetch markets
python cli.py fetch --platform all --limit 100

# Research base rates (uses LLM)
python cli.py research --limit 20

# Show opportunities
python cli.py opportunities --min-edge 3 --min-ev 1.1

# Export to JSON
python cli.py export -o opportunities.json

# Start web server
python cli.py serve --port 8000
```

## Understanding the Output

| Column | Meaning |
|--------|---------|
| Fair % | Base rate probability adjusted for time remaining |
| Market % | Current market implied probability |
| Edge | Fair - Market (positive = underpriced) |
| EV | Expected value multiplier (>1 = positive EV) |
| Kelly % | Recommended bankroll fraction to bet |
| Price | Limit order price in cents |
| Qty | Available quantity at that price |

## Base Rate Units

The agent categorizes base rates by their natural unit:

- **per_year**: Annual occurrence rate (e.g., hurricanes hitting Florida)
- **per_month**: Monthly rate
- **per_event**: Per specific event type (e.g., per press conference, per game)
- **absolute**: One-time probability (e.g., will X happen before 2025)

For time-based rates, probability is calculated as:
```
P(occurs before resolution) = 1 - (1 - rate)^periods_remaining
```

## Example

Market: "Will there be a magnitude 7+ earthquake in California in 2025?"
- Base rate: ~2% per year (historical average)
- Resolution: Dec 31, 2025
- Days remaining: 200
- Adjusted probability: 1 - (1 - 0.02)^(200/365) = ~1.1%
- Market price: 5%
- Edge: -3.9% (market overpriced, bet NO)

## API Endpoints

- `GET /api/opportunities` - Get filtered opportunities
- `GET /api/markets` - Get stored markets
- `POST /api/fetch/kalshi` - Fetch Kalshi markets
- `POST /api/fetch/polymarket` - Fetch Polymarket markets
- `POST /api/research/base_rate/{id}` - Research single market
- `POST /api/research/batch` - Research multiple markets
- `POST /api/portfolio/kelly` - Calculate Kelly portfolio

## Architecture

```
src/
├── models/
│   └── market.py       # Data models (Market, BaseRate, etc.)
├── clients/
│   ├── kalshi.py       # Kalshi API client
│   ├── polymarket.py   # Polymarket API client
│   └── odds_api.py     # Sportsbook odds client
├── agents/
│   └── base_rate_agent.py  # LLM research agent
├── web/
│   ├── app.py          # FastAPI application
│   └── templates/      # HTML templates
├── storage.py          # Data persistence
└── analyzer.py         # Opportunity analysis
```

## Limitations

- Base rate research quality depends on available data
- LLM may not find good sources for niche markets
- Order book data may be stale
- Not financial advice - use at your own risk

## License

MIT
