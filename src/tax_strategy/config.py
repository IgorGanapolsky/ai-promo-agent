"""
Configuration for tax strategy module.

Contains tax rates, thresholds, and configurable options
for tax calculations and reporting.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Dict, Optional
import os


class TaxBracket2026(Enum):
    """2026 Federal Income Tax Brackets (Single Filer)."""
    BRACKET_10 = (Decimal("0"), Decimal("11600"), Decimal("0.10"))
    BRACKET_12 = (Decimal("11600"), Decimal("47150"), Decimal("0.12"))
    BRACKET_22 = (Decimal("47150"), Decimal("100525"), Decimal("0.22"))
    BRACKET_24 = (Decimal("100525"), Decimal("191950"), Decimal("0.24"))
    BRACKET_32 = (Decimal("191950"), Decimal("243725"), Decimal("0.32"))
    BRACKET_35 = (Decimal("243725"), Decimal("609350"), Decimal("0.35"))
    BRACKET_37 = (Decimal("609350"), Decimal("999999999"), Decimal("0.37"))


@dataclass
class TaxRates:
    """Federal tax rates for capital gains."""

    # Long-term capital gains rates (2026)
    long_term_rate_0_pct_threshold: Decimal = Decimal("47025")  # 0% up to this
    long_term_rate_15_pct_threshold: Decimal = Decimal("518900")  # 15% up to this
    long_term_rate_0: Decimal = Decimal("0.00")
    long_term_rate_15: Decimal = Decimal("0.15")
    long_term_rate_20: Decimal = Decimal("0.20")

    # Net Investment Income Tax (NIIT)
    niit_threshold: Decimal = Decimal("200000")  # Single filer
    niit_rate: Decimal = Decimal("0.038")

    # Short-term = ordinary income (use marginal rate)
    default_marginal_rate: Decimal = Decimal("0.24")  # Default assumption

    # Section 1256 blended rate (60% long-term, 40% short-term)
    section_1256_long_term_portion: Decimal = Decimal("0.60")
    section_1256_short_term_portion: Decimal = Decimal("0.40")

    # Loss limitations
    max_ordinary_income_deduction: Decimal = Decimal("3000")

    def get_long_term_rate(self, taxable_income: Decimal) -> Decimal:
        """Get long-term capital gains rate based on income."""
        if taxable_income <= self.long_term_rate_0_pct_threshold:
            return self.long_term_rate_0
        elif taxable_income <= self.long_term_rate_15_pct_threshold:
            return self.long_term_rate_15
        else:
            return self.long_term_rate_20

    def get_effective_section_1256_rate(
        self, marginal_rate: Optional[Decimal] = None
    ) -> Decimal:
        """
        Calculate effective tax rate for Section 1256 contracts.

        Uses 60% long-term rate + 40% short-term (marginal) rate.
        """
        lt_rate = self.long_term_rate_15  # Assume 15% bracket
        st_rate = marginal_rate or self.default_marginal_rate

        return (self.section_1256_long_term_portion * lt_rate +
                self.section_1256_short_term_portion * st_rate)


@dataclass
class WashSaleConfig:
    """Configuration for wash sale detection."""

    # IRS wash sale window
    wash_sale_window_days: int = 30  # 30 days before and after

    # Substantially identical securities
    # Options on the same underlying are considered substantially identical
    treat_options_as_identical_to_stock: bool = True
    treat_deep_itm_calls_as_stock: bool = True
    deep_itm_threshold_pct: Decimal = Decimal("0.20")  # 20% ITM

    # Cross-account tracking (if you have multiple accounts)
    track_cross_account: bool = True

    # Automatic basis adjustment
    auto_adjust_basis: bool = True


@dataclass
class TaxLossHarvestingConfig:
    """Configuration for tax-loss harvesting."""

    # Minimum loss to consider harvesting
    min_harvest_amount: Decimal = Decimal("100")

    # Tax rate assumptions for benefit calculation
    assumed_marginal_rate: Decimal = Decimal("0.24")
    assumed_long_term_rate: Decimal = Decimal("0.15")

    # Risk thresholds
    min_days_held_before_harvest: int = 0
    warn_if_close_to_long_term: bool = True
    days_warning_threshold: int = 30  # Warn if within 30 days of long-term

    # Wash sale avoidance
    wash_sale_avoidance_days: int = 31  # Wait 31 days before repurchasing

    # Replacement security suggestions
    suggest_replacements: bool = True
    max_replacement_suggestions: int = 3


@dataclass
class CostBasisConfig:
    """Configuration for cost basis tracking."""

    from .models import CostBasisMethod

    # Default method for matching lots
    default_method: str = "fifo"  # fifo, lifo, specific_id

    # Broker default (Alpaca uses FIFO)
    broker_default_method: str = "fifo"

    # Track specific lots
    enable_specific_id: bool = True

    # Include fees in cost basis
    include_commission_in_basis: bool = True
    include_fees_in_basis: bool = True


@dataclass
class ReportingConfig:
    """Configuration for tax report generation."""

    # Output formats
    output_directory: str = "./tax_reports"
    generate_csv: bool = True
    generate_pdf: bool = False
    generate_json: bool = True

    # Form 8949 settings
    form_8949_basis_reported_to_irs: bool = True  # Box A/D vs B/E

    # Summary options
    include_unrealized_summary: bool = True
    include_wash_sale_detail: bool = True
    include_harvest_opportunities: bool = True

    # Tax year
    tax_year: int = 2026


@dataclass
class AlpacaConfig:
    """Configuration for Alpaca API integration."""

    # API credentials (from environment)
    api_key: str = field(default_factory=lambda: os.environ.get("APCA_API_KEY_ID", ""))
    api_secret: str = field(
        default_factory=lambda: os.environ.get("APCA_API_SECRET_KEY", "")
    )

    # Environment
    paper_trading: bool = field(
        default_factory=lambda: os.environ.get("APCA_PAPER", "true").lower() == "true"
    )

    # Base URLs
    live_base_url: str = "https://api.alpaca.markets"
    paper_base_url: str = "https://paper-api.alpaca.markets"
    data_base_url: str = "https://data.alpaca.markets"

    # Sync settings
    sync_interval_minutes: int = 15
    historical_days_to_fetch: int = 365

    @property
    def base_url(self) -> str:
        """Get appropriate base URL based on environment."""
        return self.paper_base_url if self.paper_trading else self.live_base_url


@dataclass
class TaxConfig:
    """
    Master configuration for the tax strategy module.

    Aggregates all sub-configurations and provides a single
    entry point for configuration management.
    """

    tax_rates: TaxRates = field(default_factory=TaxRates)
    wash_sale: WashSaleConfig = field(default_factory=WashSaleConfig)
    tax_loss_harvesting: TaxLossHarvestingConfig = field(
        default_factory=TaxLossHarvestingConfig
    )
    cost_basis: CostBasisConfig = field(default_factory=CostBasisConfig)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)
    alpaca: AlpacaConfig = field(default_factory=AlpacaConfig)

    # User tax profile
    filing_status: str = "single"  # single, married_joint, married_separate, head
    estimated_taxable_income: Decimal = Decimal("100000")
    state_tax_rate: Decimal = Decimal("0.05")  # State capital gains rate

    # Prior year carryforward
    prior_year_loss_carryforward: Decimal = Decimal("0")

    def get_effective_marginal_rate(self) -> Decimal:
        """Calculate effective marginal rate including state tax."""
        federal = self.tax_rates.default_marginal_rate
        state = self.state_tax_rate
        # Simplified - doesn't account for SALT deduction limits
        return federal + state

    def get_effective_long_term_rate(self) -> Decimal:
        """Calculate effective long-term rate including state and NIIT."""
        federal_lt = self.tax_rates.get_long_term_rate(self.estimated_taxable_income)
        state = self.state_tax_rate
        niit = (
            self.tax_rates.niit_rate
            if self.estimated_taxable_income > self.tax_rates.niit_threshold
            else Decimal("0")
        )
        return federal_lt + state + niit

    @classmethod
    def from_env(cls) -> "TaxConfig":
        """Create configuration from environment variables."""
        config = cls()

        # Override from environment if set
        if os.environ.get("TAX_FILING_STATUS"):
            config.filing_status = os.environ["TAX_FILING_STATUS"]

        if os.environ.get("TAX_ESTIMATED_INCOME"):
            config.estimated_taxable_income = Decimal(
                os.environ["TAX_ESTIMATED_INCOME"]
            )

        if os.environ.get("TAX_STATE_RATE"):
            config.state_tax_rate = Decimal(os.environ["TAX_STATE_RATE"])

        if os.environ.get("TAX_LOSS_CARRYFORWARD"):
            config.prior_year_loss_carryforward = Decimal(
                os.environ["TAX_LOSS_CARRYFORWARD"]
            )

        return config
