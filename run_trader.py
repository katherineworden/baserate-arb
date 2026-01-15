#!/usr/bin/env python3
"""
Base Rate Arbitrage Trading Runner

This script helps you:
1. Scan markets for opportunities
2. Paper trade to test strategies
3. Execute live trades (when ready)

Usage:
    # Scan for opportunities
    python run_trader.py scan --platform kalshi --limit 20

    # Start paper trading
    python run_trader.py paper --balance 1000

    # Check paper trading status
    python run_trader.py status

    # Go live (careful!)
    python run_trader.py live --dry-run  # Test without executing
    python run_trader.py live            # Real trades
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()


def cmd_scan(args):
    """Scan for opportunities."""
    from src.clients.kalshi import KalshiClient
    from src.clients.polymarket import PolymarketClient
    from src.analyzer import MarketAnalyzer, FilterCriteria
    from src.storage import MarketStorage

    print(f"\n📊 Scanning {args.platform} for opportunities...\n")

    storage = MarketStorage()
    analyzer = MarketAnalyzer()

    # Fetch markets
    if args.platform == "kalshi":
        client = KalshiClient()
        markets = client.fetch_markets_with_prices(limit=args.limit)
    else:
        client = PolymarketClient()
        if args.category:
            markets = client.get_markets_by_category(
                categories=[args.category],
                base_rate_only=args.base_rate_only,
                limit=args.limit
            )
        else:
            markets = client.fetch_markets_with_prices(limit=args.limit)

    print(f"Found {len(markets)} markets\n")

    # Load existing base rates
    for market in markets:
        stored = storage.get_market(market.id)
        if stored and stored.base_rate:
            market.base_rate = stored.base_rate
            print(f"  ✓ Loaded base rate for: {market.title[:50]}...")

    # Find markets with base rates
    markets_with_rates = [m for m in markets if m.base_rate]

    if not markets_with_rates:
        print("\n⚠️  No markets have base rates yet.")
        print("   Run the web UI to research base rates, or use the agent directly.")
        print("\n   To start the web UI:")
        print("   python -m src.web.app")
        return

    # Find opportunities
    criteria = FilterCriteria(
        min_edge=args.min_edge,
        min_ev=args.min_ev,
        platforms=[args.platform] if args.platform != "all" else None
    )

    opportunities = analyzer.find_opportunities(markets_with_rates, criteria)

    if not opportunities:
        print("\n😐 No opportunities found matching criteria.")
        print(f"   Min edge: {args.min_edge:.1%}, Min EV: {args.min_ev:.2f}x")
        return

    print(f"\n🎯 Found {len(opportunities)} opportunities:\n")
    print("-" * 80)

    for i, opp in enumerate(opportunities[:10], 1):
        print(f"{i}. {opp.market.title[:60]}...")
        print(f"   Side: {opp.side} | Price: {opp.market_probability*100:.0f}¢ | Fair: {opp.fair_probability*100:.0f}¢")
        print(f"   Edge: {opp.edge:.1%} | EV: {opp.expected_value:.2f}x | Kelly: {opp.kelly_fraction:.1%}")
        print()


def cmd_paper(args):
    """Paper trading commands."""
    from src.trading.paper_trader import PaperTrader

    trader = PaperTrader(initial_balance=args.balance)

    if args.action == "reset":
        trader.reset_account(args.balance)
        print(f"✓ Paper account reset with ${args.balance:.2f}")
        return

    if args.action == "status":
        summary = trader.get_summary()
        print("\n📈 Paper Trading Account\n")
        print("-" * 40)
        for key, value in summary.items():
            print(f"  {key.replace('_', ' ').title()}: {value}")

        positions = trader.get_open_positions()
        if positions:
            print("\n📊 Open Positions:\n")
            for p in positions:
                print(f"  • {p['title']}")
                print(f"    {p['side']} {p['qty']} @ {p['entry']} (target: {p['target']}, edge: {p['edge']})")
                print(f"    Unrealized: {p['unrealized_pnl']}")
                print()

    if args.action == "history":
        closed = trader.get_closed_positions(limit=args.limit)
        if closed:
            print("\n📜 Recent Closed Positions:\n")
            for p in closed:
                print(f"  • {p['title']}")
                print(f"    {p['side']} @ {p['entry']} → {p['exit']} | PnL: {p['pnl']} ({p['result']})")


def cmd_live(args):
    """Live trading commands."""
    from src.trading.live_trader import KalshiLiveTrader, TradeConfig

    config = TradeConfig(
        max_position_size=args.max_size,
        max_total_exposure=args.max_exposure,
        min_edge=args.min_edge,
        dry_run=args.dry_run
    )

    trader = KalshiLiveTrader(config=config, dry_run=args.dry_run)

    if args.dry_run:
        print("\n🧪 DRY RUN MODE - No real trades will be executed\n")
    else:
        print("\n⚠️  LIVE TRADING MODE - Real money at risk!\n")
        confirm = input("Type 'YES' to confirm: ")
        if confirm != "YES":
            print("Aborted.")
            return

    # Get balance
    balance = trader.get_balance()
    print(f"Account balance: {balance}")

    # Get positions
    positions = trader.get_positions()
    print(f"Current positions: {len(positions)}")


def cmd_status(args):
    """Quick status check."""
    from src.trading.paper_trader import PaperTrader
    from src.storage import MarketStorage

    print("\n📊 Base Rate Arb Status\n")
    print("=" * 50)

    # Paper trading status
    trader = PaperTrader()
    summary = trader.get_summary()
    print("\n💵 Paper Trading:")
    print(f"   Balance: {summary['current_balance']}")
    print(f"   Total PnL: {summary['total_pnl']}")
    print(f"   ROI: {summary['roi']}")
    print(f"   Win Rate: {summary['win_rate']} ({summary['winning_trades']}/{summary['total_trades']})")
    print(f"   Open Positions: {summary['open_positions']}")

    # Storage status
    storage = MarketStorage()
    markets = storage.list_markets()
    markets_with_rates = [m for m in markets if m.base_rate]
    print(f"\n📚 Research:")
    print(f"   Markets tracked: {len(markets)}")
    print(f"   With base rates: {len(markets_with_rates)}")

    # API status
    print("\n🔑 API Keys:")
    print(f"   KALSHI_API_KEY: {'✓ Set' if os.getenv('KALSHI_API_KEY') else '✗ Not set'}")
    print(f"   ANTHROPIC_API_KEY: {'✓ Set' if os.getenv('ANTHROPIC_API_KEY') else '✗ Not set'}")


def main():
    parser = argparse.ArgumentParser(
        description="Base Rate Arbitrage Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Scan command
    scan_parser = subparsers.add_parser("scan", help="Scan for opportunities")
    scan_parser.add_argument("--platform", choices=["kalshi", "polymarket", "all"], default="kalshi")
    scan_parser.add_argument("--limit", type=int, default=50)
    scan_parser.add_argument("--min-edge", type=float, default=0.03)
    scan_parser.add_argument("--min-ev", type=float, default=1.05)
    scan_parser.add_argument("--category", type=str, help="Category filter (Polymarket)")
    scan_parser.add_argument("--base-rate-only", action="store_true", help="Only base-rate-amenable markets")

    # Paper trading command
    paper_parser = subparsers.add_parser("paper", help="Paper trading")
    paper_parser.add_argument("action", choices=["status", "reset", "history"], default="status", nargs="?")
    paper_parser.add_argument("--balance", type=float, default=1000)
    paper_parser.add_argument("--limit", type=int, default=20)

    # Live trading command
    live_parser = subparsers.add_parser("live", help="Live trading (careful!)")
    live_parser.add_argument("--dry-run", action="store_true", default=True, help="Test without executing")
    live_parser.add_argument("--max-size", type=int, default=100, help="Max contracts per trade")
    live_parser.add_argument("--max-exposure", type=float, default=500, help="Max total $ exposure")
    live_parser.add_argument("--min-edge", type=float, default=0.05, help="Minimum edge to trade")

    # Status command
    status_parser = subparsers.add_parser("status", help="Check system status")

    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "paper":
        cmd_paper(args)
    elif args.command == "live":
        cmd_live(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
