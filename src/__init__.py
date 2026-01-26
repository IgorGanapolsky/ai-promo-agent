"""
AI Promo Agent - Source Package

Contains revenue tracking and tax strategy implementations.
"""

# Revenue Tracking
from .revenue_tracker import RevenueTracker, AlpacaClient, DailyRevenue, Trade

# Tax Strategy
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

__all__ = [
    # Revenue Tracking
    "RevenueTracker",
    "AlpacaClient",
    "DailyRevenue",
    "Trade",
    # Tax Strategy
    "TaxStrategyEngine",
    "TaxConfig",
    "AlpacaTaxClient",
    "CostBasisTracker",
    "WashSaleDetector",
    "TaxLossHarvester",
    "CapitalGainsCalculator",
    "Section1256Handler",
    "TaxReportGenerator",
]
