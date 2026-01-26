"""
Alpaca API client for tax data synchronization.

Fetches trade history, positions, and account activities
from Alpaca Markets for tax calculation purposes.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Dict, Any
import logging

from .models import (
    Trade,
    TaxLot,
    Position,
    AssetClass,
    OptionType,
    OrderSide,
    PositionSide,
)
from .config import AlpacaConfig

logger = logging.getLogger(__name__)


class AlpacaTaxClient:
    """
    Client for fetching tax-relevant data from Alpaca Markets API.

    Handles authentication, data fetching, and conversion to
    internal tax models.
    """

    def __init__(self, config: Optional[AlpacaConfig] = None):
        """
        Initialize the Alpaca client.

        Args:
            config: Alpaca configuration. If None, loads from environment.
        """
        self.config = config or AlpacaConfig()
        self._client = None
        self._trading_client = None
        self._data_client = None

    def _get_trading_client(self):
        """Lazy initialization of trading client."""
        if self._trading_client is None:
            try:
                from alpaca.trading.client import TradingClient

                self._trading_client = TradingClient(
                    api_key=self.config.api_key,
                    secret_key=self.config.api_secret,
                    paper=self.config.paper_trading,
                )
            except ImportError:
                raise ImportError(
                    "alpaca-py is required. Install with: pip install alpaca-py"
                )
        return self._trading_client

    def _get_options_data_client(self):
        """Lazy initialization of options data client."""
        if self._data_client is None:
            try:
                from alpaca.data.historical.option import OptionHistoricalDataClient

                self._data_client = OptionHistoricalDataClient(
                    api_key=self.config.api_key,
                    secret_key=self.config.api_secret,
                )
            except ImportError:
                raise ImportError(
                    "alpaca-py is required. Install with: pip install alpaca-py"
                )
        return self._data_client

    def get_account(self) -> Dict[str, Any]:
        """
        Fetch account information.

        Returns:
            Account details including equity, buying power, etc.
        """
        client = self._get_trading_client()
        account = client.get_account()
        return {
            "account_number": account.account_number,
            "equity": Decimal(str(account.equity)),
            "cash": Decimal(str(account.cash)),
            "buying_power": Decimal(str(account.buying_power)),
            "portfolio_value": Decimal(str(account.portfolio_value)),
            "status": account.status,
        }

    def get_positions(self) -> List[Position]:
        """
        Fetch all current positions.

        Returns:
            List of Position objects with current holdings.
        """
        client = self._get_trading_client()
        alpaca_positions = client.get_all_positions()

        positions = []
        for ap in alpaca_positions:
            # Determine asset class
            asset_class = self._determine_asset_class(ap.symbol, ap.asset_class)

            # Parse option details if applicable
            option_type = None
            strike_price = None
            expiration_date = None
            underlying = None

            if asset_class in (AssetClass.EQUITY_OPTION, AssetClass.INDEX_OPTION):
                option_details = self._parse_option_symbol(ap.symbol)
                if option_details:
                    option_type = option_details.get("option_type")
                    strike_price = option_details.get("strike")
                    expiration_date = option_details.get("expiration")
                    underlying = option_details.get("underlying")

            position = Position(
                symbol=ap.symbol,
                asset_class=asset_class,
                side=PositionSide.LONG if Decimal(str(ap.qty)) > 0 else PositionSide.SHORT,
                quantity=abs(Decimal(str(ap.qty))),
                average_cost=Decimal(str(ap.avg_entry_price)),
                total_cost_basis=Decimal(str(ap.cost_basis)),
                current_price=Decimal(str(ap.current_price)),
                market_value=Decimal(str(ap.market_value)),
                unrealized_gain_loss=Decimal(str(ap.unrealized_pl)),
                unrealized_gain_loss_pct=Decimal(str(ap.unrealized_plpc)) * 100,
                option_type=option_type,
                strike_price=strike_price,
                expiration_date=expiration_date,
                underlying_symbol=underlying,
            )
            positions.append(position)

        return positions

    def get_orders(
        self,
        status: str = "closed",
        after: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """
        Fetch orders from Alpaca.

        Args:
            status: Order status filter (open, closed, all)
            after: Start date for filtering
            until: End date for filtering
            limit: Maximum number of orders to return

        Returns:
            List of order dictionaries.
        """
        client = self._get_trading_client()

        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        status_map = {
            "open": QueryOrderStatus.OPEN,
            "closed": QueryOrderStatus.CLOSED,
            "all": QueryOrderStatus.ALL,
        }

        request = GetOrdersRequest(
            status=status_map.get(status, QueryOrderStatus.CLOSED),
            after=after,
            until=until,
            limit=limit,
        )

        orders = client.get_orders(filter=request)

        return [
            {
                "id": str(order.id),
                "symbol": order.symbol,
                "side": order.side.value,
                "qty": Decimal(str(order.qty)) if order.qty else Decimal("0"),
                "filled_qty": (
                    Decimal(str(order.filled_qty)) if order.filled_qty else Decimal("0")
                ),
                "filled_avg_price": (
                    Decimal(str(order.filled_avg_price))
                    if order.filled_avg_price
                    else Decimal("0")
                ),
                "order_type": order.order_type.value if order.order_type else None,
                "status": order.status.value if order.status else None,
                "created_at": order.created_at,
                "filled_at": order.filled_at,
                "asset_class": order.asset_class.value if order.asset_class else None,
            }
            for order in orders
        ]

    def get_trade_activities(
        self,
        after: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[Trade]:
        """
        Fetch trade activities (fills) for tax reporting.

        Args:
            after: Start date
            until: End date

        Returns:
            List of Trade objects representing completed trades.
        """
        client = self._get_trading_client()

        from alpaca.trading.requests import GetAccountActivitiesRequest
        from alpaca.trading.enums import ActivityType

        # Default to last year if not specified
        if after is None:
            after = datetime.now() - timedelta(days=365)

        request = GetAccountActivitiesRequest(
            activity_types=[ActivityType.FILL],
            after=after,
            until=until,
        )

        activities = client.get_account_activities(filter=request)
        trades = []

        for activity in activities:
            # Determine asset class and option details
            asset_class = self._determine_asset_class(
                activity.symbol,
                getattr(activity, "asset_class", None),
            )

            option_type = None
            strike_price = None
            expiration_date = None
            underlying = None

            if asset_class in (AssetClass.EQUITY_OPTION, AssetClass.INDEX_OPTION):
                option_details = self._parse_option_symbol(activity.symbol)
                if option_details:
                    option_type = option_details.get("option_type")
                    strike_price = option_details.get("strike")
                    expiration_date = option_details.get("expiration")
                    underlying = option_details.get("underlying")

            # Determine order side
            side = self._map_order_side(activity.side)

            # Calculate proceeds
            qty = Decimal(str(activity.qty))
            price = Decimal(str(activity.price))
            proceeds = qty * price

            # For options, multiply by contract multiplier
            if asset_class in (AssetClass.EQUITY_OPTION, AssetClass.INDEX_OPTION):
                proceeds *= 100  # Standard option contract = 100 shares

            trade = Trade(
                id=str(activity.id),
                symbol=activity.symbol,
                asset_class=asset_class,
                side=side,
                quantity=qty,
                price=price,
                proceeds=proceeds,
                trade_date=activity.transaction_time,
                order_id=str(activity.order_id) if activity.order_id else None,
                option_type=option_type,
                strike_price=strike_price,
                expiration_date=expiration_date,
                underlying_symbol=underlying,
            )
            trades.append(trade)

        return trades

    def get_option_contracts(
        self,
        underlying_symbol: str,
        expiration_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch available option contracts for an underlying.

        Args:
            underlying_symbol: The underlying stock symbol
            expiration_date: Optional expiration date filter (YYYY-MM-DD)

        Returns:
            List of option contract details.
        """
        client = self._get_trading_client()

        from alpaca.trading.requests import GetOptionContractsRequest

        request = GetOptionContractsRequest(
            underlying_symbols=[underlying_symbol],
            expiration_date=expiration_date,
        )

        contracts = client.get_option_contracts(request)

        return [
            {
                "symbol": c.symbol,
                "underlying": c.underlying_symbol,
                "expiration": c.expiration_date,
                "strike": Decimal(str(c.strike_price)),
                "option_type": c.type.value,
                "style": c.style.value if c.style else None,
            }
            for c in contracts
        ]

    def _determine_asset_class(
        self,
        symbol: str,
        alpaca_asset_class: Optional[str] = None,
    ) -> AssetClass:
        """
        Determine the tax asset class for a symbol.

        Args:
            symbol: The trading symbol
            alpaca_asset_class: Asset class from Alpaca API

        Returns:
            AssetClass enum value
        """
        # Check if it's an option based on symbol format
        if self._is_option_symbol(symbol):
            # Check if it's an index option (Section 1256)
            underlying = self._parse_option_symbol(symbol).get("underlying", "")
            if self._is_index_option(underlying):
                return AssetClass.INDEX_OPTION
            return AssetClass.EQUITY_OPTION

        if alpaca_asset_class == "crypto":
            return AssetClass.CRYPTO

        return AssetClass.EQUITY

    def _is_option_symbol(self, symbol: str) -> bool:
        """Check if symbol is an option contract."""
        # OCC option symbol format: underlying + YYMMDD + C/P + strike
        # Example: AAPL240119C00150000
        return len(symbol) > 10 and (symbol[-9] in ("C", "P"))

    def _is_index_option(self, underlying: str) -> bool:
        """
        Check if underlying qualifies for Section 1256 treatment.

        Broad-based index options like SPX, NDX, RUT qualify.
        ETF options like SPY, QQQ do NOT qualify.
        """
        section_1256_indices = {
            "SPX",  # S&P 500 Index
            "NDX",  # Nasdaq-100 Index
            "RUT",  # Russell 2000 Index
            "DJX",  # Dow Jones Index
            "VIX",  # CBOE Volatility Index
            "OEX",  # S&P 100 Index
            "XEO",  # S&P 100 European
        }
        return underlying.upper() in section_1256_indices

    def _parse_option_symbol(self, symbol: str) -> Dict[str, Any]:
        """
        Parse OCC option symbol format.

        Format: ROOT + YYMMDD + C/P + STRIKE (8 digits, price * 1000)
        Example: AAPL240119C00150000 = AAPL $150 Call exp 01/19/2024

        Returns:
            Dictionary with underlying, expiration, option_type, strike
        """
        if not self._is_option_symbol(symbol):
            return {}

        try:
            # Find where the date portion starts (6 digits before C/P)
            cp_index = None
            for i in range(len(symbol) - 9, -1, -1):
                if symbol[i + 6] in ("C", "P"):
                    cp_index = i + 6
                    break

            if cp_index is None:
                return {}

            underlying = symbol[: cp_index - 6]
            date_str = symbol[cp_index - 6: cp_index]
            option_type_char = symbol[cp_index]
            strike_str = symbol[cp_index + 1:]

            # Parse expiration date
            year = 2000 + int(date_str[:2])
            month = int(date_str[2:4])
            day = int(date_str[4:6])
            from datetime import date
            expiration = date(year, month, day)

            # Parse strike (8 digits, divide by 1000)
            strike = Decimal(strike_str) / 1000

            return {
                "underlying": underlying,
                "expiration": expiration,
                "option_type": (
                    OptionType.CALL if option_type_char == "C" else OptionType.PUT
                ),
                "strike": strike,
            }
        except (ValueError, IndexError):
            return {}

    def _map_order_side(self, alpaca_side: str) -> OrderSide:
        """Map Alpaca order side to internal OrderSide enum."""
        side_map = {
            "buy": OrderSide.BUY,
            "sell": OrderSide.SELL,
            "buy_to_open": OrderSide.BUY_TO_OPEN,
            "buy_to_close": OrderSide.BUY_TO_CLOSE,
            "sell_to_open": OrderSide.SELL_TO_OPEN,
            "sell_to_close": OrderSide.SELL_TO_CLOSE,
        }
        return side_map.get(alpaca_side.lower(), OrderSide.BUY)

    def sync_all(
        self,
        start_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Synchronize all tax-relevant data from Alpaca.

        Args:
            start_date: Start date for historical data fetch

        Returns:
            Dictionary with positions, trades, and account info
        """
        if start_date is None:
            start_date = datetime.now() - timedelta(
                days=self.config.historical_days_to_fetch
            )

        logger.info(f"Syncing Alpaca data from {start_date}")

        account = self.get_account()
        positions = self.get_positions()
        trades = self.get_trade_activities(after=start_date)

        return {
            "account": account,
            "positions": positions,
            "trades": trades,
            "sync_time": datetime.now(),
            "trade_count": len(trades),
            "position_count": len(positions),
        }
