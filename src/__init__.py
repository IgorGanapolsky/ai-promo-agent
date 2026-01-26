"""
AI Promo Agent - Source Package

Contains the tax strategy implementation and trading utilities.
"""

from .tax_strategy import (
    TaxStrategyEngine,
    TaxConfig,
    AlpacaTaxClient,
    CostBasisTracker,
    WashSaleDetector,
    TaxLossHarvester,
    CapitalGainsCalculator,
    Section1256Handler,
    TaxReportGenerator,
)

__version__ = "1.0.0"
