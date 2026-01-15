"""Live trading execution for Kalshi (with safety checks)."""

import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import json
from pathlib import Path

# Note: This requires the official Kalshi Python client
# pip install kalshi-python


@dataclass
class TradeConfig:
    """Configuration for live trading."""
    max_position_size: int = 100  # Max contracts per position
    max_total_exposure: float = 500  # Max total $ at risk
    min_edge: float = 0.05  # 5% minimum edge
    min_kelly: float = 0.02  # 2% minimum Kelly
    max_kelly: float = 0.25  # 25% max Kelly (quarter Kelly)
    min_liquidity: int = 500  # Minimum contracts available
    cooldown_seconds: int = 60  # Time between trades
    dry_run: bool = True  # If True, don't execute real trades


class KalshiLiveTrader:
    """
    Live trading on Kalshi with safety controls.

    IMPORTANT: This executes REAL trades with REAL money.
    Use dry_run=True to test without executing.

    Requirements:
    1. Kalshi account with API access
    2. KALSHI_API_KEY and KALSHI_API_SECRET in .env
    3. Funded account

    Usage:
        trader = KalshiLiveTrader(dry_run=True)  # Start in dry run
        trader.execute_opportunity(opportunity)

        # When ready for real trading:
        trader = KalshiLiveTrader(dry_run=False)
    """

    def __init__(
        self,
        config: Optional[TradeConfig] = None,
        dry_run: bool = True,
        log_dir: str = "data/trade_logs"
    ):
        self.config = config or TradeConfig()
        self.config.dry_run = dry_run
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.last_trade_time = 0
        self._client = None

        # Load API credentials
        self.api_key = os.getenv("KALSHI_API_KEY")
        self.api_secret = os.getenv("KALSHI_API_SECRET")

        if not dry_run and (not self.api_key or not self.api_secret):
            raise ValueError(
                "KALSHI_API_KEY and KALSHI_API_SECRET required for live trading. "
                "Set dry_run=True to test without credentials."
            )

    def _get_client(self):
        """Get or create Kalshi API client."""
        if self._client is None:
            try:
                from kalshi_python.client import Client
                self._client = Client(
                    api_key=self.api_key,
                    api_secret=self.api_secret
                )
            except ImportError:
                raise ImportError(
                    "kalshi-python not installed. Run: pip install kalshi-python"
                )
        return self._client

    def get_balance(self) -> dict:
        """Get current account balance."""
        if self.config.dry_run:
            return {"balance": "DRY RUN - No real balance", "available": "N/A"}

        try:
            client = self._get_client()
            balance = client.get_balance()
            return {
                "balance": f"${balance.get('balance', 0) / 100:.2f}",
                "available": f"${balance.get('available_balance', 0) / 100:.2f}"
            }
        except Exception as e:
            return {"error": str(e)}

    def get_positions(self) -> list[dict]:
        """Get current positions."""
        if self.config.dry_run:
            return [{"note": "DRY RUN - No real positions"}]

        try:
            client = self._get_client()
            positions = client.get_positions()
            return positions.get("market_positions", [])
        except Exception as e:
            return [{"error": str(e)}]

    def _log_trade(self, trade_data: dict):
        """Log trade to file."""
        log_file = self.log_dir / f"trades_{datetime.now().strftime('%Y%m%d')}.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(trade_data) + "\n")

    def _validate_opportunity(self, opp) -> tuple[bool, str]:
        """Validate an opportunity before trading."""
        # Check minimum edge
        if opp.edge < self.config.min_edge:
            return False, f"Edge {opp.edge:.1%} below minimum {self.config.min_edge:.1%}"

        # Check Kelly
        if opp.kelly_fraction < self.config.min_kelly:
            return False, f"Kelly {opp.kelly_fraction:.1%} below minimum {self.config.min_kelly:.1%}"

        # Check liquidity
        if opp.available_quantity < self.config.min_liquidity:
            return False, f"Liquidity {opp.available_quantity} below minimum {self.config.min_liquidity}"

        # Check cooldown
        time_since_last = time.time() - self.last_trade_time
        if time_since_last < self.config.cooldown_seconds:
            remaining = self.config.cooldown_seconds - time_since_last
            return False, f"Cooldown: {remaining:.0f}s remaining"

        return True, "Validated"

    def calculate_position_size(self, opp) -> int:
        """Calculate position size based on Kelly and limits."""
        # Use fractional Kelly (quarter Kelly by default)
        kelly = min(opp.kelly_fraction, self.config.max_kelly)

        # Calculate based on a theoretical bankroll
        # In practice, use your actual available balance
        theoretical_bankroll = self.config.max_total_exposure * 4
        position_value = theoretical_bankroll * kelly

        # Convert to contracts (price is in cents)
        contracts = int(position_value / (opp.recommended_price / 100))

        # Apply limits
        contracts = min(contracts, self.config.max_position_size)
        contracts = min(contracts, opp.available_quantity)

        return max(1, contracts)

    def execute_opportunity(self, opp) -> dict:
        """
        Execute a trade for an opportunity.

        Args:
            opp: OpportunityAnalysis object

        Returns:
            Trade result dictionary
        """
        result = {
            "timestamp": datetime.utcnow().isoformat(),
            "market_id": opp.market.id,
            "market_title": opp.market.title,
            "side": opp.side,
            "dry_run": self.config.dry_run
        }

        # Validate
        valid, message = self._validate_opportunity(opp)
        if not valid:
            result["status"] = "rejected"
            result["reason"] = message
            self._log_trade(result)
            return result

        # Calculate size
        quantity = self.calculate_position_size(opp)
        price = int(opp.recommended_price)  # Kalshi uses integer cents

        result["quantity"] = quantity
        result["price"] = price
        result["edge"] = opp.edge
        result["kelly"] = opp.kelly_fraction
        result["ev"] = opp.expected_value

        if self.config.dry_run:
            result["status"] = "dry_run"
            result["message"] = f"Would place: {opp.side} {quantity} @ {price}¢"
            self._log_trade(result)
            return result

        # Execute real trade
        try:
            client = self._get_client()

            # Place limit order
            order = client.create_order(
                ticker=opp.market.id,
                side="yes" if opp.side == "YES" else "no",
                action="buy",
                count=quantity,
                type="limit",
                yes_price=price if opp.side == "YES" else None,
                no_price=price if opp.side == "NO" else None
            )

            result["status"] = "executed"
            result["order_id"] = order.get("order", {}).get("order_id")
            result["message"] = f"Order placed: {opp.side} {quantity} @ {price}¢"

            self.last_trade_time = time.time()

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)

        self._log_trade(result)
        return result

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order."""
        if self.config.dry_run:
            return {"status": "dry_run", "message": f"Would cancel order {order_id}"}

        try:
            client = self._get_client()
            client.cancel_order(order_id)
            return {"status": "cancelled", "order_id": order_id}
        except Exception as e:
            return {"status": "error", "error": str(e)}


class PolymarketLiveTrader:
    """
    Live trading on Polymarket (crypto-based).

    IMPORTANT: Polymarket requires:
    1. Crypto wallet (MetaMask, etc.)
    2. USDC on Polygon network
    3. py_clob_client library

    This is more complex than Kalshi due to crypto requirements.
    """

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        # Polymarket trading requires more complex setup
        # including wallet connection and signing transactions
        raise NotImplementedError(
            "Polymarket live trading requires crypto wallet integration. "
            "Use paper trading to test strategies, then manually execute "
            "trades on polymarket.com when ready."
        )
