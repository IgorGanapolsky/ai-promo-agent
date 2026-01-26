"""
Tax-loss harvesting strategy implementation.

Identifies positions with unrealized losses that can be harvested
to offset capital gains and reduce tax liability.
"""

from datetime import datetime, timedelta, date
from decimal import Decimal
from typing import List, Optional, Dict
import logging

from .models import (
    Position,
    TaxLot,
    TaxHarvestOpportunity,
    GainType,
    AssetClass,
)
from .config import TaxLossHarvestingConfig, TaxConfig
from .cost_basis import CostBasisTracker
from .wash_sale import WashSaleDetector

logger = logging.getLogger(__name__)


# Common ETF replacement pairs for tax-loss harvesting
# These track similar indices but are not "substantially identical"
REPLACEMENT_PAIRS = {
    # S&P 500 ETFs
    "SPY": ["VOO", "IVV", "SPLG"],
    "VOO": ["SPY", "IVV", "SPLG"],
    "IVV": ["SPY", "VOO", "SPLG"],
    # Total Market ETFs
    "VTI": ["ITOT", "SCHB", "SPTM"],
    "ITOT": ["VTI", "SCHB", "SPTM"],
    # Nasdaq ETFs
    "QQQ": ["QQQM", "ONEQ", "VGT"],
    "QQQM": ["QQQ", "ONEQ", "VGT"],
    # International ETFs
    "VXUS": ["IXUS", "VEU", "ACWX"],
    "VEA": ["IEFA", "SCHF", "EFA"],
    # Bond ETFs
    "BND": ["AGG", "SCHZ", "IUSB"],
    "AGG": ["BND", "SCHZ", "IUSB"],
    # Small Cap ETFs
    "VB": ["IJR", "SCHA", "VIOO"],
    "IJR": ["VB", "SCHA", "VIOO"],
    # Growth ETFs
    "VUG": ["IWF", "SCHG", "SPYG"],
    "IWF": ["VUG", "SCHG", "SPYG"],
    # Value ETFs
    "VTV": ["IWD", "SCHV", "SPYV"],
    "IWD": ["VTV", "SCHV", "SPYV"],
}


