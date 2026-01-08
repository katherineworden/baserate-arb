"""FastAPI web application for base rate analysis."""

import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.requests import Request
from pydantic import BaseModel
from dotenv import load_dotenv

from src.models.market import Platform, BaseRateUnit
from src.storage import MarketStorage, WatchlistStorage
from src.analyzer import MarketAnalyzer, FilterCriteria, calculate_portfolio_kelly
from src.clients.kalshi import KalshiClient
from src.clients.polymarket import PolymarketClient
from src.clients.odds_api import OddsAPIClient
from src.agents.base_rate_agent import BaseRateAgent, EnhancedBaseRateAgent

load_dotenv()

app = FastAPI(title="Base Rate Arbitrage", version="1.0.0")

# Mount static files
static_path = os.path.join(os.path.dirname(__file__), "static")
templates_path = os.path.join(os.path.dirname(__file__), "templates")

if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

templates = Jinja2Templates(directory=templates_path)

# Initialize storage
storage = MarketStorage()
watchlist = WatchlistStorage()
analyzer = MarketAnalyzer(storage)

# Background task status
task_status = {"running": False, "message": "", "progress": 0}


# Request/Response models
class FilterParams(BaseModel):
    min_edge: float = 0.02
    min_ev: float = 1.05
    min_quantity: int = 100
    min_kelly: float = 0.001
    max_kelly: float = 1.0
    platforms: Optional[list[str]] = None
    categories: Optional[list[str]] = None


class PortfolioParams(BaseModel):
    bankroll: float = 10000
    max_position_pct: float = 0.1
    kelly_fraction: float = 0.5


class MarketSearchParams(BaseModel):
    query: str
    platform: Optional[str] = None
    limit: int = 50


# API Routes
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main page with opportunity table."""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "market_count": storage.market_count,
        "base_rate_count": storage.base_rate_count
    })


@app.get("/api/opportunities")
async def get_opportunities(
    min_edge: float = Query(0.02),
    min_ev: float = Query(1.05),
    min_quantity: int = Query(100),
    min_kelly: float = Query(0.001),
    max_kelly: float = Query(1.0),
    platforms: Optional[str] = Query(None),
    categories: Optional[str] = Query(None),
    sort_by: str = Query("expected_value"),
    sort_desc: bool = Query(True)
):
    """Get filtered opportunities."""
    criteria = FilterCriteria(
        min_edge=min_edge,
        min_ev=min_ev,
        min_quantity=min_quantity,
        min_kelly=min_kelly,
        max_kelly=max_kelly,
        platforms=[Platform(p) for p in platforms.split(",")] if platforms else None,
        categories=categories.split(",") if categories else None
    )

    opportunities = analyzer.find_opportunities(criteria, min_quantity=min_quantity)

    # Sort
    sort_keys = {
        "expected_value": lambda x: x.expected_value,
        "edge": lambda x: x.edge,
        "kelly": lambda x: x.kelly_fraction,
        "fair_prob": lambda x: x.fair_probability,
        "quantity": lambda x: x.available_quantity
    }

    if sort_by in sort_keys:
        opportunities.sort(key=sort_keys[sort_by], reverse=sort_desc)

    # Convert to dicts
    result = [opp.to_dict() for opp in opportunities]
    stats = analyzer.get_summary_stats(opportunities)

    return {"opportunities": result, "stats": stats}


@app.get("/api/markets")
async def get_markets(
    platform: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    has_base_rate: Optional[bool] = Query(None)
):
    """Get stored markets."""
    plat = Platform(platform) if platform else None
    markets = storage.get_markets(platform=plat, category=category, has_base_rate=has_base_rate)
    return {"markets": [m.to_dict() for m in markets], "count": len(markets)}


@app.get("/api/market/{market_id}")
async def get_market(market_id: str):
    """Get a single market."""
    market = storage.get_market(market_id)
    if not market:
        raise HTTPException(status_code=404, detail="Market not found")

    # Get analysis
    opportunities = analyzer.analyze_market(market)

    return {
        "market": market.to_dict(),
        "opportunities": [o.to_dict() for o in opportunities]
    }


@app.post("/api/fetch/kalshi")
async def fetch_kalshi_markets(
    background_tasks: BackgroundTasks,
    series_ticker: Optional[str] = None,
    min_volume: float = 0,
    limit: int = 100
):
    """Fetch markets from Kalshi."""
    api_key = os.getenv("KALSHI_API_KEY")
    private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH")

    async def fetch_task():
        global task_status
        task_status = {"running": True, "message": "Fetching Kalshi markets...", "progress": 0}

        try:
            with KalshiClient(api_key=api_key, private_key_path=private_key_path) as client:
                markets = client.fetch_markets_with_books(
                    series_ticker=series_ticker,
                    min_volume=min_volume,
                    limit=limit
                )
                storage.save_markets(markets)
                task_status = {
                    "running": False,
                    "message": f"Fetched {len(markets)} markets from Kalshi",
                    "progress": 100
                }
        except Exception as e:
            task_status = {"running": False, "message": f"Error: {str(e)}", "progress": 0}

    background_tasks.add_task(fetch_task)
    return {"status": "started", "message": "Fetching Kalshi markets in background"}


@app.post("/api/fetch/polymarket")
async def fetch_polymarket_markets(
    background_tasks: BackgroundTasks,
    min_liquidity: float = 0,
    limit: int = 100
):
    """Fetch markets from Polymarket."""
    async def fetch_task():
        global task_status
        task_status = {"running": True, "message": "Fetching Polymarket markets...", "progress": 0}

        try:
            with PolymarketClient() as client:
                markets = client.fetch_markets_with_prices(
                    min_liquidity=min_liquidity,
                    limit=limit
                )
                storage.save_markets(markets)
                task_status = {
                    "running": False,
                    "message": f"Fetched {len(markets)} markets from Polymarket",
                    "progress": 100
                }
        except Exception as e:
            task_status = {"running": False, "message": f"Error: {str(e)}", "progress": 0}

    background_tasks.add_task(fetch_task)
    return {"status": "started", "message": "Fetching Polymarket markets in background"}


@app.post("/api/research/base_rate/{market_id}")
async def research_base_rate(market_id: str, background_tasks: BackgroundTasks):
    """Research base rate for a specific market."""
    market = storage.get_market(market_id)
    if not market:
        raise HTTPException(status_code=404, detail="Market not found")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    async def research_task():
        global task_status
        task_status = {
            "running": True,
            "message": f"Researching base rate for: {market.title[:50]}...",
            "progress": 50
        }

        try:
            with BaseRateAgent(api_key=api_key) as agent:
                base_rate = agent.research_base_rate(market)
                if base_rate:
                    storage.save_base_rate(market_id, base_rate)
                    task_status = {
                        "running": False,
                        "message": f"Base rate calculated: {base_rate.rate:.4f} ({base_rate.unit.value})",
                        "progress": 100
                    }
                else:
                    task_status = {
                        "running": False,
                        "message": "Could not determine base rate",
                        "progress": 100
                    }
        except Exception as e:
            task_status = {"running": False, "message": f"Error: {str(e)}", "progress": 0}

    background_tasks.add_task(research_task)
    return {"status": "started", "message": "Researching base rate in background"}


@app.post("/api/research/batch")
async def research_batch_base_rates(
    background_tasks: BackgroundTasks,
    limit: int = 10,
    skip_existing: bool = True
):
    """Research base rates for multiple markets."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    markets = storage.get_markets(has_base_rate=False if skip_existing else None)[:limit]

    async def batch_task():
        global task_status
        task_status = {
            "running": True,
            "message": f"Researching base rates for {len(markets)} markets...",
            "progress": 0
        }

        try:
            with BaseRateAgent(api_key=api_key) as agent:
                for i, market in enumerate(markets):
                    task_status["message"] = f"({i+1}/{len(markets)}) {market.title[:40]}..."
                    task_status["progress"] = int((i / len(markets)) * 100)

                    try:
                        base_rate = agent.research_base_rate(market)
                        if base_rate:
                            storage.save_base_rate(market.id, base_rate)
                    except Exception:
                        continue

                task_status = {
                    "running": False,
                    "message": f"Completed research for {len(markets)} markets",
                    "progress": 100
                }
        except Exception as e:
            task_status = {"running": False, "message": f"Error: {str(e)}", "progress": 0}

    background_tasks.add_task(batch_task)
    return {"status": "started", "message": f"Researching {len(markets)} markets in background"}


