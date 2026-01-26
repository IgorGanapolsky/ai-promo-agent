"""
Wash sale detection and adjustment.

Implements IRS wash sale rules to detect violations and
automatically adjust cost basis on replacement securities.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Dict, Tuple
from collections import defaultdict
import logging

from .models import (
    TaxLot,
    Trade,
    WashSale,
    AssetClass,
    OrderSide,
)
from .config import WashSaleConfig
from .cost_basis import CostBasisTracker

logger = logging.getLogger(__name__)


class WashSaleDetector:
    """
    Detects and processes wash sales per IRS rules.

    A wash sale occurs when you sell a security at a loss and
    purchase a substantially identical security within 30 days
    before or after the sale.

    Key rules:
    - 30-day window before AND after the sale
    - Loss is disallowed and added to basis of replacement
    - Holding period of original shares carries over
    - Section 1256 contracts are EXEMPT from wash sales
    """

    def __init__(
        self,
        config: Optional[WashSaleConfig] = None,
        cost_basis_tracker: Optional[CostBasisTracker] = None,
    ):
        """
        Initialize the wash sale detector.

        Args:
            config: Wash sale configuration
            cost_basis_tracker: Cost basis tracker for basis adjustments
        """
        self.config = config or WashSaleConfig()
        self.cost_basis_tracker = cost_basis_tracker or CostBasisTracker()

        # Track all trades for wash sale detection
        self._trades: List[Trade] = []

        # Recorded wash sales
        self._wash_sales: List[WashSale] = []

        # Pending disallowed losses awaiting replacement
        self._pending_losses: Dict[str, List[Dict]] = defaultdict(list)

    def add_trade(self, trade: Trade) -> Optional[WashSale]:
        """
        Add a trade and check for wash sale implications.

        Args:
            trade: The trade to process

        Returns:
            WashSale if one was triggered, None otherwise
        """
        self._trades.append(trade)

        # Section 1256 contracts are exempt
        if trade.asset_class == AssetClass.INDEX_OPTION:
            logger.debug(
                f"Trade {trade.id[:8]} is Section 1256 - exempt from wash sale"
            )
            return None

        # Check if this is a sale at a loss
        if self._is_loss_sale(trade):
            return self._process_loss_sale(trade)

        # Check if this is a purchase that triggers pending wash sale
        if self._is_purchase(trade):
            return self._process_purchase(trade)

        return None

    def _is_loss_sale(self, trade: Trade) -> bool:
        """Check if trade is a sale that realized a loss."""
        if trade.side not in (
            OrderSide.SELL,
            OrderSide.SELL_TO_CLOSE,
        ):
            return False

        return trade.realized_gain_loss < 0

    def _is_purchase(self, trade: Trade) -> bool:
        """Check if trade is a purchase."""
        return trade.side in (
            OrderSide.BUY,
            OrderSide.BUY_TO_OPEN,
            OrderSide.BUY_TO_CLOSE,
        )

    def _process_loss_sale(self, trade: Trade) -> Optional[WashSale]:
        """
        Process a sale at a loss for wash sale detection.

        Checks 30 days before and after for substantially identical purchases.
        """
        symbol = trade.symbol
        underlying = trade.underlying_symbol or symbol
        sale_date = trade.trade_date
        loss_amount = abs(trade.realized_gain_loss)

        # Define wash sale window
        window_start = sale_date - timedelta(days=self.config.wash_sale_window_days)
        window_end = sale_date + timedelta(days=self.config.wash_sale_window_days)

        # Find substantially identical purchases in window
        replacement = self._find_replacement_purchase(
            underlying=underlying,
            symbol=symbol,
            window_start=window_start,
            window_end=window_end,
            exclude_trade_id=trade.id,
        )

        if replacement:
            # Wash sale triggered!
            wash_sale = self._create_wash_sale(
                sale_trade=trade,
                replacement_trade=replacement,
                disallowed_loss=loss_amount,
            )

            # Adjust basis of replacement lot
            if self.config.auto_adjust_basis:
                self._adjust_replacement_basis(wash_sale)

            return wash_sale

        # No replacement found yet - store pending loss
        # (may be triggered by future purchase within 30 days)
        self._pending_losses[underlying].append({
            "trade": trade,
            "loss_amount": loss_amount,
            "expires": window_end,
        })

        logger.info(
            f"Loss sale {trade.id[:8]} for ${loss_amount:.2f} - "
            f"no wash sale yet, monitoring until {window_end.date()}"
        )

        return None

    def _process_purchase(self, trade: Trade) -> Optional[WashSale]:
        """
        Process a purchase to check if it triggers pending wash sales.
        """
        symbol = trade.symbol
        underlying = trade.underlying_symbol or symbol
        purchase_date = trade.trade_date

        # Check for pending losses on this underlying
        pending = self._pending_losses.get(underlying, [])
        if not pending:
            return None

        # Filter to unexpired pending losses
        active_pending = [
            p for p in pending
            if p["expires"] >= purchase_date
        ]

        if not active_pending:
            return None

        # Take the oldest pending loss (FIFO)
        pending_loss = active_pending[0]
        loss_trade = pending_loss["trade"]
        loss_amount = pending_loss["loss_amount"]

        # Create wash sale
        wash_sale = self._create_wash_sale(
            sale_trade=loss_trade,
            replacement_trade=trade,
            disallowed_loss=loss_amount,
        )

        # Adjust basis of replacement lot
        if self.config.auto_adjust_basis:
            self._adjust_replacement_basis(wash_sale)

        # Remove from pending
        self._pending_losses[underlying].remove(pending_loss)

        return wash_sale

    def _find_replacement_purchase(
        self,
        underlying: str,
        symbol: str,
        window_start: datetime,
        window_end: datetime,
        exclude_trade_id: str,
    ) -> Optional[Trade]:
        """
        Find a substantially identical purchase within the wash sale window.

        Args:
            underlying: The underlying symbol
            symbol: The exact symbol sold
            window_start: Start of 61-day window
            window_end: End of 61-day window
            exclude_trade_id: Trade ID to exclude (the sale itself)

        Returns:
            Replacement trade if found
        """
        for trade in self._trades:
            if trade.id == exclude_trade_id:
                continue

            if not self._is_purchase(trade):
                continue

            # Check if in window
            if not (window_start <= trade.trade_date <= window_end):
                continue

            # Check if substantially identical
            if self._is_substantially_identical(underlying, symbol, trade):
                return trade

        return None

    def _is_substantially_identical(
        self,
        underlying: str,
        sold_symbol: str,
        purchased_trade: Trade,
    ) -> bool:
        """
        Determine if purchased security is substantially identical to sold.

        IRS considers these substantially identical:
        - Same stock or security
        - Options on the same underlying
        - Deep ITM calls (may be treated as stock)
        """
        purchase_underlying = purchased_trade.underlying_symbol or purchased_trade.symbol

        # Same underlying
        if purchase_underlying.upper() == underlying.upper():
            return True

        # Same exact symbol
        if purchased_trade.symbol.upper() == sold_symbol.upper():
            return True

        # Options on same stock as stock itself
        if self.config.treat_options_as_identical_to_stock:
            if purchase_underlying.upper() == underlying.upper():
                return True

        # Deep ITM calls treated as stock
        if self.config.treat_deep_itm_calls_as_stock:
            # Would need current price to calculate ITM %
            # Simplified: any call option on same underlying
            if (
                purchased_trade.option_type
                and purchase_underlying.upper() == underlying.upper()
            ):
                return True

        return False

    def _create_wash_sale(
        self,
        sale_trade: Trade,
        replacement_trade: Trade,
        disallowed_loss: Decimal,
    ) -> WashSale:
        """
        Create a wash sale record.
        """
        wash_sale = WashSale(
            sale_trade_id=sale_trade.id,
            sale_symbol=sale_trade.symbol,
            sale_date=sale_trade.trade_date,
            sale_quantity=sale_trade.quantity,
            disallowed_loss=disallowed_loss,
            replacement_trade_id=replacement_trade.id,
            replacement_date=replacement_trade.trade_date,
            replacement_quantity=replacement_trade.quantity,
            basis_adjustment=disallowed_loss,
            original_acquisition_date=sale_trade.acquisition_date,
        )

        self._wash_sales.append(wash_sale)

        logger.warning(
            f"WASH SALE DETECTED: Sold {sale_trade.symbol} at "
            f"${disallowed_loss:.2f} loss, replaced within 30 days. "
            f"Loss disallowed, basis adjusted on replacement."
        )

        return wash_sale

    def _adjust_replacement_basis(self, wash_sale: WashSale) -> None:
        """
        Adjust the cost basis of the replacement lot.

        The disallowed loss is added to the basis of the replacement shares.
        """
        # Find the replacement lot
        # This is a simplified approach - in practice would need to match
        # the specific lot created from the replacement trade

        result = self.cost_basis_tracker.adjust_lot_basis(
            lot_id=wash_sale.adjusted_tax_lot_id,
            adjustment=wash_sale.disallowed_loss,
            reason="wash_sale",
        )

        if result:
            wash_sale.is_resolved = True
            logger.info(
                f"Wash sale resolved: Basis adjusted by ${wash_sale.disallowed_loss:.2f}"
            )

    def check_potential_wash_sale(
        self,
        symbol: str,
        sale_date: datetime,
    ) -> Dict[str, any]:
        """
        Check if a potential sale would trigger a wash sale.

        Useful for tax-loss harvesting to avoid wash sales.

        Args:
            symbol: Symbol to potentially sell
            sale_date: Planned sale date

        Returns:
            Dictionary with wash sale risk assessment
        """
        underlying = symbol  # Simplified

        window_start = sale_date - timedelta(days=self.config.wash_sale_window_days)
        window_end = sale_date + timedelta(days=self.config.wash_sale_window_days)

        # Check for recent purchases
        recent_purchases = []
        for trade in self._trades:
            if not self._is_purchase(trade):
                continue

            trade_underlying = trade.underlying_symbol or trade.symbol
            if trade_underlying.upper() != underlying.upper():
                continue

            if window_start <= trade.trade_date <= sale_date:
                recent_purchases.append(trade)

        # Check current positions (potential future purchases)
        current_lots = self.cost_basis_tracker.get_lots(symbol)
        recent_lots = [
            lot for lot in current_lots
            if window_start <= lot.acquisition_date <= sale_date
        ]

        risk_level = "none"
        if recent_purchases or recent_lots:
            risk_level = "high"

        lockout_until = sale_date + timedelta(days=self.config.wash_sale_window_days + 1)

        return {
            "symbol": symbol,
            "risk_level": risk_level,
            "recent_purchases": len(recent_purchases),
            "recent_lots": len(recent_lots),
            "wash_sale_window_start": window_start,
            "wash_sale_window_end": window_end,
            "safe_to_repurchase_after": lockout_until,
            "recommendation": (
                "Wait 31 days after sale before repurchasing"
                if risk_level == "none"
                else "CAUTION: Recent purchases may trigger wash sale"
            ),
        }

    def get_wash_sales(
        self,
        tax_year: Optional[int] = None,
        symbol: Optional[str] = None,
    ) -> List[WashSale]:
        """
        Get recorded wash sales.

        Args:
            tax_year: Filter to specific tax year
            symbol: Filter to specific symbol

        Returns:
            List of WashSale records
        """
        results = self._wash_sales

        if tax_year:
            results = [
                ws for ws in results
                if ws.sale_date.year == tax_year
            ]

        if symbol:
            results = [
                ws for ws in results
                if ws.sale_symbol.upper() == symbol.upper()
            ]

        return results

    def get_total_disallowed_losses(
        self,
        tax_year: Optional[int] = None,
    ) -> Decimal:
        """
        Get total disallowed losses from wash sales.

        Args:
            tax_year: Filter to specific tax year

        Returns:
            Total disallowed loss amount
        """
        wash_sales = self.get_wash_sales(tax_year=tax_year)
        return sum(ws.disallowed_loss for ws in wash_sales)

    def export_wash_sales(self) -> List[Dict]:
        """
        Export wash sales for reporting.

        Returns:
            List of wash sale dictionaries
        """
        return [
            {
                "id": ws.id,
                "sale_trade_id": ws.sale_trade_id,
                "sale_symbol": ws.sale_symbol,
                "sale_date": ws.sale_date.isoformat(),
                "sale_quantity": str(ws.sale_quantity),
                "disallowed_loss": str(ws.disallowed_loss),
                "replacement_trade_id": ws.replacement_trade_id,
                "replacement_date": ws.replacement_date.isoformat(),
                "replacement_quantity": str(ws.replacement_quantity),
                "basis_adjustment": str(ws.basis_adjustment),
                "is_resolved": ws.is_resolved,
            }
            for ws in self._wash_sales
        ]
