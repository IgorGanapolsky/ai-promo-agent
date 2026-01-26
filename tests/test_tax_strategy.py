"""
Tests for the Tax Strategy Module.

Tests cover:
- Cost basis tracking and tax lot management
- Wash sale detection and adjustment
- Tax-loss harvesting opportunity identification
- Capital gains calculation
- Section 1256 contract handling
"""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal

from src.tax_strategy.models import (
    TaxLot,
    Trade,
    Position,
    AssetClass,
    OptionType,
    OrderSide,
    GainType,
    CostBasisMethod,
)
from src.tax_strategy.config import TaxConfig
from src.tax_strategy.cost_basis import CostBasisTracker
from src.tax_strategy.wash_sale import WashSaleDetector
from src.tax_strategy.tax_loss_harvesting import TaxLossHarvester
from src.tax_strategy.capital_gains import CapitalGainsCalculator
from src.tax_strategy.section_1256 import Section1256Handler


class TestTaxLot:
    """Tests for TaxLot model."""

    def test_holding_period_calculation(self):
        """Test holding period days calculation."""
        lot = TaxLot(
            symbol="AAPL",
            quantity=Decimal("100"),
            cost_per_unit=Decimal("150.00"),
            total_cost=Decimal("15000.00"),
            acquisition_date=datetime.now() - timedelta(days=400),
        )

        assert lot.holding_period_days >= 400
        assert lot.is_long_term is True
        assert lot.gain_type == GainType.LONG_TERM

    def test_short_term_classification(self):
        """Test short-term gain classification."""
        lot = TaxLot(
            symbol="AAPL",
            quantity=Decimal("100"),
            cost_per_unit=Decimal("150.00"),
            total_cost=Decimal("15000.00"),
            acquisition_date=datetime.now() - timedelta(days=100),
        )

        assert lot.is_long_term is False
        assert lot.gain_type == GainType.SHORT_TERM

    def test_section_1256_classification(self):
        """Test Section 1256 contract classification."""
        lot = TaxLot(
            symbol="SPX240119C04500000",
            asset_class=AssetClass.INDEX_OPTION,
            quantity=Decimal("1"),
            cost_per_unit=Decimal("50.00"),
            total_cost=Decimal("5000.00"),
            acquisition_date=datetime.now() - timedelta(days=10),
        )

        # Section 1256 regardless of holding period
        assert lot.gain_type == GainType.SECTION_1256


