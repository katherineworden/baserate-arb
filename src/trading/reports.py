"""Generate trading performance reports."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from dataclasses import dataclass


@dataclass
class PerformanceReport:
    """Summary of trading performance over a period."""
    period: str  # "daily", "weekly", "monthly", "all_time"
    start_date: datetime
    end_date: datetime

    # Balance
    starting_balance: float
    ending_balance: float
    net_pnl: float
    roi_percent: float

    # Trades
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float

    # Positions
    open_positions: int
    unrealized_pnl: float

    # Best/Worst
    best_trade_pnl: float
    best_trade_market: str
    worst_trade_pnl: float
    worst_trade_market: str

    # Edge analysis
    avg_edge_taken: float
    avg_kelly_used: float

    def to_text(self) -> str:
        """Generate human-readable report."""
        lines = [
            f"📊 {self.period.upper()} PERFORMANCE REPORT",
            f"   Period: {self.start_date.strftime('%Y-%m-%d')} to {self.end_date.strftime('%Y-%m-%d')}",
            "",
            "💰 BALANCE",
            f"   Starting: ${self.starting_balance:.2f}",
            f"   Ending:   ${self.ending_balance:.2f}",
            f"   Net P&L:  ${self.net_pnl:+.2f}",
            f"   ROI:      {self.roi_percent:+.1f}%",
            "",
            "📈 TRADES",
            f"   Total:    {self.total_trades}",
            f"   Winners:  {self.winning_trades}",
            f"   Losers:   {self.losing_trades}",
            f"   Win Rate: {self.win_rate:.1f}%",
            "",
            "📊 OPEN POSITIONS",
            f"   Count:         {self.open_positions}",
            f"   Unrealized:    ${self.unrealized_pnl:+.2f}",
        ]

        if self.best_trade_pnl != 0:
            lines.extend([
                "",
                "🏆 BEST/WORST TRADES",
                f"   Best:  ${self.best_trade_pnl:+.2f} ({self.best_trade_market[:40]}...)",
                f"   Worst: ${self.worst_trade_pnl:+.2f} ({self.worst_trade_market[:40]}...)",
            ])

        if self.avg_edge_taken > 0:
            lines.extend([
                "",
                "📐 EDGE ANALYSIS",
                f"   Avg Edge:  {self.avg_edge_taken:.1%}",
                f"   Avg Kelly: {self.avg_kelly_used:.1%}",
            ])

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON storage."""
        return {
            "period": self.period,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "starting_balance": self.starting_balance,
            "ending_balance": self.ending_balance,
            "net_pnl": self.net_pnl,
            "roi_percent": self.roi_percent,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.win_rate,
            "open_positions": self.open_positions,
            "unrealized_pnl": self.unrealized_pnl,
            "best_trade_pnl": self.best_trade_pnl,
            "best_trade_market": self.best_trade_market,
            "worst_trade_pnl": self.worst_trade_pnl,
            "worst_trade_market": self.worst_trade_market,
            "avg_edge_taken": self.avg_edge_taken,
            "avg_kelly_used": self.avg_kelly_used,
        }


