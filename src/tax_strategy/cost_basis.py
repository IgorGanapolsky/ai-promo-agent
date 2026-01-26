"""
Cost basis tracking and tax lot management.

Implements FIFO, LIFO, and Specific ID cost basis methods
for accurate tax lot matching and gain/loss calculation.
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict, Tuple
from collections import defaultdict
import logging

from .models import (
    TaxLot,
    Trade,
    AssetClass,
    CostBasisMethod,
    OrderSide,
    GainType,
)
from .config import CostBasisConfig

logger = logging.getLogger(__name__)


class CostBasisTracker:
    """
    Tracks cost basis using tax lots with multiple accounting methods.

    Supports FIFO (First In, First Out), LIFO (Last In, First Out),
    and Specific Identification methods for tax lot matching.
    """

    def __init__(self, config: Optional[CostBasisConfig] = None):
        """
        Initialize the cost basis tracker.

        Args:
            config: Cost basis configuration. Uses defaults if None.
        """
        self.config = config or CostBasisConfig()

        # Tax lots organized by symbol
        self._tax_lots: Dict[str, List[TaxLot]] = defaultdict(list)

        # Closed lots for historical tracking
        self._closed_lots: Dict[str, List[TaxLot]] = defaultdict(list)

        # Trade history for wash sale lookback
        self._trade_history: List[Trade] = []

    def add_lot(self, lot: TaxLot) -> TaxLot:
        """
        Add a new tax lot from a purchase.

        Args:
            lot: The tax lot to add

        Returns:
            The added tax lot with any modifications
        """
        # Calculate adjusted cost basis including fees if configured
        lot.adjusted_cost_basis = lot.total_cost + lot.wash_sale_adjustment

        self._tax_lots[lot.symbol].append(lot)
        logger.info(
            f"Added tax lot {lot.id[:8]} for {lot.symbol}: "
            f"{lot.quantity} @ ${lot.cost_per_unit}"
        )
        return lot

    def create_lot_from_trade(self, trade: Trade) -> TaxLot:
        """
        Create a tax lot from a buy trade.

        Args:
            trade: The purchase trade

        Returns:
            New TaxLot created from the trade
        """
        # Calculate total cost including commissions/fees
        total_cost = trade.quantity * trade.price
        if self.config.include_commission_in_basis:
            total_cost += trade.commission
        if self.config.include_fees_in_basis:
            total_cost += trade.fees

        lot = TaxLot(
            symbol=trade.symbol,
            asset_class=trade.asset_class,
            quantity=trade.quantity,
            remaining_quantity=trade.quantity,
            cost_per_unit=trade.price,
            total_cost=total_cost,
            option_type=trade.option_type,
            strike_price=trade.strike_price,
            expiration_date=trade.expiration_date,
            underlying_symbol=trade.underlying_symbol,
            acquisition_date=trade.trade_date,
            order_id=trade.order_id,
        )

        return self.add_lot(lot)

    def sell(
        self,
        symbol: str,
        quantity: Decimal,
        sale_price: Decimal,
        sale_date: datetime,
        method: Optional[CostBasisMethod] = None,
        specific_lot_ids: Optional[List[str]] = None,
        commission: Decimal = Decimal("0"),
        fees: Decimal = Decimal("0"),
    ) -> Tuple[Decimal, List[Dict]]:
        """
        Process a sale and match against tax lots.

        Args:
            symbol: Symbol being sold
            quantity: Quantity to sell
            sale_price: Price per unit
            sale_date: Date of sale
            method: Cost basis method (defaults to config default)
            specific_lot_ids: Specific lot IDs if using specific ID method
            commission: Commission paid
            fees: Other fees

        Returns:
            Tuple of (total_cost_basis, list of lot matches with gains)
        """
        method = method or CostBasisMethod(self.config.default_method)

        # Get available lots
        available_lots = [
            lot for lot in self._tax_lots[symbol]
            if lot.remaining_quantity > 0
        ]

        if not available_lots:
            logger.warning(f"No tax lots available for {symbol}")
            return Decimal("0"), []

        # Sort lots based on method
        if method == CostBasisMethod.FIFO:
            available_lots.sort(key=lambda x: x.acquisition_date)
        elif method == CostBasisMethod.LIFO:
            available_lots.sort(key=lambda x: x.acquisition_date, reverse=True)
        elif method == CostBasisMethod.SPECIFIC_ID and specific_lot_ids:
            # Filter to specific lots
            available_lots = [
                lot for lot in available_lots
                if lot.id in specific_lot_ids
            ]

        # Match lots against sale quantity
        remaining_to_sell = quantity
        total_cost_basis = Decimal("0")
        lot_matches = []

        for lot in available_lots:
            if remaining_to_sell <= 0:
                break

            # Determine quantity from this lot
            qty_from_lot = min(lot.remaining_quantity, remaining_to_sell)

            # Calculate proportional cost basis
            proportion = qty_from_lot / lot.quantity
            lot_cost_basis = lot.adjusted_cost_basis * proportion

            # Calculate proceeds
            proceeds = qty_from_lot * sale_price

            # For options, apply contract multiplier
            if lot.asset_class in (AssetClass.EQUITY_OPTION, AssetClass.INDEX_OPTION):
                proceeds *= lot.contract_multiplier

            # Net proceeds after fees (proportional)
            fee_proportion = qty_from_lot / quantity
            net_proceeds = proceeds - (commission + fees) * fee_proportion

            # Calculate gain/loss
            gain_loss = net_proceeds - lot_cost_basis

            # Determine gain type
            days_held = (sale_date - lot.acquisition_date).days
            if lot.asset_class == AssetClass.INDEX_OPTION:
                gain_type = GainType.SECTION_1256
            elif days_held > 365:
                gain_type = GainType.LONG_TERM
            else:
                gain_type = GainType.SHORT_TERM

            # Record the match
            lot_matches.append({
                "lot_id": lot.id,
                "quantity": qty_from_lot,
                "cost_basis": lot_cost_basis,
                "proceeds": net_proceeds,
                "gain_loss": gain_loss,
                "gain_type": gain_type,
                "acquisition_date": lot.acquisition_date,
                "sale_date": sale_date,
                "days_held": days_held,
            })

            # Update lot
            lot.remaining_quantity -= qty_from_lot
            if lot.remaining_quantity == 0:
                lot.is_closed = True
                lot.closed_date = sale_date
                # Move to closed lots
                self._closed_lots[symbol].append(lot)

            total_cost_basis += lot_cost_basis
            remaining_to_sell -= qty_from_lot

        # Remove fully closed lots from active list
        self._tax_lots[symbol] = [
            lot for lot in self._tax_lots[symbol]
            if not lot.is_closed
        ]

        logger.info(
            f"Sold {quantity} {symbol}: Cost basis ${total_cost_basis:.2f}, "
            f"matched {len(lot_matches)} lots"
        )

        return total_cost_basis, lot_matches

    def get_lots(
        self,
        symbol: Optional[str] = None,
        include_closed: bool = False,
    ) -> List[TaxLot]:
        """
        Get tax lots, optionally filtered by symbol.

        Args:
            symbol: Filter to specific symbol
            include_closed: Include closed lots

        Returns:
            List of tax lots
        """
        if symbol:
            lots = list(self._tax_lots[symbol])
            if include_closed:
                lots.extend(self._closed_lots[symbol])
            return lots

        # All lots
        all_lots = []
        for symbol_lots in self._tax_lots.values():
            all_lots.extend(symbol_lots)

        if include_closed:
            for symbol_lots in self._closed_lots.values():
                all_lots.extend(symbol_lots)

        return all_lots

    def get_position_cost_basis(self, symbol: str) -> Dict[str, Decimal]:
        """
        Get total cost basis and average cost for a position.

        Args:
            symbol: The symbol to query

        Returns:
            Dictionary with total_cost_basis, total_quantity, average_cost
        """
        lots = self.get_lots(symbol)

        total_cost = Decimal("0")
        total_qty = Decimal("0")

        for lot in lots:
            if lot.remaining_quantity > 0:
                proportion = lot.remaining_quantity / lot.quantity
                total_cost += lot.adjusted_cost_basis * proportion
                total_qty += lot.remaining_quantity

        average = total_cost / total_qty if total_qty > 0 else Decimal("0")

        return {
            "total_cost_basis": total_cost,
            "total_quantity": total_qty,
            "average_cost": average,
        }

    def adjust_lot_basis(
        self,
        lot_id: str,
        adjustment: Decimal,
        reason: str = "wash_sale",
    ) -> Optional[TaxLot]:
        """
        Adjust the cost basis of a specific lot.

        Used for wash sale basis adjustments.

        Args:
            lot_id: ID of the lot to adjust
            adjustment: Amount to add to cost basis
            reason: Reason for adjustment

        Returns:
            The adjusted lot, or None if not found
        """
        for symbol_lots in self._tax_lots.values():
            for lot in symbol_lots:
                if lot.id == lot_id:
                    lot.wash_sale_adjustment += adjustment
                    lot.adjusted_cost_basis = (
                        lot.total_cost + lot.wash_sale_adjustment
                    )
                    logger.info(
                        f"Adjusted lot {lot_id[:8]} basis by ${adjustment:.2f} "
                        f"({reason}). New basis: ${lot.adjusted_cost_basis:.2f}"
                    )
                    return lot

        return None

    def get_unrealized_gains(
        self,
        current_prices: Dict[str, Decimal],
    ) -> Dict[str, Dict]:
        """
        Calculate unrealized gains/losses for all positions.

        Args:
            current_prices: Dictionary of symbol -> current price

        Returns:
            Dictionary of symbol -> unrealized gain details
        """
        results = {}

        for symbol, lots in self._tax_lots.items():
            if symbol not in current_prices:
                continue

            current_price = current_prices[symbol]
            total_cost = Decimal("0")
            total_qty = Decimal("0")
            short_term_gain = Decimal("0")
            long_term_gain = Decimal("0")

            for lot in lots:
                if lot.remaining_quantity <= 0:
                    continue

                proportion = lot.remaining_quantity / lot.quantity
                lot_cost = lot.adjusted_cost_basis * proportion
                lot_value = lot.remaining_quantity * current_price

                # For options, apply multiplier
                if lot.asset_class in (
                    AssetClass.EQUITY_OPTION,
                    AssetClass.INDEX_OPTION,
                ):
                    lot_value *= lot.contract_multiplier

                unrealized = lot_value - lot_cost

                if lot.is_long_term:
                    long_term_gain += unrealized
                else:
                    short_term_gain += unrealized

                total_cost += lot_cost
                total_qty += lot.remaining_quantity

            total_value = total_qty * current_price
            results[symbol] = {
                "quantity": total_qty,
                "cost_basis": total_cost,
                "market_value": total_value,
                "unrealized_gain": total_value - total_cost,
                "unrealized_short_term": short_term_gain,
                "unrealized_long_term": long_term_gain,
            }

        return results

    def export_lots(self) -> List[Dict]:
        """
        Export all lots for persistence or reporting.

        Returns:
            List of lot dictionaries
        """
        all_lots = self.get_lots(include_closed=True)
        return [
            {
                "id": lot.id,
                "symbol": lot.symbol,
                "asset_class": lot.asset_class.value,
                "quantity": str(lot.quantity),
                "remaining_quantity": str(lot.remaining_quantity),
                "cost_per_unit": str(lot.cost_per_unit),
                "total_cost": str(lot.total_cost),
                "adjusted_cost_basis": str(lot.adjusted_cost_basis),
                "wash_sale_adjustment": str(lot.wash_sale_adjustment),
                "acquisition_date": lot.acquisition_date.isoformat(),
                "is_closed": lot.is_closed,
                "closed_date": (
                    lot.closed_date.isoformat() if lot.closed_date else None
                ),
                "option_type": lot.option_type.value if lot.option_type else None,
                "strike_price": str(lot.strike_price) if lot.strike_price else None,
                "expiration_date": (
                    lot.expiration_date.isoformat() if lot.expiration_date else None
                ),
            }
            for lot in all_lots
        ]

    def import_lots(self, lot_data: List[Dict]) -> int:
        """
        Import lots from persisted data.

        Args:
            lot_data: List of lot dictionaries

        Returns:
            Number of lots imported
        """
        from .models import OptionType

        count = 0
        for data in lot_data:
            lot = TaxLot(
                id=data["id"],
                symbol=data["symbol"],
                asset_class=AssetClass(data["asset_class"]),
                quantity=Decimal(data["quantity"]),
                remaining_quantity=Decimal(data["remaining_quantity"]),
                cost_per_unit=Decimal(data["cost_per_unit"]),
                total_cost=Decimal(data["total_cost"]),
                adjusted_cost_basis=Decimal(data["adjusted_cost_basis"]),
                wash_sale_adjustment=Decimal(data["wash_sale_adjustment"]),
                acquisition_date=datetime.fromisoformat(data["acquisition_date"]),
                is_closed=data["is_closed"],
                closed_date=(
                    datetime.fromisoformat(data["closed_date"])
                    if data["closed_date"]
                    else None
                ),
                option_type=(
                    OptionType(data["option_type"]) if data["option_type"] else None
                ),
                strike_price=(
                    Decimal(data["strike_price"]) if data["strike_price"] else None
                ),
                expiration_date=(
                    datetime.fromisoformat(data["expiration_date"]).date()
                    if data["expiration_date"]
                    else None
                ),
            )

            if lot.is_closed:
                self._closed_lots[lot.symbol].append(lot)
            else:
                self._tax_lots[lot.symbol].append(lot)

            count += 1

        logger.info(f"Imported {count} tax lots")
        return count