class TestCostBasisTracker:
    """Tests for cost basis tracking."""

    def test_fifo_lot_matching(self):
        """Test FIFO lot matching on sale."""
        tracker = CostBasisTracker()

        # Buy 100 shares at $100
        lot1 = TaxLot(
            symbol="AAPL",
            quantity=Decimal("100"),
            cost_per_unit=Decimal("100.00"),
            total_cost=Decimal("10000.00"),
            acquisition_date=datetime.now() - timedelta(days=60),
        )
        tracker.add_lot(lot1)

        # Buy 100 more at $120
        lot2 = TaxLot(
            symbol="AAPL",
            quantity=Decimal("100"),
            cost_per_unit=Decimal("120.00"),
            total_cost=Decimal("12000.00"),
            acquisition_date=datetime.now() - timedelta(days=30),
        )
        tracker.add_lot(lot2)

        # Sell 100 at $150 - should match lot1 (FIFO)
        cost_basis, matches = tracker.sell(
            symbol="AAPL",
            quantity=Decimal("100"),
            sale_price=Decimal("150.00"),
            sale_date=datetime.now(),
            method=CostBasisMethod.FIFO,
        )

        assert cost_basis == Decimal("10000.00")
        assert len(matches) == 1
        assert matches[0]["lot_id"] == lot1.id

    def test_lifo_lot_matching(self):
        """Test LIFO lot matching on sale."""
        tracker = CostBasisTracker()

        # Buy 100 shares at $100
        lot1 = TaxLot(
            symbol="AAPL",
            quantity=Decimal("100"),
            cost_per_unit=Decimal("100.00"),
            total_cost=Decimal("10000.00"),
            acquisition_date=datetime.now() - timedelta(days=60),
        )
        tracker.add_lot(lot1)

        # Buy 100 more at $120
        lot2 = TaxLot(
            symbol="AAPL",
            quantity=Decimal("100"),
            cost_per_unit=Decimal("120.00"),
            total_cost=Decimal("12000.00"),
            acquisition_date=datetime.now() - timedelta(days=30),
        )
        tracker.add_lot(lot2)

        # Sell 100 at $150 - should match lot2 (LIFO)
        cost_basis, matches = tracker.sell(
            symbol="AAPL",
            quantity=Decimal("100"),
            sale_price=Decimal("150.00"),
            sale_date=datetime.now(),
            method=CostBasisMethod.LIFO,
        )

        assert cost_basis == Decimal("12000.00")
        assert len(matches) == 1
        assert matches[0]["lot_id"] == lot2.id

    def test_partial_lot_sale(self):
        """Test selling partial lot."""
        tracker = CostBasisTracker()

        lot = TaxLot(
            symbol="AAPL",
            quantity=Decimal("100"),
            cost_per_unit=Decimal("100.00"),
            total_cost=Decimal("10000.00"),
            acquisition_date=datetime.now() - timedelta(days=60),
        )
        tracker.add_lot(lot)

        # Sell 50 shares
        cost_basis, matches = tracker.sell(
            symbol="AAPL",
            quantity=Decimal("50"),
            sale_price=Decimal("150.00"),
            sale_date=datetime.now(),
        )

        # Should have 50% of cost basis
        assert cost_basis == Decimal("5000.00")
        assert lot.remaining_quantity == Decimal("50")
        assert lot.is_closed is False


class TestWashSaleDetector:
    """Tests for wash sale detection."""

    def test_wash_sale_detection_purchase_after_sale(self):
        """Test wash sale when repurchasing within 30 days."""
        detector = WashSaleDetector()

        # Sell at a loss
        sale_trade = Trade(
            symbol="AAPL",
            side=OrderSide.SELL,
            quantity=Decimal("100"),
            price=Decimal("90.00"),
            proceeds=Decimal("9000.00"),
            cost_basis=Decimal("10000.00"),
            realized_gain_loss=Decimal("-1000.00"),
            trade_date=datetime.now() - timedelta(days=15),
        )

        wash_sale = detector.add_trade(sale_trade)

        # No wash sale yet (no replacement)
        assert wash_sale is None

        # Buy within 30 days - triggers wash sale
        buy_trade = Trade(
            symbol="AAPL",
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            price=Decimal("95.00"),
            proceeds=Decimal("9500.00"),
            trade_date=datetime.now(),
        )

        wash_sale = detector.add_trade(buy_trade)

        assert wash_sale is not None
        assert wash_sale.disallowed_loss == Decimal("1000.00")

    def test_no_wash_sale_after_30_days(self):
        """Test no wash sale when repurchasing after 30 days."""
        detector = WashSaleDetector()

        # Sell at a loss
        sale_trade = Trade(
            symbol="AAPL",
            side=OrderSide.SELL,
            quantity=Decimal("100"),
            price=Decimal("90.00"),
            proceeds=Decimal("9000.00"),
            cost_basis=Decimal("10000.00"),
            realized_gain_loss=Decimal("-1000.00"),
            trade_date=datetime.now() - timedelta(days=35),
        )

        detector.add_trade(sale_trade)

        # Buy after 30+ days - no wash sale
        buy_trade = Trade(
            symbol="AAPL",
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            price=Decimal("95.00"),
            proceeds=Decimal("9500.00"),
            trade_date=datetime.now(),
        )

        wash_sale = detector.add_trade(buy_trade)

        assert wash_sale is None

    def test_section_1256_exempt_from_wash_sale(self):
        """Test Section 1256 contracts are exempt."""
        detector = WashSaleDetector()

        # Sell index option at a loss
        sale_trade = Trade(
            symbol="SPX240119C04500000",
            asset_class=AssetClass.INDEX_OPTION,
            side=OrderSide.SELL_TO_CLOSE,
            quantity=Decimal("1"),
            price=Decimal("10.00"),
            proceeds=Decimal("1000.00"),
            cost_basis=Decimal("2000.00"),
            realized_gain_loss=Decimal("-1000.00"),
            trade_date=datetime.now() - timedelta(days=15),
        )

        wash_sale = detector.add_trade(sale_trade)

        # Section 1256 is exempt
        assert wash_sale is None


