"""
Tax Strategy Module for Options Trading

A comprehensive tax management system for options trading with Alpaca Markets.
Handles cost basis tracking, wash sale detection, tax-loss harvesting,
capital gains calculation, and Section 1256 contract treatment.

Key Features:
- Cost basis tracking (FIFO, LIFO, Specific ID methods)
- Wash sale rule detection and basis adjustment
- Tax-loss harvesting opportunity identification
- Short-term vs long-term capital gains classification
- Section 1256 contract 60/40 tax treatment
- Iron condor and multi-leg strategy support
- Alpaca API integration for trade synchronization
- Tax report generation (Form 8949, Schedule D, Form 6781)

Usage:
    from src.tax_strategy import TaxStrategyEngine

    engine = TaxStrategyEngine()
    engine.sync_from_alpaca()

    # Get tax summary
    summary = engine.get_tax_summary()
    print(f"Net capital gain/loss: ${summary.net_capital_gain_loss}")

    # Find tax-loss harvesting opportunities
    opportunities = engine.scan_harvest_opportunities()
    for opp in opportunities:
        print(f"{opp.symbol}: ${opp.unrealized_loss} potential harvest")

    # Generate tax reports
    reports = engine.generate_reports()
"""

from .models import (
    TaxLot,
    Trade,
    Position,
    WashSale,
    CapitalGain,
    TaxHarvestOpportunity,
    Section1256Contract,
    IronCondor,
    TaxSummary,
    AssetClass,
    OptionType,
    PositionSide,
    OrderSide,
    CostBasisMethod,
    GainType,
)
from .config import TaxConfig, TaxRates, AlpacaConfig
from .alpaca_client import AlpacaTaxClient
from .cost_basis import CostBasisTracker
from .wash_sale import WashSaleDetector
from .tax_loss_harvesting import TaxLossHarvester
from .capital_gains import CapitalGainsCalculator
from .section_1256 import Section1256Handler
from .reports import TaxReportGenerator
from .engine import TaxStrategyEngine, create_engine

__version__ = "1.0.0"

__all__ = [
    # Models
    "TaxLot",
    "Trade",
    "Position",
    "WashSale",
    "CapitalGain",
    "TaxHarvestOpportunity",
    "Section1256Contract",
    "IronCondor",
    "TaxSummary",
    # Enums
    "AssetClass",
    "OptionType",
    "PositionSide",
    "OrderSide",
    "CostBasisMethod",
    "GainType",
    # Config
    "TaxConfig",
    "TaxRates",
    "AlpacaConfig",
    # Core components
    "AlpacaTaxClient",
    "CostBasisTracker",
    "WashSaleDetector",
    "TaxLossHarvester",
    "CapitalGainsCalculator",
    "Section1256Handler",
    "TaxReportGenerator",
    # Engine
    "TaxStrategyEngine",
    "create_engine",
]
