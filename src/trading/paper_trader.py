"""Paper trading simulator for testing strategies without real money."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import random


@dataclass
class PaperPosition:
    """A simulated position."""
    market_id: str
    market_title: str
    platform: str
    side: str  # "YES" or "NO"
    entry_price: float  # In cents
    quantity: int
    entry_time: datetime
    target_price: float  # Fair value estimate
    current_price: float = 0
    status: str = "open"  # open, closed, resolved
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    pnl: float = 0
    resolution: Optional[str] = None  # "win", "lose" for resolved markets

    def to_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "market_title": self.market_title,
            "platform": self.platform,
            "side": self.side,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "entry_time": self.entry_time.isoformat(),
            "target_price": self.target_price,
            "current_price": self.current_price,
            "status": self.status,
            "exit_price": self.exit_price,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "pnl": self.pnl,
            "resolution": self.resolution
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PaperPosition":
        return cls(
            market_id=data["market_id"],
            market_title=data["market_title"],
            platform=data["platform"],
            side=data["side"],
            entry_price=data["entry_price"],
            quantity=data["quantity"],
            entry_time=datetime.fromisoformat(data["entry_time"]),
            target_price=data["target_price"],
            current_price=data.get("current_price", 0),
            status=data.get("status", "open"),
            exit_price=data.get("exit_price"),
            exit_time=datetime.fromisoformat(data["exit_time"]) if data.get("exit_time") else None,
            pnl=data.get("pnl", 0),
            resolution=data.get("resolution")
        )


@dataclass
class PaperAccount:
    """Simulated trading account."""
    initial_balance: float = 1000.0  # Starting balance in dollars
    balance: float = 1000.0
    positions: list[PaperPosition] = field(default_factory=list)
    closed_positions: list[PaperPosition] = field(default_factory=list)
    total_trades: int = 0
    winning_trades: int = 0
    total_pnl: float = 0

    def available_balance(self) -> float:
        """Balance minus open position costs."""
        open_cost = sum(
            (p.entry_price / 100) * p.quantity
            for p in self.positions
        )
        return self.balance - open_cost

    def unrealized_pnl(self) -> float:
        """PnL from open positions at current prices."""
        pnl = 0
        for p in self.positions:
            if p.current_price > 0:
                if p.side == "YES":
                    pnl += (p.current_price - p.entry_price) / 100 * p.quantity
                else:
                    pnl += (p.entry_price - p.current_price) / 100 * p.quantity
        return pnl

    def total_value(self) -> float:
        """Total account value including unrealized PnL."""
        return self.balance + self.unrealized_pnl()

    def win_rate(self) -> float:
        """Percentage of winning trades."""
        if self.total_trades == 0:
            return 0
        return self.winning_trades / self.total_trades * 100

    def roi(self) -> float:
        """Return on investment percentage."""
        return (self.total_value() - self.initial_balance) / self.initial_balance * 100


class PaperTrader:
    """
    Paper trading engine for backtesting and forward testing strategies.

    Usage:
        trader = PaperTrader(initial_balance=1000)

        # Open a position
        trader.open_position(
            market_id="abc123",
            market_title="Will X happen?",
            platform="kalshi",
            side="YES",
            price=35,  # 35 cents
            quantity=100,
            fair_value=45  # Our estimate
        )

        # Update prices
        trader.update_price("abc123", 40)

        # Check status
        print(trader.get_summary())

        # Simulate resolution
        trader.resolve_market("abc123", outcome="YES")
    """

    def __init__(
        self,
        initial_balance: float = 1000.0,
        data_dir: str = "data/paper_trading"
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.account_file = self.data_dir / "account.json"

        # Load existing account or create new
        if self.account_file.exists():
            self.account = self._load_account()
        else:
            self.account = PaperAccount(
                initial_balance=initial_balance,
                balance=initial_balance
            )
            self._save_account()

    def _load_account(self) -> PaperAccount:
        """Load account from disk."""
        with open(self.account_file) as f:
            data = json.load(f)

        account = PaperAccount(
            initial_balance=data["initial_balance"],
            balance=data["balance"],
            total_trades=data["total_trades"],
            winning_trades=data["winning_trades"],
            total_pnl=data["total_pnl"]
        )
        account.positions = [
            PaperPosition.from_dict(p) for p in data.get("positions", [])
        ]
        account.closed_positions = [
            PaperPosition.from_dict(p) for p in data.get("closed_positions", [])
        ]
        return account

    def _save_account(self):
        """Save account to disk."""
        data = {
            "initial_balance": self.account.initial_balance,
            "balance": self.account.balance,
            "total_trades": self.account.total_trades,
            "winning_trades": self.account.winning_trades,
            "total_pnl": self.account.total_pnl,
            "positions": [p.to_dict() for p in self.account.positions],
            "closed_positions": [p.to_dict() for p in self.account.closed_positions]
        }
        with open(self.account_file, "w") as f:
            json.dump(data, f, indent=2)

    def reset_account(self, initial_balance: float = 1000.0):
        """Reset the paper trading account."""
        self.account = PaperAccount(
            initial_balance=initial_balance,
            balance=initial_balance
        )
        self._save_account()

    def open_position(
        self,
        market_id: str,
        market_title: str,
        platform: str,
        side: str,
        price: float,
        quantity: int,
        fair_value: float
    ) -> tuple[bool, str]:
        """
        Open a new paper position.

        Args:
            market_id: Unique market identifier
            market_title: Human-readable title
            platform: "kalshi" or "polymarket"
            side: "YES" or "NO"
            price: Entry price in cents (1-99)
            quantity: Number of contracts
            fair_value: Our estimated fair value in cents

        Returns:
            (success, message)
        """
        # Check if already have position in this market
        for p in self.account.positions:
            if p.market_id == market_id:
                return False, f"Already have position in {market_id}"

        # Check available balance
        cost = (price / 100) * quantity
        if cost > self.account.available_balance():
            return False, f"Insufficient balance. Need ${cost:.2f}, have ${self.account.available_balance():.2f}"

        # Create position
        position = PaperPosition(
            market_id=market_id,
            market_title=market_title,
            platform=platform,
            side=side,
            entry_price=price,
            quantity=quantity,
            entry_time=datetime.utcnow(),
            target_price=fair_value,
            current_price=price
        )

        self.account.positions.append(position)
        self._save_account()

        edge = fair_value - price if side == "YES" else price - fair_value
        return True, f"Opened {side} position: {quantity} contracts @ {price}¢ (edge: {edge:.1f}¢)"

    def close_position(
        self,
        market_id: str,
        exit_price: float
    ) -> tuple[bool, str]:
        """
        Close a position at current market price.

        Args:
            market_id: Market to close
            exit_price: Current market price in cents

        Returns:
            (success, message)
        """
        position = None
        for i, p in enumerate(self.account.positions):
            if p.market_id == market_id:
                position = self.account.positions.pop(i)
                break

        if not position:
            return False, f"No open position for {market_id}"

        # Calculate PnL
        if position.side == "YES":
            pnl = (exit_price - position.entry_price) / 100 * position.quantity
        else:
            pnl = (position.entry_price - exit_price) / 100 * position.quantity

        position.exit_price = exit_price
        position.exit_time = datetime.utcnow()
        position.status = "closed"
        position.pnl = pnl

        # Update account
        self.account.balance += pnl
        self.account.total_trades += 1
        self.account.total_pnl += pnl
        if pnl > 0:
            self.account.winning_trades += 1

        self.account.closed_positions.append(position)
        self._save_account()

        return True, f"Closed position: PnL ${pnl:.2f}"

    def resolve_market(
        self,
        market_id: str,
        outcome: str  # "YES" or "NO"
    ) -> tuple[bool, str]:
        """
        Resolve a market and calculate final PnL.

        Args:
            market_id: Market to resolve
            outcome: "YES" or "NO"

        Returns:
            (success, message)
        """
        position = None
        for i, p in enumerate(self.account.positions):
            if p.market_id == market_id:
                position = self.account.positions.pop(i)
                break

        if not position:
            return False, f"No open position for {market_id}"

        # Calculate PnL based on resolution
        # Winner gets $1 per contract, loser gets $0
        won = (position.side == outcome)

        if won:
            # We get $1 per contract, paid entry price
            pnl = (100 - position.entry_price) / 100 * position.quantity
            position.resolution = "win"
        else:
            # We get $0, lose entry price
            pnl = -position.entry_price / 100 * position.quantity
            position.resolution = "lose"

        position.exit_price = 100 if won else 0
        position.exit_time = datetime.utcnow()
        position.status = "resolved"
        position.pnl = pnl

        # Update account
        self.account.balance += pnl
        self.account.total_trades += 1
        self.account.total_pnl += pnl
        if pnl > 0:
            self.account.winning_trades += 1

        self.account.closed_positions.append(position)
        self._save_account()

        result = "WON" if won else "LOST"
        return True, f"Market resolved {outcome}. Position {result}: PnL ${pnl:.2f}"

    def update_price(self, market_id: str, current_price: float):
        """Update current price for a position."""
        for p in self.account.positions:
            if p.market_id == market_id:
                p.current_price = current_price
        self._save_account()

    def update_all_prices(self, prices: dict[str, float]):
        """Update prices for multiple markets."""
        for p in self.account.positions:
            if p.market_id in prices:
                p.current_price = prices[p.market_id]
        self._save_account()

    def get_open_positions(self) -> list[dict]:
        """Get all open positions with current PnL."""
        positions = []
        for p in self.account.positions:
            if p.current_price > 0:
                if p.side == "YES":
                    unrealized = (p.current_price - p.entry_price) / 100 * p.quantity
                else:
                    unrealized = (p.entry_price - p.current_price) / 100 * p.quantity
            else:
                unrealized = 0

            positions.append({
                "market_id": p.market_id,
                "title": p.market_title[:50] + "..." if len(p.market_title) > 50 else p.market_title,
                "platform": p.platform,
                "side": p.side,
                "entry": f"{p.entry_price:.0f}¢",
                "current": f"{p.current_price:.0f}¢" if p.current_price else "N/A",
                "target": f"{p.target_price:.0f}¢",
                "qty": p.quantity,
                "cost": f"${p.entry_price / 100 * p.quantity:.2f}",
                "unrealized_pnl": f"${unrealized:+.2f}",
                "edge": f"{p.target_price - p.entry_price:+.0f}¢" if p.side == "YES" else f"{p.entry_price - p.target_price:+.0f}¢"
            })
        return positions

    def get_closed_positions(self, limit: int = 20) -> list[dict]:
        """Get recent closed positions."""
        positions = []
        for p in sorted(self.account.closed_positions, key=lambda x: x.exit_time or datetime.min, reverse=True)[:limit]:
            positions.append({
                "market_id": p.market_id,
                "title": p.market_title[:40] + "..." if len(p.market_title) > 40 else p.market_title,
                "side": p.side,
                "entry": f"{p.entry_price:.0f}¢",
                "exit": f"{p.exit_price:.0f}¢" if p.exit_price else "N/A",
                "qty": p.quantity,
                "pnl": f"${p.pnl:+.2f}",
                "result": p.resolution or "closed",
                "date": p.exit_time.strftime("%Y-%m-%d") if p.exit_time else "N/A"
            })
        return positions

    def get_summary(self) -> dict:
        """Get account summary."""
        return {
            "initial_balance": f"${self.account.initial_balance:.2f}",
            "current_balance": f"${self.account.balance:.2f}",
            "unrealized_pnl": f"${self.account.unrealized_pnl():+.2f}",
            "total_value": f"${self.account.total_value():.2f}",
            "total_pnl": f"${self.account.total_pnl:+.2f}",
            "roi": f"{self.account.roi():+.1f}%",
            "total_trades": self.account.total_trades,
            "winning_trades": self.account.winning_trades,
            "win_rate": f"{self.account.win_rate():.1f}%",
            "open_positions": len(self.account.positions),
            "available_balance": f"${self.account.available_balance():.2f}"
        }

    def simulate_from_opportunities(
        self,
        opportunities: list,
        max_positions: int = 10,
        position_size: int = 50,  # Contracts per position
        min_edge: float = 0.05
    ) -> list[str]:
        """
        Automatically open positions from opportunity analysis.

        Args:
            opportunities: List of OpportunityAnalysis objects
            max_positions: Maximum number of positions to open
            position_size: Default position size in contracts
            min_edge: Minimum edge to take position

        Returns:
            List of messages about actions taken
        """
        messages = []

        for opp in opportunities:
            if len(self.account.positions) >= max_positions:
                messages.append(f"Max positions ({max_positions}) reached")
                break

            if opp.edge < min_edge:
                continue

            # Check if already have position
            already_have = any(
                p.market_id == opp.market.id
                for p in self.account.positions
            )
            if already_have:
                continue

            # Determine position size based on Kelly
            kelly_size = int(position_size * min(opp.kelly_fraction, 0.25) * 4)
            qty = max(10, kelly_size)

            # Get entry price
            price = opp.recommended_price

            success, msg = self.open_position(
                market_id=opp.market.id,
                market_title=opp.market.title,
                platform=opp.market.platform.value,
                side=opp.side,
                price=price,
                quantity=qty,
                fair_value=opp.fair_probability * 100
            )
            messages.append(msg)

        return messages