class TestTaxLossHarvester:
    """Tests for tax-loss harvesting."""

    def test_identify_harvest_opportunity(self):
        """Test identification of harvest opportunities."""
        harvester = TaxLossHarvester()

        # Position with unrealized loss
        position = Position(
            symbol="AAPL",
            quantity=Decimal("100"),
            total_cost_basis=Decimal("15000.00"),
            current_price=Decimal("140.00"),
            market_value=Decimal("14000.00"),
            unrealized_gain_loss=Decimal("-1000.00"),
        )

        opportunities = harvester.scan_for_opportunities([position])

        assert len(opportunities) > 0
        assert opportunities[0].symbol == "AAPL"
        assert opportunities[0].unrealized_loss == Decimal("1000.00")

    def test_replacement_suggestions(self):
        """Test replacement security suggestions."""
        harvester = TaxLossHarvester()

        position = Position(
            symbol="SPY",
            quantity=Decimal("100"),
            total_cost_basis=Decimal("50000.00"),
            current_price=Decimal("480.00"),
            market_value=Decimal("48000.00"),
            unrealized_gain_loss=Decimal("-2000.00"),
        )

        opportunities = harvester.scan_for_opportunities([position])

        # Should suggest VOO, IVV as replacements for SPY
        assert len(opportunities) > 0
        assert len(opportunities[0].replacement_symbols) > 0
        assert "VOO" in opportunities[0].replacement_symbols


class TestCapitalGainsCalculator:
    """Tests for capital gains calculation."""

    def test_short_term_gain_calculation(self):
        """Test short-term gain calculation."""
        calc = CapitalGainsCalculator()

        trade = Trade(
            symbol="AAPL",
            side=OrderSide.SELL,
            quantity=Decimal("100"),
            price=Decimal("160.00"),
            proceeds=Decimal("16000.00"),
            cost_basis=Decimal("15000.00"),
            realized_gain_loss=Decimal("1000.00"),
            gain_type=GainType.SHORT_TERM,
            trade_date=datetime.now(),
            acquisition_date=datetime.now() - timedelta(days=100),
        )

        gains = calc.process_trade(trade)

        assert len(gains) == 1
        assert gains[0].gain_type == GainType.SHORT_TERM
        assert gains[0].gain_or_loss == Decimal("1000.00")

    def test_tax_summary_calculation(self):
        """Test comprehensive tax summary."""
        calc = CapitalGainsCalculator()

        # Process several trades
        trades = [
            Trade(
                symbol="AAPL",
                side=OrderSide.SELL,
                quantity=Decimal("100"),
                proceeds=Decimal("16000.00"),
                cost_basis=Decimal("15000.00"),
                realized_gain_loss=Decimal("1000.00"),
                gain_type=GainType.SHORT_TERM,
                trade_date=datetime.now(),
            ),
            Trade(
                symbol="MSFT",
                side=OrderSide.SELL,
                quantity=Decimal("50"),
                proceeds=Decimal("20000.00"),
                cost_basis=Decimal("15000.00"),
                realized_gain_loss=Decimal("5000.00"),
                gain_type=GainType.LONG_TERM,
                trade_date=datetime.now(),
            ),
        ]

        for trade in trades:
            calc.process_trade(trade)

        summary = calc.get_tax_summary()

        assert summary.short_term_gains == Decimal("1000.00")
        assert summary.long_term_gains == Decimal("5000.00")
        assert summary.net_capital_gain_loss == Decimal("6000.00")