class TaxLossHarvester:
    """
    Identifies and manages tax-loss harvesting opportunities.

    Tax-loss harvesting involves selling investments at a loss to
    offset capital gains taxes. Key considerations:
    - Short-term losses offset short-term gains first (best value)
    - Excess losses offset long-term gains
    - Up to $3,000 can offset ordinary income
    - Remaining losses carry forward indefinitely
    - Must avoid wash sales (30-day rule)
    """

    def __init__(
        self,
        config: Optional[TaxLossHarvestingConfig] = None,
        tax_config: Optional[TaxConfig] = None,
        cost_basis_tracker: Optional[CostBasisTracker] = None,
        wash_sale_detector: Optional[WashSaleDetector] = None,
    ):
        """
        Initialize the tax-loss harvester.

        Args:
            config: Harvesting configuration
            tax_config: Overall tax configuration
            cost_basis_tracker: Cost basis tracker
            wash_sale_detector: Wash sale detector
        """
        self.config = config or TaxLossHarvestingConfig()
        self.tax_config = tax_config or TaxConfig()
        self.cost_basis_tracker = cost_basis_tracker or CostBasisTracker()
        self.wash_sale_detector = wash_sale_detector or WashSaleDetector()

        # Track harvested positions to avoid wash sales
        self._harvested_symbols: Dict[str, date] = {}

    def scan_for_opportunities(
        self,
        positions: List[Position],
        current_prices: Optional[Dict[str, Decimal]] = None,
        realized_gains_ytd: Decimal = Decimal("0"),
    ) -> List[TaxHarvestOpportunity]:
        """
        Scan positions for tax-loss harvesting opportunities.

        Args:
            positions: Current positions to analyze
            current_prices: Current prices (uses position prices if not provided)
            realized_gains_ytd: Year-to-date realized capital gains

        Returns:
            List of harvesting opportunities, sorted by benefit
        """
        opportunities = []
        today = datetime.now()

        for position in positions:
            # Skip positions without unrealized losses
            if position.unrealized_gain_loss >= 0:
                continue

            # Check minimum loss threshold
            loss_amount = abs(position.unrealized_gain_loss)
            if loss_amount < self.config.min_harvest_amount:
                continue

            # Get tax lot details for holding period analysis
            lots = self.cost_basis_tracker.get_lots(position.symbol)

            # Analyze harvest opportunity
            opportunity = self._analyze_opportunity(
                position=position,
                lots=lots,
                realized_gains_ytd=realized_gains_ytd,
                today=today,
            )

            if opportunity.harvest_recommended:
                opportunities.append(opportunity)

        # Sort by estimated tax savings (highest first)
        opportunities.sort(key=lambda x: x.estimated_tax_savings, reverse=True)

        return opportunities

    def _analyze_opportunity(
        self,
        position: Position,
        lots: List[TaxLot],
        realized_gains_ytd: Decimal,
        today: datetime,
    ) -> TaxHarvestOpportunity:
        """
        Analyze a single position for harvesting potential.
        """
        loss_amount = abs(position.unrealized_gain_loss)

        # Determine gain type based on holding period
        # Use shortest holding period lot for conservative estimate
        shortest_holding = min(
            (lot.holding_period_days for lot in lots if lot.remaining_quantity > 0),
            default=0,
        )

        if shortest_holding > 365:
            gain_type = GainType.LONG_TERM
        else:
            gain_type = GainType.SHORT_TERM

        # Calculate days to long-term
        days_to_long_term = max(0, 366 - shortest_holding) if shortest_holding <= 365 else None

        # Check wash sale risk
        wash_sale_risk = self._check_wash_sale_risk(position.symbol)
        lockout_date = self._harvested_symbols.get(position.symbol)

        # Calculate estimated tax savings
        tax_savings = self._estimate_tax_savings(
            loss_amount=loss_amount,
            gain_type=gain_type,
            realized_gains_ytd=realized_gains_ytd,
        )

        # Determine recommendation
        harvest_recommended = True
        recommendation_reason = ""

        if wash_sale_risk:
            harvest_recommended = False
            recommendation_reason = "High wash sale risk - recent purchases detected"
        elif lockout_date and lockout_date > today.date():
            harvest_recommended = False
            recommendation_reason = f"In wash sale lockout until {lockout_date}"
        elif days_to_long_term and days_to_long_term <= self.config.days_warning_threshold:
            if self.config.warn_if_close_to_long_term:
                recommendation_reason = (
                    f"Consider waiting {days_to_long_term} days for long-term treatment"
                )
        else:
            recommendation_reason = (
                f"Good candidate - ${tax_savings:.2f} estimated tax savings"
            )

        # Get replacement suggestions
        replacements = self._get_replacement_suggestions(position.symbol)

        return TaxHarvestOpportunity(
            symbol=position.symbol,
            position_id=str(id(position)),
            quantity=position.quantity,
            cost_basis=position.total_cost_basis,
            current_value=position.market_value,
            unrealized_loss=loss_amount,
            estimated_tax_savings=tax_savings,
            gain_type_if_harvested=gain_type,
            days_held=shortest_holding,
            days_to_long_term=days_to_long_term,
            wash_sale_risk=wash_sale_risk,
            wash_sale_lockout_until=lockout_date,
            harvest_recommended=harvest_recommended,
            recommendation_reason=recommendation_reason,
            replacement_symbols=replacements,
        )

    def _estimate_tax_savings(
        self,
        loss_amount: Decimal,
        gain_type: GainType,
        realized_gains_ytd: Decimal,
    ) -> Decimal:
        """
        Estimate tax savings from harvesting a loss.

        Losses offset gains in this order:
        1. Short-term losses offset short-term gains (taxed at marginal rate)
        2. Long-term losses offset long-term gains (taxed at LT rate)
        3. Net losses offset opposite type gains
        4. Up to $3,000 offsets ordinary income
        5. Remainder carries forward
        """
        marginal_rate = self.config.assumed_marginal_rate
        lt_rate = self.config.assumed_long_term_rate

        if gain_type == GainType.SHORT_TERM:
            # Short-term loss - most valuable, offsets at marginal rate
            if realized_gains_ytd > 0:
                # Offset gains at marginal rate
                gains_to_offset = min(loss_amount, realized_gains_ytd)
                savings = gains_to_offset * marginal_rate

                # Remaining loss
                remaining = loss_amount - gains_to_offset
                if remaining > 0:
                    # Up to $3,000 offsets ordinary income
                    ordinary_offset = min(remaining, Decimal("3000"))
                    savings += ordinary_offset * marginal_rate

                return savings
            else:
                # No gains to offset - up to $3,000 offsets ordinary income
                ordinary_offset = min(loss_amount, Decimal("3000"))
                return ordinary_offset * marginal_rate

        else:
            # Long-term loss
            if realized_gains_ytd > 0:
                # First offset gains
                gains_to_offset = min(loss_amount, realized_gains_ytd)
                savings = gains_to_offset * lt_rate

                remaining = loss_amount - gains_to_offset
                if remaining > 0:
                    ordinary_offset = min(remaining, Decimal("3000"))
                    savings += ordinary_offset * marginal_rate

                return savings
            else:
                ordinary_offset = min(loss_amount, Decimal("3000"))
                return ordinary_offset * marginal_rate

    def _check_wash_sale_risk(self, symbol: str) -> bool:
        """
        Check if symbol has wash sale risk from recent activity.
        """
        check = self.wash_sale_detector.check_potential_wash_sale(
            symbol=symbol,
            sale_date=datetime.now(),
        )
        return check.get("risk_level") == "high"

    def _get_replacement_suggestions(self, symbol: str) -> List[str]:
        """
        Get suggested replacement securities to maintain exposure.
        """
        if not self.config.suggest_replacements:
            return []

        # Check our predefined pairs
        suggestions = REPLACEMENT_PAIRS.get(symbol.upper(), [])

        # Limit to max suggestions
        return suggestions[: self.config.max_replacement_suggestions]

    def record_harvest(
        self,
        symbol: str,
        harvest_date: Optional[date] = None,
    ) -> date:
        """
        Record a harvested position to track wash sale lockout.

        Args:
            symbol: Symbol that was harvested
            harvest_date: Date of harvest (defaults to today)

        Returns:
            Date when it's safe to repurchase
        """
        harvest_date = harvest_date or datetime.now().date()

        # Calculate safe repurchase date (31 days after to be safe)
        safe_date = harvest_date + timedelta(days=self.config.wash_sale_avoidance_days)

        self._harvested_symbols[symbol] = safe_date

        logger.info(
            f"Recorded harvest of {symbol}. "
            f"Safe to repurchase after {safe_date}"
        )

        return safe_date

    def get_safe_to_repurchase(self, symbol: str) -> Optional[date]:
        """
        Get the date when a harvested symbol is safe to repurchase.

        Args:
            symbol: The symbol to check

        Returns:
            Safe repurchase date, or None if no lockout
        """
        lockout = self._harvested_symbols.get(symbol)
        if lockout and lockout > datetime.now().date():
            return lockout
        return None

    def calculate_harvest_summary(
        self,
        opportunities: List[TaxHarvestOpportunity],
    ) -> Dict[str, Decimal]:
        """
        Calculate summary statistics for harvesting opportunities.

        Args:
            opportunities: List of opportunities to summarize

        Returns:
            Summary dictionary
        """
        total_harvestable_loss = sum(
            opp.unrealized_loss for opp in opportunities
            if opp.harvest_recommended
        )

        total_tax_savings = sum(
            opp.estimated_tax_savings for opp in opportunities
            if opp.harvest_recommended
        )

        short_term_losses = sum(
            opp.unrealized_loss for opp in opportunities
            if opp.harvest_recommended and opp.gain_type_if_harvested == GainType.SHORT_TERM
        )

        long_term_losses = sum(
            opp.unrealized_loss for opp in opportunities
            if opp.harvest_recommended and opp.gain_type_if_harvested == GainType.LONG_TERM
        )

        return {
            "total_opportunities": len(opportunities),
            "recommended_count": sum(1 for o in opportunities if o.harvest_recommended),
            "total_harvestable_loss": total_harvestable_loss,
            "short_term_harvestable": short_term_losses,
            "long_term_harvestable": long_term_losses,
            "estimated_tax_savings": total_tax_savings,
            "max_ordinary_income_offset": min(
                total_harvestable_loss, Decimal("3000")
            ),
        }

    def get_year_end_recommendations(
        self,
        positions: List[Position],
        realized_gains_ytd: Decimal,
        realized_losses_ytd: Decimal,
    ) -> Dict[str, any]:
        """
        Get year-end tax-loss harvesting recommendations.

        Args:
            positions: Current positions
            realized_gains_ytd: Year-to-date realized gains
            realized_losses_ytd: Year-to-date realized losses

        Returns:
            Comprehensive year-end recommendations
        """
        net_gains = realized_gains_ytd - realized_losses_ytd

        # Scan for opportunities
        opportunities = self.scan_for_opportunities(
            positions=positions,
            realized_gains_ytd=realized_gains_ytd,
        )

        summary = self.calculate_harvest_summary(opportunities)

        # Calculate what harvesting would achieve
        if net_gains > 0:
            # Have gains to offset
            harvesting_target = net_gains
            action = "offset gains"
        else:
            # Already have net losses - harvest for carryforward
            harvesting_target = Decimal("3000") - abs(net_gains)
            harvesting_target = max(Decimal("0"), harvesting_target)
            action = "maximize ordinary income offset"

        return {
            "current_net_gains": net_gains,
            "recommended_action": action,
            "harvesting_target": harvesting_target,
            "opportunities": opportunities,
            "summary": summary,
            "year_end_deadline": date(datetime.now().year, 12, 31),
            "recommendation": (
                f"Consider harvesting ${summary['total_harvestable_loss']:.2f} "
                f"in losses to {action}. Estimated tax savings: "
                f"${summary['estimated_tax_savings']:.2f}"
            ),
        }
