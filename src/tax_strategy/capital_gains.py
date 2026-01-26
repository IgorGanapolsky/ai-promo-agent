"""
Capital gains calculation and classification.

Calculates realized capital gains and losses, classifies them
as short-term or long-term, and prepares data for tax reporting.
"""

from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional, Dict, Tuple
from collections import defaultdict
import logging

from .models import (
    Trade,
    TaxLot,
    CapitalGain,
    TaxSummary,
    GainType,
    AssetClass,
    OrderSide,
)
from .config import TaxConfig, TaxRates
from .cost_basis import CostBasisTracker

logger = logging.getLogger(__name__)


class CapitalGainsCalculator:
    """
    Calculates and classifies capital gains and losses.

    Handles:
    - Short-term vs long-term classification
    - Wash sale adjustments
    - Section 1256 60/40 treatment
    - Form 8949 categorization
    - Tax liability estimation
    """

    def __init__(
        self,
        config: Optional[TaxConfig] = None,
        cost_basis_tracker: Optional[CostBasisTracker] = None,
    ):
        """
        Initialize the capital gains calculator.

        Args:
            config: Tax configuration
            cost_basis_tracker: Cost basis tracker
        """
        self.config = config or TaxConfig()
        self.cost_basis_tracker = cost_basis_tracker or CostBasisTracker()

        # Processed trades
        self._capital_gains: List[CapitalGain] = []

        # Running totals by year
        self._yearly_totals: Dict[int, Dict[str, Decimal]] = defaultdict(
            lambda: {
                "short_term_gains": Decimal("0"),
                "short_term_losses": Decimal("0"),
                "long_term_gains": Decimal("0"),
                "long_term_losses": Decimal("0"),
                "section_1256_gains": Decimal("0"),
                "section_1256_losses": Decimal("0"),
                "wash_sale_disallowed": Decimal("0"),
            }
        )

    def process_trade(
        self,
        trade: Trade,
        lot_matches: Optional[List[Dict]] = None,
    ) -> List[CapitalGain]:
        """
        Process a sale trade and create capital gain records.

        Args:
            trade: The sale trade to process
            lot_matches: Pre-calculated lot matches from cost basis tracker

        Returns:
            List of CapitalGain records created
        """
        if trade.side not in (OrderSide.SELL, OrderSide.SELL_TO_CLOSE):
            return []

        gains = []
        tax_year = trade.trade_date.year

        # If lot matches not provided, this is a simplified calculation
        if lot_matches is None:
            gain = self._create_simple_capital_gain(trade)
            gains.append(gain)
            self._update_totals(gain, tax_year)
        else:
            # Create a capital gain record for each lot match
            for match in lot_matches:
                gain = self._create_capital_gain_from_match(trade, match)
                gains.append(gain)
                self._update_totals(gain, tax_year)

        self._capital_gains.extend(gains)
        return gains

    def _create_simple_capital_gain(self, trade: Trade) -> CapitalGain:
        """
        Create a capital gain from trade with pre-calculated values.
        """
        # Determine Form 8949 box
        box = self._determine_form_8949_box(
            gain_type=trade.gain_type,
            basis_reported=self.config.reporting.form_8949_basis_reported_to_irs,
        )

        # Adjustment for wash sales
        adjustment_code = "W" if trade.is_wash_sale else ""
        adjustment_amount = trade.wash_sale_disallowed if trade.is_wash_sale else Decimal("0")

        return CapitalGain(
            description=self._format_description(trade),
            date_acquired=trade.acquisition_date.date() if trade.acquisition_date else None,
            date_sold=trade.trade_date.date(),
            proceeds=trade.proceeds,
            cost_basis=trade.cost_basis,
            adjustment_code=adjustment_code,
            adjustment_amount=adjustment_amount,
            gain_or_loss=trade.realized_gain_loss,
            gain_type=trade.gain_type,
            form_8949_box=box,
            trade_id=trade.id,
        )

    def _create_capital_gain_from_match(
        self,
        trade: Trade,
        match: Dict,
    ) -> CapitalGain:
        """
        Create a capital gain from a lot match.
        """
        gain_type = match.get("gain_type", GainType.SHORT_TERM)

        box = self._determine_form_8949_box(
            gain_type=gain_type,
            basis_reported=self.config.reporting.form_8949_basis_reported_to_irs,
        )

        return CapitalGain(
            description=self._format_description(trade, match.get("quantity")),
            date_acquired=match.get("acquisition_date", datetime.now()).date(),
            date_sold=match.get("sale_date", trade.trade_date).date(),
            proceeds=match.get("proceeds", Decimal("0")),
            cost_basis=match.get("cost_basis", Decimal("0")),
            adjustment_code="",
            adjustment_amount=Decimal("0"),
            gain_or_loss=match.get("gain_loss", Decimal("0")),
            gain_type=gain_type,
            form_8949_box=box,
            trade_id=trade.id,
            tax_lot_id=match.get("lot_id", ""),
        )

    def _format_description(
        self,
        trade: Trade,
        quantity: Optional[Decimal] = None,
    ) -> str:
        """
        Format description for Form 8949.

        Example: "100 SH AAPL" or "1 AAPL 01/19/24 C 150"
        """
        qty = quantity or trade.quantity

        if trade.asset_class in (AssetClass.EQUITY_OPTION, AssetClass.INDEX_OPTION):
            # Option format
            exp = trade.expiration_date.strftime("%m/%d/%y") if trade.expiration_date else "UNK"
            opt_type = "C" if trade.option_type and trade.option_type.value == "call" else "P"
            strike = trade.strike_price or Decimal("0")
            underlying = trade.underlying_symbol or trade.symbol[:4]
            return f"{qty} {underlying} {exp} {opt_type} {strike}"
        else:
            # Stock format
            return f"{qty} SH {trade.symbol}"

    def _determine_form_8949_box(
        self,
        gain_type: GainType,
        basis_reported: bool,
    ) -> str:
        """
        Determine the Form 8949 box for reporting.

        Short-term:
        - Box A: Basis reported to IRS
        - Box B: Basis NOT reported to IRS
        - Box C: No Form 1099-B

        Long-term:
        - Box D: Basis reported to IRS
        - Box E: Basis NOT reported to IRS
        - Box F: No Form 1099-B
        """
        if gain_type == GainType.SECTION_1256:
            # Section 1256 goes on Form 6781, not 8949
            return "6781"

        if gain_type == GainType.SHORT_TERM:
            return "A" if basis_reported else "B"
        else:  # LONG_TERM
            return "D" if basis_reported else "E"

    def _update_totals(self, gain: CapitalGain, tax_year: int) -> None:
        """
        Update running totals for a tax year.
        """
        totals = self._yearly_totals[tax_year]
        amount = gain.gain_or_loss

        if gain.gain_type == GainType.SHORT_TERM:
            if amount >= 0:
                totals["short_term_gains"] += amount
            else:
                totals["short_term_losses"] += abs(amount)
        elif gain.gain_type == GainType.LONG_TERM:
            if amount >= 0:
                totals["long_term_gains"] += amount
            else:
                totals["long_term_losses"] += abs(amount)
        elif gain.gain_type == GainType.SECTION_1256:
            if amount >= 0:
                totals["section_1256_gains"] += amount
            else:
                totals["section_1256_losses"] += abs(amount)

        if gain.adjustment_code == "W":
            totals["wash_sale_disallowed"] += gain.adjustment_amount

    def get_tax_summary(
        self,
        tax_year: Optional[int] = None,
        prior_carryforward: Decimal = Decimal("0"),
    ) -> TaxSummary:
        """
        Get comprehensive tax summary for a year.

        Args:
            tax_year: Tax year (defaults to current year)
            prior_carryforward: Loss carryforward from prior year

        Returns:
            TaxSummary with all calculations
        """
        tax_year = tax_year or datetime.now().year
        totals = self._yearly_totals[tax_year]

        # Calculate net short-term and long-term
        net_short_term = (
            totals["short_term_gains"] - totals["short_term_losses"]
        )
        net_long_term = (
            totals["long_term_gains"] - totals["long_term_losses"]
        )
        net_section_1256 = (
            totals["section_1256_gains"] - totals["section_1256_losses"]
        )

        # Apply prior carryforward
        carryforward_used = Decimal("0")
        carryforward_remaining = prior_carryforward

        # Carryforward applies to net capital gain
        total_net = net_short_term + net_long_term + net_section_1256

        if total_net > 0 and carryforward_remaining > 0:
            carryforward_used = min(total_net, carryforward_remaining)
            carryforward_remaining -= carryforward_used
            total_net -= carryforward_used

        # Calculate ordinary income deduction
        ordinary_deduction = Decimal("0")
        if total_net < 0:
            # Can deduct up to $3,000 against ordinary income
            ordinary_deduction = min(
                abs(total_net),
                self.config.tax_rates.max_ordinary_income_deduction,
            )
            # Remaining becomes carryforward
            carryforward_remaining += abs(total_net) - ordinary_deduction

        # Estimate tax liability
        estimated_st_tax = Decimal("0")
        estimated_lt_tax = Decimal("0")

        if net_short_term > 0:
            estimated_st_tax = net_short_term * self.config.get_effective_marginal_rate()

        if net_long_term > 0:
            estimated_lt_tax = net_long_term * self.config.get_effective_long_term_rate()

        # Section 1256: 60/40 split
        if net_section_1256 > 0:
            lt_portion = net_section_1256 * Decimal("0.60")
            st_portion = net_section_1256 * Decimal("0.40")
            estimated_lt_tax += lt_portion * self.config.get_effective_long_term_rate()
            estimated_st_tax += st_portion * self.config.get_effective_marginal_rate()

        return TaxSummary(
            tax_year=tax_year,
            short_term_gains=totals["short_term_gains"],
            short_term_losses=totals["short_term_losses"],
            net_short_term=net_short_term,
            long_term_gains=totals["long_term_gains"],
            long_term_losses=totals["long_term_losses"],
            net_long_term=net_long_term,
            section_1256_gains=totals["section_1256_gains"],
            section_1256_losses=totals["section_1256_losses"],
            net_section_1256=net_section_1256,
            total_gains=(
                totals["short_term_gains"]
                + totals["long_term_gains"]
                + totals["section_1256_gains"]
            ),
            total_losses=(
                totals["short_term_losses"]
                + totals["long_term_losses"]
                + totals["section_1256_losses"]
            ),
            net_capital_gain_loss=total_net,
            total_wash_sale_disallowed=totals["wash_sale_disallowed"],
            loss_carryforward_used=carryforward_used,
            loss_carryforward_remaining=carryforward_remaining,
            ordinary_income_deduction=ordinary_deduction,
            estimated_short_term_tax=estimated_st_tax,
            estimated_long_term_tax=estimated_lt_tax,
            estimated_total_tax=estimated_st_tax + estimated_lt_tax,
        )

    def get_capital_gains(
        self,
        tax_year: Optional[int] = None,
        gain_type: Optional[GainType] = None,
    ) -> List[CapitalGain]:
        """
        Get capital gains, optionally filtered.

        Args:
            tax_year: Filter to specific year
            gain_type: Filter to specific gain type

        Returns:
            List of CapitalGain records
        """
        results = self._capital_gains

        if tax_year:
            results = [
                g for g in results
                if g.date_sold and g.date_sold.year == tax_year
            ]

        if gain_type:
            results = [g for g in results if g.gain_type == gain_type]

        return results

    def get_form_8949_data(
        self,
        tax_year: Optional[int] = None,
    ) -> Dict[str, List[CapitalGain]]:
        """
        Get capital gains organized by Form 8949 box.

        Args:
            tax_year: Tax year to report

        Returns:
            Dictionary mapping box letters to capital gains
        """
        gains = self.get_capital_gains(tax_year=tax_year)

        by_box = defaultdict(list)
        for gain in gains:
            if gain.form_8949_box != "6781":  # Exclude Section 1256
                by_box[gain.form_8949_box].append(gain)

        return dict(by_box)

    def estimate_quarterly_tax(
        self,
        quarter: int,
        tax_year: Optional[int] = None,
    ) -> Dict[str, Decimal]:
        """
        Estimate quarterly tax payment for capital gains.

        Args:
            quarter: Quarter number (1-4)
            tax_year: Tax year

        Returns:
            Estimated quarterly payment details
        """
        tax_year = tax_year or datetime.now().year
        summary = self.get_tax_summary(tax_year=tax_year)

        # Quarterly estimate is roughly 1/4 of annual
        # (simplified - actual rules are more complex)
        quarterly_st = summary.estimated_short_term_tax / 4
        quarterly_lt = summary.estimated_long_term_tax / 4

        return {
            "quarter": quarter,
            "tax_year": tax_year,
            "estimated_short_term": quarterly_st,
            "estimated_long_term": quarterly_lt,
            "estimated_total": quarterly_st + quarterly_lt,
            "due_date": self._get_quarterly_due_date(quarter, tax_year),
        }

    def _get_quarterly_due_date(self, quarter: int, year: int) -> date:
        """Get quarterly estimated tax due date."""
        due_dates = {
            1: date(year, 4, 15),  # Q1: April 15
            2: date(year, 6, 15),  # Q2: June 15
            3: date(year, 9, 15),  # Q3: September 15
            4: date(year + 1, 1, 15),  # Q4: January 15 (next year)
        }
        return due_dates.get(quarter, date(year, 4, 15))

    def export_gains(self, tax_year: Optional[int] = None) -> List[Dict]:
        """
        Export capital gains for persistence or reporting.

        Args:
            tax_year: Filter to specific year

        Returns:
            List of gain dictionaries
        """
        gains = self.get_capital_gains(tax_year=tax_year)

        return [
            {
                "description": g.description,
                "date_acquired": g.date_acquired.isoformat() if g.date_acquired else None,
                "date_sold": g.date_sold.isoformat() if g.date_sold else None,
                "proceeds": str(g.proceeds),
                "cost_basis": str(g.cost_basis),
                "adjustment_code": g.adjustment_code,
                "adjustment_amount": str(g.adjustment_amount),
                "gain_or_loss": str(g.gain_or_loss),
                "gain_type": g.gain_type.value,
                "form_8949_box": g.form_8949_box,
                "trade_id": g.trade_id,
                "tax_lot_id": g.tax_lot_id,
            }
            for g in gains
        ]
