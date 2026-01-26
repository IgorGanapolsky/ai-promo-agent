"""
Daily Revenue Tracker

Tracks trading revenue from Alpaca and other sources.
Integrates with Vertex AI RAG for lesson logging.
"""

import os
import json
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Optional
import requests


@dataclass
class Trade:
    symbol: str
    side: str  # 'buy' or 'sell'
    quantity: float
    price: float
    timestamp: str
    pnl: float = 0.0
    order_id: str = ""


@dataclass
class DailyRevenue:
    date: str
    total_pnl: float
    realized_pnl: float
    unrealized_pnl: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    largest_win: float
    largest_loss: float
    trades: list


class AlpacaClient:
    """Client for Alpaca Markets API."""

    def __init__(self):
        self.api_key = os.environ.get("ALPACA_API_KEY")
        self.api_secret = os.environ.get("ALPACA_API_SECRET")
        self.base_url = os.environ.get(
            "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
        )

        if not self.api_key or not self.api_secret:
            raise ValueError(
                "ALPACA_API_KEY and ALPACA_API_SECRET environment variables required"
            )

    def _headers(self):
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }

    def get_account(self) -> dict:
        """Get account information including equity and P&L."""
        response = requests.get(
            f"{self.base_url}/v2/account", headers=self._headers(), timeout=30
        )
        response.raise_for_status()
        return response.json()

    def get_portfolio_history(self, period: str = "1D") -> dict:
        """Get portfolio history for P&L calculation."""
        response = requests.get(
            f"{self.base_url}/v2/account/portfolio/history",
            headers=self._headers(),
            params={"period": period, "timeframe": "1D"},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def get_orders(
        self, status: str = "closed", after: Optional[str] = None
    ) -> list:
        """Get orders for trade history."""
        params = {"status": status, "limit": 500}
        if after:
            params["after"] = after

        response = requests.get(
            f"{self.base_url}/v2/orders",
            headers=self._headers(),
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def get_positions(self) -> list:
        """Get current positions with unrealized P&L."""
        response = requests.get(
            f"{self.base_url}/v2/positions", headers=self._headers(), timeout=30
        )
        response.raise_for_status()
        return response.json()


class RevenueTracker:
    """Tracks and reports daily trading revenue."""

    def __init__(self, client: Optional[AlpacaClient] = None):
        self.client = client

    def get_daily_revenue(self, date: Optional[str] = None) -> DailyRevenue:
        """
        Calculate daily revenue for a given date.

        Args:
            date: Date string in YYYY-MM-DD format. Defaults to today.

        Returns:
            DailyRevenue object with all metrics.
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        if self.client is None:
            return self._get_mock_revenue(date)

        # Get account info
        account = self.client.get_account()

        # Get today's orders
        start_of_day = f"{date}T00:00:00Z"
        orders = self.client.get_orders(status="closed", after=start_of_day)

        # Get current positions for unrealized P&L
        positions = self.client.get_positions()

        # Calculate metrics
        trades = []
        realized_pnl = 0.0
        winning_trades = 0
        losing_trades = 0
        largest_win = 0.0
        largest_loss = 0.0

        for order in orders:
            if order.get("filled_at", "").startswith(date):
                trade = Trade(
                    symbol=order["symbol"],
                    side=order["side"],
                    quantity=float(order.get("filled_qty", 0)),
                    price=float(order.get("filled_avg_price", 0)),
                    timestamp=order.get("filled_at", ""),
                    order_id=order["id"],
                )
                trades.append(trade)

        # Calculate unrealized P&L from positions
        unrealized_pnl = sum(
            float(pos.get("unrealized_pl", 0)) for pos in positions
        )

        # Get realized P&L from portfolio history
        try:
            history = self.client.get_portfolio_history(period="1D")
            if history.get("profit_loss"):
                realized_pnl = history["profit_loss"][-1] if history["profit_loss"] else 0
        except Exception:
            realized_pnl = 0.0

        total_pnl = realized_pnl + unrealized_pnl

        return DailyRevenue(
            date=date,
            total_pnl=total_pnl,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            total_trades=len(trades),
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            largest_win=largest_win,
            largest_loss=largest_loss,
            trades=[asdict(t) for t in trades],
        )

    def _get_mock_revenue(self, date: str) -> DailyRevenue:
        """Return mock data when no client is configured."""
        return DailyRevenue(
            date=date,
            total_pnl=0.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            largest_win=0.0,
            largest_loss=0.0,
            trades=[],
        )

    def format_report(self, revenue: DailyRevenue) -> str:
        """Format revenue data as a human-readable report."""
        pnl_emoji = "+" if revenue.total_pnl >= 0 else ""
        status = "PROFIT" if revenue.total_pnl >= 0 else "LOSS"

        report = f"""
=====================================
  DAILY REVENUE REPORT - {revenue.date}
=====================================

Status: {status}

PROFIT & LOSS
-------------
Total P&L:      {pnl_emoji}${revenue.total_pnl:,.2f}
Realized P&L:   {pnl_emoji}${revenue.realized_pnl:,.2f}
Unrealized P&L: {pnl_emoji}${revenue.unrealized_pnl:,.2f}

TRADE STATISTICS
----------------
Total Trades:   {revenue.total_trades}
Winning Trades: {revenue.winning_trades}
Losing Trades:  {revenue.losing_trades}
Win Rate:       {(revenue.winning_trades / revenue.total_trades * 100) if revenue.total_trades > 0 else 0:.1f}%

EXTREMES
--------
Largest Win:    ${revenue.largest_win:,.2f}
Largest Loss:   ${revenue.largest_loss:,.2f}

=====================================
"""
        return report

    def to_json(self, revenue: DailyRevenue) -> str:
        """Export revenue data as JSON."""
        return json.dumps(asdict(revenue), indent=2)


def main():
    """Main entry point for daily revenue tracking."""
    print("Daily Revenue Tracker")
    print("=" * 40)

    try:
        client = AlpacaClient()
        tracker = RevenueTracker(client)
        print("Connected to Alpaca API")
    except ValueError as e:
        print(f"Warning: {e}")
        print("Running in demo mode with no live data")
        tracker = RevenueTracker()

    # Get today's revenue
    revenue = tracker.get_daily_revenue()

    # Print report
    print(tracker.format_report(revenue))

    # Output JSON for CI/CD integration
    print("\nJSON Output:")
    print(tracker.to_json(revenue))

    return revenue


if __name__ == "__main__":
    main()
