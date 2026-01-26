"""
Tax Strategy Engine - Main orchestrator for tax management.

Coordinates all tax strategy components and provides a unified
interface for tax tracking, analysis, and reporting.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Dict, Any
import logging

from .models import (
    Trade,
    Position,
    TaxLot,
    TaxSummary,
    TaxHarvestOpportunity,
    AssetClass,
    OrderSide,
)
from .config import TaxConfig
from .alpaca_client import AlpacaTaxClient
from .cost_basis import CostBasisTracker
from .wash_sale import WashSaleDetector
from .tax_loss_harvesting import TaxLossHarvester
from .capital_gains import CapitalGainsCalculator
from .section_1256 import Section1256Handler
from .reports import TaxReportGenerator

logger = logging.getLogger(__name__)


class TaxStrategyEngine:
    """
    Main orchestrator for the tax strategy system.

    Provides a unified interface for:
    - Syncing trades from Alpaca
    - Processing trades for tax purposes
    - Tracking cost basis and tax lots
    - Detecting wash sales
    - Identifying tax-loss harvesting opportunities
    - Generating tax reports

    Usage:
        engine = TaxStrategyEngine()
        engine.sync_from_alpaca()
        summary = engine.get_tax_summary()
        opportunities = engine.scan_harvest_opportunities()
        engine.generate_reports()
    """

    def __init__(self, config: Optional[TaxConfig] = None):
        """
        Initialize the tax strategy engine.

        Args:
            config: Tax configuration. Loads from environment if not provided.
        """
        self.config = config or TaxConfig.from_env()

        # Initialize components
        self.alpaca_client = AlpacaTaxClient(self.config.alpaca)
        self.cost_basis = CostBasisTracker(self.config.cost_basis)
        self.wash_sales = WashSaleDetector(
            config=self.config.wash_sale,
            cost_basis_tracker=self.cost_basis,
        )
        self.capital_gains = CapitalGainsCalculator(
            config=self.config,
            cost_basis_tracker=self.cost_basis,
        )
        self.section_1256 = Section1256Handler(self.config)
        self.harvester = TaxLossHarvester(
            config=self.config.tax_loss_harvesting,
            tax_config=self.config,
            cost_basis_tracker=self.cost_basis,
            wash_sale_detector=self.wash_sales,
        )
        self.reports = TaxReportGenerator(
            config=self.config.reporting,
            tax_config=self.config,
            capital_gains_calc=self.capital_gains,
            wash_sale_detector=self.wash_sales,
            section_1256_handler=self.section_1256,
            tax_loss_harvester=self.harvester,
        )

        # State
        self._positions: List[Position] = []
        self._last_sync: Optional[datetime] = None

        logger.info("Tax Strategy Engine initialized")

    def sync_from_alpaca(
        self,
        start_date: Optional[datetime] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Synchronize trading data from Alpaca.

        Args:
            start_date: Start date for historical data
            force: Force sync even if recently synced

        Returns:
            Sync results summary
        """
        # Check if we should sync
        if not force and self._last_sync:
            since_sync = datetime.now() - self._last_sync
            if since_sync < timedelta(minutes=self.config.alpaca.sync_interval_minutes):
                logger.info(
                    f"Skipping sync - last sync {since_sync.seconds}s ago"
                )
                return {"skipped": True, "last_sync": self._last_sync}

        logger.info("Starting Alpaca sync...")

        # Fetch data
        sync_data = self.alpaca_client.sync_all(start_date)

        # Process trades
        trades_processed = 0
        for trade in sync_data["trades"]:
            self._process_trade(trade)
            trades_processed += 1

        # Update positions
        self._positions = sync_data["positions"]

        self._last_sync = datetime.now()

        result = {
            "sync_time": self._last_sync,
            "trades_processed": trades_processed,
            "positions": len(self._positions),
            "account": sync_data["account"],
        }

        logger.info(
            f"Sync complete: {trades_processed} trades, "
            f"{len(self._positions)} positions"
        )

        return result

    def _process_trade(self, trade: Trade) -> None:
        """
        Process a single trade through the tax system.

        Args:
            trade: Trade to process
        """
        # Determine if it's a buy or sell
        if trade.side in (OrderSide.BUY, OrderSide.BUY_TO_OPEN):
            # Create tax lot for purchase
            self.cost_basis.create_lot_from_trade(trade)

            # Check for Section 1256
            if self.section_1256.is_section_1256(
                trade.symbol,
                trade.underlying_symbol,
            ):
                self.section_1256.add_contract(
                    symbol=trade.symbol,
                    quantity=trade.quantity,
                    entry_price=trade.price,
                    entry_date=trade.trade_date,
                    underlying=trade.underlying_symbol,
                )

        elif trade.side in (OrderSide.SELL, OrderSide.SELL_TO_CLOSE):
            # Process sale - match against tax lots
            cost_basis, lot_matches = self.cost_basis.sell(
                symbol=trade.symbol,
                quantity=trade.quantity,
                sale_price=trade.price,
                sale_date=trade.trade_date,
                commission=trade.commission,
                fees=trade.fees,
            )

            # Update trade with cost basis
            trade.cost_basis = cost_basis
            trade.adjusted_cost_basis = cost_basis

            # Calculate gain/loss
            trade.realized_gain_loss = trade.proceeds - cost_basis

            # Determine gain type from lot matches
            if lot_matches:
                trade.gain_type = lot_matches[0]["gain_type"]
                trade.acquisition_date = lot_matches[0]["acquisition_date"]

            # Process for capital gains
            self.capital_gains.process_trade(trade, lot_matches)

            # Check for wash sales
            wash_sale = self.wash_sales.add_trade(trade)
            if wash_sale:
                trade.is_wash_sale = True
                trade.wash_sale_disallowed = wash_sale.disallowed_loss

    def add_manual_trade(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        trade_date: Optional[datetime] = None,
        asset_class: str = "equity",
        **kwargs,
    ) -> Trade:
        """
        Manually add a trade (for non-Alpaca trades).

        Args:
            symbol: Trading symbol
            side: Order side (buy, sell, buy_to_open, etc.)
            quantity: Quantity traded
            price: Price per unit
            trade_date: Date of trade
            asset_class: Asset class (equity, equity_option, index_option)
            **kwargs: Additional trade attributes

        Returns:
            The processed trade
        """
        trade = Trade(
            symbol=symbol,
            side=OrderSide(side.lower()),
            quantity=quantity,
            price=price,
            proceeds=quantity * price,
            trade_date=trade_date or datetime.now(),
            asset_class=AssetClass(asset_class),
            **kwargs,
        )

        self._process_trade(trade)

        return trade

    def get_tax_summary(
        self,
        tax_year: Optional[int] = None,
    ) -> TaxSummary:
        """
        Get comprehensive tax summary for a year.

        Args:
            tax_year: Tax year (defaults to current year)

        Returns:
            TaxSummary with all calculations
        """
        return self.capital_gains.get_tax_summary(
            tax_year=tax_year,
            prior_carryforward=self.config.prior_year_loss_carryforward,
        )

    def scan_harvest_opportunities(
        self,
        positions: Optional[List[Position]] = None,
    ) -> List[TaxHarvestOpportunity]:
        """
        Scan for tax-loss harvesting opportunities.

        Args:
            positions: Positions to analyze (uses synced positions if not provided)

        Returns:
            List of harvesting opportunities
        """
        positions = positions or self._positions

        # Get year-to-date gains
        summary = self.get_tax_summary()
        ytd_gains = summary.total_gains - summary.total_losses

        return self.harvester.scan_for_opportunities(
            positions=positions,
            realized_gains_ytd=ytd_gains,
        )

    def get_harvest_recommendations(self) -> Dict[str, Any]:
        """
        Get comprehensive harvest recommendations.

        Returns:
            Recommendations including opportunities and strategy
        """
        summary = self.get_tax_summary()

        return self.harvester.get_year_end_recommendations(
            positions=self._positions,
            realized_gains_ytd=summary.total_gains,
            realized_losses_ytd=summary.total_losses,
        )

    def check_wash_sale_risk(
        self,
        symbol: str,
        planned_sale_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Check wash sale risk before selling a position.

        Args:
            symbol: Symbol to potentially sell
            planned_sale_date: Planned sale date

        Returns:
            Wash sale risk assessment
        """
        return self.wash_sales.check_potential_wash_sale(
            symbol=symbol,
            sale_date=planned_sale_date or datetime.now(),
        )

    def estimate_quarterly_tax(
        self,
        quarter: int,
        tax_year: Optional[int] = None,
    ) -> Dict[str, Decimal]:
        """
        Estimate quarterly tax payment.

        Args:
            quarter: Quarter number (1-4)
            tax_year: Tax year

        Returns:
            Estimated quarterly payment
        """
        return self.capital_gains.estimate_quarterly_tax(quarter, tax_year)

    def perform_year_end_mtm(
        self,
        year_end_prices: Dict[str, Decimal],
        tax_year: Optional[int] = None,
    ) -> List[Dict]:
        """
        Perform year-end mark-to-market for Section 1256 contracts.

        Args:
            year_end_prices: Year-end prices for open contracts
            tax_year: Tax year

        Returns:
            MTM adjustments made
        """
        return self.section_1256.perform_year_end_mtm(
            year_end_prices=year_end_prices,
            tax_year=tax_year,
        )

    def generate_reports(
        self,
        tax_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Generate all tax reports for a year.

        Args:
            tax_year: Tax year to report

        Returns:
            Generated reports summary
        """
        return self.reports.generate_all_reports(tax_year)

    def get_positions(self) -> List[Position]:
        """Get current positions."""
        return self._positions

    def get_tax_lots(
        self,
        symbol: Optional[str] = None,
        include_closed: bool = False,
    ) -> List[TaxLot]:
        """
        Get tax lots.

        Args:
            symbol: Filter to specific symbol
            include_closed: Include closed lots

        Returns:
            List of tax lots
        """
        return self.cost_basis.get_lots(symbol, include_closed)

    def get_unrealized_gains(
        self,
        current_prices: Dict[str, Decimal],
    ) -> Dict[str, Dict]:
        """
        Get unrealized gains/losses.

        Args:
            current_prices: Current prices by symbol

        Returns:
            Unrealized gains by symbol
        """
        return self.cost_basis.get_unrealized_gains(current_prices)

    def export_state(self) -> Dict[str, Any]:
        """
        Export current state for persistence.

        Returns:
            State dictionary for later import
        """
        return {
            "export_time": datetime.now().isoformat(),
            "tax_lots": self.cost_basis.export_lots(),
            "wash_sales": self.wash_sales.export_wash_sales(),
            "section_1256": self.section_1256.export_contracts(),
            "capital_gains": self.capital_gains.export_gains(),
        }

    def import_state(self, state: Dict[str, Any]) -> None:
        """
        Import previously exported state.

        Args:
            state: State dictionary from export_state()
        """
        if "tax_lots" in state:
            self.cost_basis.import_lots(state["tax_lots"])

        logger.info("State imported successfully")


def create_engine(
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
    paper: bool = True,
    **config_overrides,
) -> TaxStrategyEngine:
    """
    Factory function to create a configured TaxStrategyEngine.

    Args:
        api_key: Alpaca API key
        api_secret: Alpaca API secret
        paper: Use paper trading environment
        **config_overrides: Additional config overrides

    Returns:
        Configured TaxStrategyEngine
    """
    config = TaxConfig.from_env()

    if api_key:
        config.alpaca.api_key = api_key
    if api_secret:
        config.alpaca.api_secret = api_secret
    config.alpaca.paper_trading = paper

    # Apply overrides
    for key, value in config_overrides.items():
        if hasattr(config, key):
            setattr(config, key, value)

    return TaxStrategyEngine(config)
