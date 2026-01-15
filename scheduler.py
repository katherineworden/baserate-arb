#!/usr/bin/env python3
"""
Scheduled job runner for base rate arbitrage.

Runs on a schedule to:
1. Scan markets for opportunities
2. Execute paper/live trades
3. Generate daily/weekly reports
4. Send notifications (optional)

Deploy on DigitalOcean, AWS, or any server.
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('data/scheduler.log')
    ]
)
logger = logging.getLogger(__name__)

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()


def research_base_rates(markets, storage):
    """Use LLM agent to research base rates for markets."""
    from src.agents.base_rate_agent import BaseRateAgent

    logger.info(f"Researching base rates for {len(markets)} markets...")

    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("No ANTHROPIC_API_KEY set, skipping research")
            return
        agent = BaseRateAgent(api_key=api_key)

        for market in markets:
            try:
                logger.info(f"Researching: {market.title[:60]}...")
                base_rate = agent.research_base_rate(market)

                if base_rate:
                    market.base_rate = base_rate
                    storage.save_market(market)
                    logger.info(f"  -> Found base rate: {base_rate.rate:.1%} ({base_rate.unit.value})")
                else:
                    logger.info(f"  -> No base rate found")

            except Exception as e:
                logger.warning(f"  -> Research failed: {e}")
                continue

    except Exception as e:
        logger.error(f"Base rate research failed: {e}")


def scan_and_trade():
    """Scan markets and execute paper trades."""
    from src.clients.kalshi import KalshiClient
    from src.analyzer import MarketAnalyzer, FilterCriteria
    from src.trading.paper_trader import PaperTrader
    from src.storage import MarketStorage

    logger.info("Starting market scan...")

    try:
        # Initialize
        client = KalshiClient()
        storage = MarketStorage()
        analyzer = MarketAnalyzer(storage)
        trader = PaperTrader()

        # Fetch markets
        markets = client.fetch_markets_with_books(limit=50)
        logger.info(f"Fetched {len(markets)} markets")

        # Load stored base rates
        for market in markets:
            stored = storage.get_market(market.id)
            if stored and stored.base_rate:
                market.base_rate = stored.base_rate

        # Filter to markets with base rates
        markets_with_rates = [m for m in markets if m.base_rate]
        logger.info(f"Markets with base rates: {len(markets_with_rates)}")

        # Auto-research base rates for markets that don't have them
        markets_without_rates = [m for m in markets if not m.base_rate]
        if markets_without_rates and os.getenv("ANTHROPIC_API_KEY"):
            research_base_rates(markets_without_rates[:5], storage)  # Research up to 5 per scan
            # Reload markets with new base rates
            for market in markets:
                stored = storage.get_market(market.id)
                if stored and stored.base_rate:
                    market.base_rate = stored.base_rate
            markets_with_rates = [m for m in markets if m.base_rate]
            logger.info(f"Markets with base rates after research: {len(markets_with_rates)}")

        if not markets_with_rates:
            logger.info("No markets with base rates yet. Will research more next scan.")
            return

        # Find opportunities
        criteria = FilterCriteria(
            min_edge=0.03,
            min_ev=1.05,
            min_quantity=100
        )
        opportunities = analyzer.find_opportunities(markets_with_rates, criteria)
        logger.info(f"Found {len(opportunities)} opportunities")

        # Execute paper trades
        if opportunities:
            messages = trader.simulate_from_opportunities(
                opportunities,
                max_positions=5,
                position_size=50,
                min_edge=0.03
            )
            for msg in messages:
                logger.info(f"Trade: {msg}")

        # Update prices for existing positions
        current_prices = {m.id: m.yes_price for m in markets}
        trader.update_all_prices(current_prices)

        client.close()
        logger.info("Scan complete")

    except Exception as e:
        logger.error(f"Scan failed: {e}", exc_info=True)


def generate_daily_report():
    """Generate and log daily report."""
    from src.trading.reports import ReportGenerator, generate_email_summary

    logger.info("Generating daily report...")

    try:
        generator = ReportGenerator()
        report = generator.generate_report(period="daily", save=True)

        if report:
            logger.info(f"\n{report.to_text()}")

            # Optionally send email (requires SMTP setup)
            if os.getenv("SMTP_HOST"):
                send_email_report(report)
        else:
            logger.info("No trading data for report")

    except Exception as e:
        logger.error(f"Report generation failed: {e}", exc_info=True)


def generate_weekly_report():
    """Generate and log weekly report."""
    from src.trading.reports import ReportGenerator

    logger.info("Generating weekly report...")

    try:
        generator = ReportGenerator()
        report = generator.generate_report(period="weekly", save=True)

        if report:
            logger.info(f"\n{report.to_text()}")

            if os.getenv("SMTP_HOST"):
                send_email_report(report)

    except Exception as e:
        logger.error(f"Weekly report failed: {e}", exc_info=True)


def send_email_report(report):
    """Send report via email."""
    import smtplib
    from email.mime.text import MIMEText
    from src.trading.reports import generate_email_summary

    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    to_email = os.getenv("REPORT_EMAIL")

    if not all([smtp_host, smtp_user, smtp_pass, to_email]):
        logger.warning("Email not configured - skipping")
        return

    try:
        msg = MIMEText(generate_email_summary(report))
        msg["Subject"] = f"Base Rate Arb - {report.period.title()} Report"
        msg["From"] = smtp_user
        msg["To"] = to_email

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        logger.info(f"Report sent to {to_email}")

    except Exception as e:
        logger.error(f"Email send failed: {e}")


def run_scheduler():
    """Main scheduler loop."""
    logger.info("Starting scheduler...")
    logger.info(f"KALSHI_API_KEY: {'Set' if os.getenv('KALSHI_API_KEY') else 'Not set'}")
    logger.info(f"ANTHROPIC_API_KEY: {'Set' if os.getenv('ANTHROPIC_API_KEY') else 'Not set'}")

    # Configuration
    scan_interval = int(os.getenv("SCAN_INTERVAL_MINUTES", 60))  # Default: hourly
    last_scan = datetime.min
    last_daily_report = datetime.min
    last_weekly_report = datetime.min

    while True:
        now = datetime.utcnow()

        # Run scan every interval
        if (now - last_scan) >= timedelta(minutes=scan_interval):
            scan_and_trade()
            last_scan = now

        # Daily report at 00:00 UTC
        if now.date() > last_daily_report.date() and now.hour >= 0:
            generate_daily_report()
            last_daily_report = now

        # Weekly report on Monday at 00:00 UTC
        if now.weekday() == 0 and now.date() > last_weekly_report.date():
            generate_weekly_report()
            last_weekly_report = now

        # Sleep for 1 minute before checking again
        time.sleep(60)


def run_once():
    """Run a single scan (for testing)."""
    logger.info("Running single scan...")
    scan_and_trade()
    generate_daily_report()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Base Rate Arb Scheduler")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--report", choices=["daily", "weekly"], help="Generate report only")

    args = parser.parse_args()

    if args.once:
        run_once()
    elif args.report == "daily":
        generate_daily_report()
    elif args.report == "weekly":
        generate_weekly_report()
    else:
        run_scheduler()