@app.get("/api/task/status")
async def get_task_status():
    """Get background task status."""
    return task_status


@app.post("/api/portfolio/kelly")
async def calculate_kelly_portfolio(params: PortfolioParams):
    """Calculate Kelly-optimal portfolio."""
    opportunities = analyzer.find_opportunities()

    positions = calculate_portfolio_kelly(
        opportunities,
        bankroll=params.bankroll,
        max_position_pct=params.max_position_pct,
        kelly_fraction=params.kelly_fraction
    )

    total_allocated = sum(p["total_cost"] for p in positions.values())

    return {
        "positions": positions,
        "total_allocated": total_allocated,
        "remaining_bankroll": params.bankroll - total_allocated,
        "position_count": len(positions)
    }


@app.get("/api/sportsbook/odds")
async def get_sportsbook_odds(sport: Optional[str] = None):
    """Get sportsbook odds for reference."""
    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        return {"error": "ODDS_API_KEY not configured", "odds": []}

    try:
        with OddsAPIClient(api_key=api_key) as client:
            if sport:
                sports = [sport]
            else:
                sports = ["americanfootball_nfl", "basketball_nba", "baseball_mlb"]

            odds = client.fetch_all_odds(sports=sports)
            return {
                "odds": [o.to_dict() for o in odds],
                "remaining_requests": client.remaining_requests
            }
    except Exception as e:
        return {"error": str(e), "odds": []}


@app.post("/api/watchlist/add/{market_id}")
async def add_to_watchlist(market_id: str):
    """Add market to watchlist."""
    watchlist.add(market_id)
    return {"status": "added", "market_id": market_id}


@app.delete("/api/watchlist/remove/{market_id}")
async def remove_from_watchlist(market_id: str):
    """Remove market from watchlist."""
    watchlist.remove(market_id)
    return {"status": "removed", "market_id": market_id}


@app.get("/api/watchlist")
async def get_watchlist():
    """Get watchlist markets."""
    ids = watchlist.get_all()
    markets = [storage.get_market(mid) for mid in ids]
    markets = [m for m in markets if m is not None]
    return {"markets": [m.to_dict() for m in markets], "count": len(markets)}


def create_app():
    """Create and configure the app."""
    return app


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host=host, port=port)