class TestSection1256Handler:
    """Tests for Section 1256 contract handling."""

    def test_is_section_1256(self):
        """Test Section 1256 identification."""
        handler = Section1256Handler()

        # Index options qualify
        assert handler.is_section_1256("SPX") is True
        assert handler.is_section_1256("SPX240119C04500000", "SPX") is True
        assert handler.is_section_1256("NDX") is True

        # ETF options do NOT qualify
        assert handler.is_section_1256("SPY") is False
        assert handler.is_section_1256("QQQ") is False

    def test_60_40_split(self):
        """Test 60/40 long-term/short-term split."""
        handler = Section1256Handler()

        # Add and close a contract
        contract = handler.add_contract(
            symbol="SPX240119C04500000",
            quantity=Decimal("1"),
            entry_price=Decimal("50.00"),
            underlying="SPX",
        )

        result = handler.close_contract(
            contract_id=contract.id,
            exit_price=Decimal("75.00"),
        )

        # Gain = (75 - 50) * 100 = $2500
        total_gain = Decimal("2500.00")

        assert result["total_gain_loss"] == total_gain
        assert result["long_term_portion"] == total_gain * Decimal("0.60")  # $1500
        assert result["short_term_portion"] == total_gain * Decimal("0.40")  # $1000

    def test_form_6781_data(self):
        """Test Form 6781 data generation."""
        handler = Section1256Handler()

        contract = handler.add_contract(
            symbol="SPX240119C04500000",
            quantity=Decimal("1"),
            entry_price=Decimal("50.00"),
            underlying="SPX",
        )

        handler.close_contract(
            contract_id=contract.id,
            exit_price=Decimal("75.00"),
        )

        form_data = handler.get_form_6781_data()

        assert form_data["aggregate_profit_or_loss"] == Decimal("2500.00")
        assert form_data["long_term_portion"] == Decimal("1500.00")
        assert form_data["short_term_portion"] == Decimal("1000.00")


class TestIntegration:
    """Integration tests combining multiple components."""

    def test_full_trade_lifecycle(self):
        """Test complete trade lifecycle with wash sale."""
        from src.tax_strategy.cost_basis import CostBasisTracker
        from src.tax_strategy.wash_sale import WashSaleDetector
        from src.tax_strategy.capital_gains import CapitalGainsCalculator

        cost_basis = CostBasisTracker()
        wash_sales = WashSaleDetector(cost_basis_tracker=cost_basis)
        cap_gains = CapitalGainsCalculator(cost_basis_tracker=cost_basis)

        # 1. Buy 100 shares
        buy_trade = Trade(
            symbol="AAPL",
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            price=Decimal("150.00"),
            proceeds=Decimal("15000.00"),
            trade_date=datetime.now() - timedelta(days=60),
        )
        lot = cost_basis.create_lot_from_trade(buy_trade)

        # 2. Sell at a loss
        sell_date = datetime.now() - timedelta(days=15)
        cost, matches = cost_basis.sell(
            symbol="AAPL",
            quantity=Decimal("100"),
            sale_price=Decimal("140.00"),
            sale_date=sell_date,
        )

        sell_trade = Trade(
            symbol="AAPL",
            side=OrderSide.SELL,
            quantity=Decimal("100"),
            price=Decimal("140.00"),
            proceeds=Decimal("14000.00"),
            cost_basis=cost,
            realized_gain_loss=Decimal("14000.00") - cost,
            trade_date=sell_date,
        )

        # Check for wash sale (none yet)
        ws = wash_sales.add_trade(sell_trade)
        assert ws is None  # No replacement purchase yet

        # 3. Repurchase within 30 days - triggers wash sale
        rebuy_trade = Trade(
            symbol="AAPL",
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            price=Decimal("145.00"),
            proceeds=Decimal("14500.00"),
            trade_date=datetime.now(),
        )

        ws = wash_sales.add_trade(rebuy_trade)
        assert ws is not None
        assert ws.disallowed_loss == Decimal("1000.00")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
