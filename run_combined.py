#!/usr/bin/env python3
"""
Combined Arbitrage Scanner

Runs both instant arbitrage detection (orderbook spreads) and base rate
arbitrage detection (mispriced markets vs historical probability).

Schedule:
- Every 5 minutes: Instant arbitrage scan (free, catches quick profits)
- Every hour: Base rate scan + research (costs ~$0.03/market)
- Daily at midnight: Performance report

Usage:
    python run_combined.py              # Run continuous scheduler
    python run_combined.py --once       # Single scan of both systems
    python run_combined.py --instant    # Only instant arb scan
    python run_combined.py --baserate   # Only base rate scan
"""

import os
import sys
import time
import json
import logging
import argparse
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('data/combined_scanner.log')
    ]
)
logger = logging.getLogger(__name__)

# Ensure data directory exists
Path('data').mkdir(exist_ok=True)
Path('data/opportunities').mkdir(exist_ok=True)


class CombinedScanner:
    """
    Combined scanner that runs both instant arbitrage and base rate strategies.
    """

    def __init__(self, auto_execute: bool = False, paper_trade: bool = True):
        """
        Initialize the combined scanner.

        Args:
            auto_execute: If True, execute real trades (DANGEROUS)
            paper_trade: If True, simulate trades for tracking
        """
        self.auto_execute = auto_execute
        self.paper_trade = paper_trade

        # Stats tracking
        self.stats = {
            'instant_scans': 0,
            'baserate_scans': 0,
            'instant_opportunities': 0,
            'baserate_opportunities': 0,
            'trades_executed': 0,
            'paper_trades': 0,
            'total_profit': 0.0,
            'paper_profit': 0.0,
            'start_time': datetime.now().isoformat(),
            'last_instant_scan': None,
            'last_baserate_scan': None,
        }

        # Load previous stats if they exist
        self._load_stats()

        # Initialize clients lazily
        self._instant_bot = None
        self._kalshi_client = None
        self._analyzer = None

        logger.info("Combined Scanner initialized")
        logger.info(f"  Auto-execute: {auto_execute}")
        logger.info(f"  Paper trading: {paper_trade}")

    def _load_stats(self):
        """Load stats from previous run."""
        stats_file = Path('data/scanner_stats.json')
        if stats_file.exists():
            try:
                with open(stats_file) as f:
                    saved = json.load(f)
                    # Merge with defaults (in case new fields added)
                    self.stats.update(saved)
                    logger.info("Loaded previous scanner stats")
            except Exception as e:
                logger.warning(f"Could not load stats: {e}")

    def _save_stats(self):
        """Save current stats."""
        try:
            with open('data/scanner_stats.json', 'w') as f:
                json.dump(self.stats, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Could not save stats: {e}")

    @property
    def instant_bot(self):
        """Lazy-load instant arbitrage bot."""
        if self._instant_bot is None:
            from bot import KalshiArbitrageBot
            self._instant_bot = KalshiArbitrageBot(auto_execute_trades=self.auto_execute)
            self._instant_bot.min_liquidity = int(os.getenv('MIN_LIQUIDITY', '10000'))
        return self._instant_bot

    @property
    def kalshi_client(self):
        """Lazy-load Kalshi client for base rate system."""
        if self._kalshi_client is None:
            from src.clients.kalshi import KalshiClient
            self._kalshi_client = KalshiClient()
        return self._kalshi_client

    @property
    def analyzer(self):
        """Lazy-load market analyzer."""
        if self._analyzer is None:
            from src.analyzer import MarketAnalyzer
            self._analyzer = MarketAnalyzer()
        return self._analyzer

    def scan_instant_arbitrage(self, limit: int = 100) -> Tuple[int, int, float]:
        """
        Scan for instant arbitrage opportunities (orderbook spreads).

        Returns:
            Tuple of (arbitrage_count, trade_count, total_profit)
        """
        logger.info(f"Starting instant arbitrage scan (limit={limit})...")
        self.stats['instant_scans'] += 1
        self.stats['last_instant_scan'] = datetime.now().isoformat()

        try:
            arb_opps, trade_opps, executed = self.instant_bot.scan_all_opportunities(
                limit=limit,
                auto_execute=self.auto_execute
            )

            total_opps = len(arb_opps) + len(trade_opps)
            self.stats['instant_opportunities'] += total_opps

            if total_opps > 0:
                logger.info(f"Found {len(trade_opps)} immediate trades, {len(arb_opps)} arbitrage opportunities")

                # Log best opportunities
                if trade_opps:
                    best = trade_opps[0]
                    logger.info(f"  Best trade: {best.market_ticker} - ${best.net_profit:.2f} profit")

                if arb_opps:
                    best = arb_opps[0]
                    logger.info(f"  Best arb: {best.market_ticker} - ${best.profit_per_day:.2f}/day")

                # Save opportunities to file
                self._save_opportunities('instant', trade_opps, arb_opps)

                # Paper trade if enabled
                if self.paper_trade and not self.auto_execute:
                    self._paper_trade_instant(trade_opps)
            else:
                logger.info("No instant arbitrage opportunities found")

            total_profit = sum(t.net_profit for t in trade_opps) if executed > 0 else 0
            self.stats['trades_executed'] += executed
            self.stats['total_profit'] += total_profit

            self._save_stats()
            return total_opps, executed, total_profit

        except Exception as e:
            logger.error(f"Error in instant arbitrage scan: {e}")
            return 0, 0, 0.0

    def scan_baserate_arbitrage(self, limit: int = 50, research_limit: int = 3) -> Tuple[int, List]:
        """
        Scan for base rate arbitrage opportunities (mispriced vs historical probability).

        Args:
            limit: Max markets to fetch
            research_limit: Max markets to research with LLM (costs money)

        Returns:
            Tuple of (opportunity_count, opportunities_list)
        """
        logger.info(f"Starting base rate scan (limit={limit}, research={research_limit})...")
        self.stats['baserate_scans'] += 1
        self.stats['last_baserate_scan'] = datetime.now().isoformat()

        try:
            from src.storage import Storage
            from src.agents.base_rate_agent import BaseRateAgent

            storage = Storage()

            # Fetch markets
            markets = self.kalshi_client.get_markets(limit=limit)
            logger.info(f"Fetched {len(markets)} markets from Kalshi")

            # Filter to markets without base rates
            markets_needing_research = []
            for market in markets:
                existing = storage.get_base_rate(market.id)
                if not existing:
                    markets_needing_research.append(market)

            logger.info(f"{len(markets_needing_research)} markets need base rate research")

            # Research top N markets (this costs API credits)
            if markets_needing_research and research_limit > 0:
                agent = BaseRateAgent()
                researched = 0

                for market in markets_needing_research[:research_limit]:
                    try:
                        logger.info(f"Researching: {market.title[:60]}...")
                        base_rate = agent.research_base_rate(market)

                        if base_rate:
                            storage.save_base_rate(base_rate)
                            market.base_rate = base_rate
                            researched += 1
                            logger.info(f"  -> Rate: {base_rate.rate:.2%} ({base_rate.unit}), confidence: {base_rate.confidence:.0%}")

                        # Rate limit between researches
                        time.sleep(2)

                    except Exception as e:
                        logger.error(f"Error researching {market.id}: {e}")

                logger.info(f"Researched {researched} new base rates")

            # Find opportunities in all markets with base rates
            opportunities = []
            for market in markets:
                if not market.base_rate:
                    market.base_rate = storage.get_base_rate(market.id)

                if market.base_rate:
                    analysis = self.analyzer.analyze_opportunity(market)
                    if analysis and analysis.edge > 0.03:  # 3% minimum edge
                        opportunities.append({
                            'market': market,
                            'analysis': analysis
                        })

            # Sort by expected value
            opportunities.sort(key=lambda x: x['analysis'].ev_multiplier, reverse=True)

            self.stats['baserate_opportunities'] += len(opportunities)

            if opportunities:
                logger.info(f"Found {len(opportunities)} base rate opportunities:")
                for i, opp in enumerate(opportunities[:5], 1):
                    m = opp['market']
                    a = opp['analysis']
                    logger.info(f"  {i}. {m.title[:50]}...")
                    logger.info(f"     Edge: {a.edge:.1%}, EV: {a.ev_multiplier:.2f}x, Kelly: {a.kelly_fraction:.1%}")

                self._save_opportunities('baserate', opportunities)
            else:
                logger.info("No base rate opportunities found above threshold")

            self._save_stats()
            return len(opportunities), opportunities

        except Exception as e:
            logger.error(f"Error in base rate scan: {e}")
            import traceback
            traceback.print_exc()
            return 0, []

    def _paper_trade_instant(self, trade_opps):
        """Record paper trades for instant opportunities."""
        if not trade_opps:
            return

        paper_file = Path('data/paper_trades.json')
        trades = []
        if paper_file.exists():
            try:
                with open(paper_file) as f:
                    trades = json.load(f)
            except:
                pass

        for opp in trade_opps[:3]:  # Top 3 only
            trade = {
                'timestamp': datetime.now().isoformat(),
                'type': 'instant',
                'market': opp.market_ticker,
                'side': opp.side,
                'buy_price': opp.buy_price,
                'sell_price': opp.sell_price,
                'quantity': opp.quantity,
                'net_profit': opp.net_profit,
                'status': 'simulated'
            }
            trades.append(trade)
            self.stats['paper_trades'] += 1
            self.stats['paper_profit'] += opp.net_profit

        with open(paper_file, 'w') as f:
            json.dump(trades[-1000:], f, indent=2)  # Keep last 1000

        logger.info(f"Recorded {min(3, len(trade_opps))} paper trades")

    def _save_opportunities(self, scan_type: str, *args):
        """Save opportunities to timestamped file."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'data/opportunities/{scan_type}_{timestamp}.json'

        data = {
            'timestamp': datetime.now().isoformat(),
            'type': scan_type,
            'opportunities': []
        }

        for arg in args:
            if not arg:
                continue
            for item in arg:
                try:
                    if hasattr(item, '__dict__'):
                        data['opportunities'].append({
                            'ticker': getattr(item, 'market_ticker', 'unknown'),
                            'profit': getattr(item, 'net_profit', 0),
                            'type': type(item).__name__
                        })
                    elif isinstance(item, dict):
                        data['opportunities'].append({
                            'ticker': item.get('market', {}).id if hasattr(item.get('market', {}), 'id') else 'unknown',
                            'edge': item.get('analysis', {}).edge if hasattr(item.get('analysis', {}), 'edge') else 0
                        })
                except:
                    pass

        try:
            with open(filename, 'w') as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Could not save opportunities: {e}")

    def generate_report(self) -> str:
        """Generate a performance report."""
        report = []
        report.append("=" * 60)
        report.append("COMBINED SCANNER PERFORMANCE REPORT")
        report.append("=" * 60)
        report.append(f"Report generated: {datetime.now()}")
        report.append(f"Running since: {self.stats.get('start_time', 'unknown')}")
        report.append("")
        report.append("SCAN STATISTICS:")
        report.append(f"  Instant arb scans: {self.stats['instant_scans']}")
        report.append(f"  Base rate scans: {self.stats['baserate_scans']}")
        report.append(f"  Last instant scan: {self.stats.get('last_instant_scan', 'never')}")
        report.append(f"  Last baserate scan: {self.stats.get('last_baserate_scan', 'never')}")
        report.append("")
        report.append("OPPORTUNITIES FOUND:")
        report.append(f"  Instant opportunities: {self.stats['instant_opportunities']}")
        report.append(f"  Base rate opportunities: {self.stats['baserate_opportunities']}")
        report.append("")
        report.append("TRADING:")
        report.append(f"  Real trades executed: {self.stats['trades_executed']}")
        report.append(f"  Real profit: ${self.stats['total_profit']:.2f}")
        report.append(f"  Paper trades: {self.stats['paper_trades']}")
        report.append(f"  Paper profit: ${self.stats['paper_profit']:.2f}")
        report.append("=" * 60)

        return "\n".join(report)

    def send_email_report(self, report: str, subject: str = None):
        """Send report via email if SMTP is configured."""
        smtp_host = os.getenv('SMTP_HOST')
        smtp_port = int(os.getenv('SMTP_PORT', '587'))
        smtp_user = os.getenv('SMTP_USER')
        smtp_pass = os.getenv('SMTP_PASS')
        report_email = os.getenv('REPORT_EMAIL')

        if not all([smtp_host, smtp_user, smtp_pass, report_email]):
            logger.debug("Email not configured, skipping email report")
            return False

        try:
            if subject is None:
                subject = f"Arbitrage Scanner Report - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

            msg = MIMEMultipart()
            msg['From'] = smtp_user
            msg['To'] = report_email
            msg['Subject'] = subject

            # Add summary stats to subject if there are opportunities
            if self.stats['instant_opportunities'] > 0 or self.stats['baserate_opportunities'] > 0:
                msg['Subject'] = f"[{self.stats['instant_opportunities']} opps] " + subject

            msg.attach(MIMEText(report, 'plain'))

            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)

            logger.info(f"Email report sent to {report_email}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email report: {e}")
            return False

    def run_once(self, instant: bool = True, baserate: bool = True):
        """Run a single scan of specified systems."""
        logger.info("Running single scan...")

        if instant:
            self.scan_instant_arbitrage(limit=100)

        if baserate:
            self.scan_baserate_arbitrage(limit=50, research_limit=3)

        print(self.generate_report())

    def run_continuous(self,
                       instant_interval: int = 300,    # 5 minutes
                       baserate_interval: int = 3600,  # 1 hour
                       report_interval: int = 86400):  # 24 hours
        """
        Run continuous scanning with different intervals for each strategy.

        Args:
            instant_interval: Seconds between instant arb scans (default 5 min)
            baserate_interval: Seconds between base rate scans (default 1 hour)
            report_interval: Seconds between reports (default 24 hours)
        """
        logger.info("Starting continuous scanning...")
        logger.info(f"  Instant arb: every {instant_interval}s ({instant_interval/60:.0f} min)")
        logger.info(f"  Base rate: every {baserate_interval}s ({baserate_interval/3600:.1f} hr)")
        logger.info(f"  Reports: every {report_interval}s ({report_interval/3600:.0f} hr)")
        logger.info("Press Ctrl+C to stop")

        last_instant = 0
        last_baserate = 0
        last_report = 0

        try:
            while True:
                now = time.time()

                # Instant arbitrage scan
                if now - last_instant >= instant_interval:
                    self.scan_instant_arbitrage(limit=100)
                    last_instant = now

                # Base rate scan (less frequent)
                if now - last_baserate >= baserate_interval:
                    self.scan_baserate_arbitrage(limit=50, research_limit=3)
                    last_baserate = now

                # Daily report
                if now - last_report >= report_interval:
                    report = self.generate_report()
                    logger.info("\n" + report)

                    # Save report to file
                    report_file = f'data/report_{datetime.now().strftime("%Y%m%d")}.txt'
                    with open(report_file, 'w') as f:
                        f.write(report)

                    # Send email report
                    self.send_email_report(report, subject="Daily Arbitrage Scanner Report")

                    last_report = now

                # Sleep a bit before checking again
                time.sleep(10)

        except KeyboardInterrupt:
            logger.info("\nStopping scanner...")
            print(self.generate_report())
            self._save_stats()


def main():
    parser = argparse.ArgumentParser(
        description="Combined Arbitrage Scanner - Instant + Base Rate strategies"
    )
    parser.add_argument(
        '--once', action='store_true',
        help='Run a single scan and exit'
    )
    parser.add_argument(
        '--instant', action='store_true',
        help='Only run instant arbitrage scan'
    )
    parser.add_argument(
        '--baserate', action='store_true',
        help='Only run base rate scan'
    )
    parser.add_argument(
        '--auto-execute', action='store_true', dest='auto_execute',
        help='Auto-execute real trades (DANGEROUS - use with caution)'
    )
    parser.add_argument(
        '--no-paper', action='store_true', dest='no_paper',
        help='Disable paper trading simulation'
    )
    parser.add_argument(
        '--instant-interval', type=int, default=300,
        help='Seconds between instant arb scans (default: 300 = 5 min)'
    )
    parser.add_argument(
        '--baserate-interval', type=int, default=3600,
        help='Seconds between base rate scans (default: 3600 = 1 hour)'
    )
    parser.add_argument(
        '--report', action='store_true',
        help='Generate and print performance report'
    )

    args = parser.parse_args()

    # Safety check for auto-execute
    if args.auto_execute:
        print("WARNING: Auto-execute is ENABLED. Real trades will be placed!")
        print("Type 'yes' to confirm: ", end='')
        confirm = input().strip().lower()
        if confirm != 'yes':
            print("Aborted.")
            sys.exit(1)

    scanner = CombinedScanner(
        auto_execute=args.auto_execute,
        paper_trade=not args.no_paper
    )

    if args.report:
        print(scanner.generate_report())
        return

    if args.once:
        # Single scan mode
        run_instant = args.instant or (not args.instant and not args.baserate)
        run_baserate = args.baserate or (not args.instant and not args.baserate)
        scanner.run_once(instant=run_instant, baserate=run_baserate)
    elif args.instant and not args.baserate:
        # Instant only continuous
        scanner.run_continuous(instant_interval=args.instant_interval, baserate_interval=999999999)
    elif args.baserate and not args.instant:
        # Baserate only continuous
        scanner.run_continuous(instant_interval=999999999, baserate_interval=args.baserate_interval)
    else:
        # Full continuous mode
        scanner.run_continuous(
            instant_interval=args.instant_interval,
            baserate_interval=args.baserate_interval
        )


if __name__ == "__main__":
    main()