class ReportGenerator:
    """Generate performance reports from paper trading data."""

    def __init__(self, data_dir: str = "data/paper_trading"):
        self.data_dir = Path(data_dir)
        self.reports_dir = self.data_dir / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def _load_account(self) -> Optional[dict]:
        """Load paper trading account data."""
        account_file = self.data_dir / "account.json"
        if not account_file.exists():
            return None
        with open(account_file) as f:
            return json.load(f)

    def generate_report(
        self,
        period: str = "weekly",
        save: bool = True
    ) -> Optional[PerformanceReport]:
        """
        Generate a performance report.

        Args:
            period: "daily", "weekly", "monthly", or "all_time"
            save: Whether to save the report to disk

        Returns:
            PerformanceReport or None if no data
        """
        account = self._load_account()
        if not account:
            return None

        now = datetime.utcnow()

        # Determine date range
        if period == "daily":
            start_date = now - timedelta(days=1)
        elif period == "weekly":
            start_date = now - timedelta(weeks=1)
        elif period == "monthly":
            start_date = now - timedelta(days=30)
        else:  # all_time
            start_date = datetime.min

        # Filter closed positions by date
        closed_positions = []
        for p in account.get("closed_positions", []):
            exit_time = p.get("exit_time")
            if exit_time:
                exit_dt = datetime.fromisoformat(exit_time)
                if exit_dt >= start_date:
                    closed_positions.append(p)

        # Calculate metrics
        total_trades = len(closed_positions)
        winning_trades = sum(1 for p in closed_positions if p.get("pnl", 0) > 0)
        losing_trades = sum(1 for p in closed_positions if p.get("pnl", 0) < 0)

        period_pnl = sum(p.get("pnl", 0) for p in closed_positions)

        # Best/worst trades
        if closed_positions:
            best = max(closed_positions, key=lambda p: p.get("pnl", 0))
            worst = min(closed_positions, key=lambda p: p.get("pnl", 0))
            best_pnl = best.get("pnl", 0)
            best_market = best.get("market_title", "Unknown")
            worst_pnl = worst.get("pnl", 0)
            worst_market = worst.get("market_title", "Unknown")
        else:
            best_pnl = worst_pnl = 0
            best_market = worst_market = "N/A"

        # Calculate unrealized PnL from open positions
        open_positions = account.get("positions", [])
        unrealized = 0
        for p in open_positions:
            current = p.get("current_price", 0)
            entry = p.get("entry_price", 0)
            qty = p.get("quantity", 0)
            side = p.get("side", "YES")
            if current > 0:
                if side == "YES":
                    unrealized += (current - entry) / 100 * qty
                else:
                    unrealized += (entry - current) / 100 * qty

        # Account values
        initial = account.get("initial_balance", 1000)
        current_balance = account.get("balance", initial)

        # For period starting balance, estimate from current minus period PnL
        starting_balance = current_balance - period_pnl if period != "all_time" else initial

        # Win rate
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        # ROI
        roi = (period_pnl / starting_balance * 100) if starting_balance > 0 else 0

        report = PerformanceReport(
            period=period,
            start_date=start_date if start_date != datetime.min else datetime.utcnow() - timedelta(days=365),
            end_date=now,
            starting_balance=starting_balance,
            ending_balance=current_balance,
            net_pnl=period_pnl,
            roi_percent=roi,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            open_positions=len(open_positions),
            unrealized_pnl=unrealized,
            best_trade_pnl=best_pnl,
            best_trade_market=best_market,
            worst_trade_pnl=worst_pnl,
            worst_trade_market=worst_market,
            avg_edge_taken=0,  # Would need to track this in positions
            avg_kelly_used=0,
        )

        if save:
            self._save_report(report)

        return report

    def _save_report(self, report: PerformanceReport):
        """Save report to disk."""
        filename = f"{report.period}_{report.end_date.strftime('%Y%m%d_%H%M%S')}.json"
        filepath = self.reports_dir / filename
        with open(filepath, "w") as f:
            json.dump(report.to_dict(), f, indent=2)

    def get_report_history(self, period: str = "weekly", limit: int = 10) -> list[dict]:
        """Get historical reports for a period type."""
        reports = []
        for filepath in sorted(self.reports_dir.glob(f"{period}_*.json"), reverse=True)[:limit]:
            with open(filepath) as f:
                reports.append(json.load(f))
        return reports


def generate_email_summary(report: PerformanceReport) -> str:
    """Generate an email-friendly summary."""
    emoji_status = "🟢" if report.net_pnl >= 0 else "🔴"

    return f"""
{emoji_status} Base Rate Arb - {report.period.title()} Summary

Period: {report.start_date.strftime('%b %d')} - {report.end_date.strftime('%b %d, %Y')}

💰 P&L: ${report.net_pnl:+.2f} ({report.roi_percent:+.1f}%)
📊 Trades: {report.total_trades} ({report.win_rate:.0f}% win rate)
📈 Balance: ${report.ending_balance:.2f}

Open Positions: {report.open_positions}
Unrealized P&L: ${report.unrealized_pnl:+.2f}

---
Generated by baserate-arb
"""
