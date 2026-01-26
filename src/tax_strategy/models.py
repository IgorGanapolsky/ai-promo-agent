"""
Data models for tax strategy tracking.

These models represent the core entities used for tax calculations,
including tax lots, trades, positions, wash sales, and capital gains.
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import Optional, List
import uuid


class AssetClass(Enum):
    """Asset class for tax treatment determination."""
    EQUITY = "equity"
    EQUITY_OPTION = "equity_option"
    INDEX_OPTION = "index_option"  # Section 1256
    FUTURE = "future"  # Section 1256
    CRYPTO = "crypto"


class OptionType(Enum):
    """Option contract type."""
    CALL = "call"
    PUT = "put"


class PositionSide(Enum):
    """Position direction."""
    LONG = "long"
    SHORT = "short"


class OrderSide(Enum):
    """Order side."""
    BUY = "buy"
    SELL = "sell"
    BUY_TO_OPEN = "buy_to_open"
    BUY_TO_CLOSE = "buy_to_close"
    SELL_TO_OPEN = "sell_to_open"
    SELL_TO_CLOSE = "sell_to_close"


class CostBasisMethod(Enum):
    """Cost basis calculation method."""
    FIFO = "fifo"  # First In, First Out
    LIFO = "lifo"  # Last In, First Out
    SPECIFIC_ID = "specific_id"  # Specific identification
    AVERAGE = "average"  # Average cost (mutual funds only)


class GainType(Enum):
    """Capital gain type for tax purposes."""
    SHORT_TERM = "short_term"  # Held <= 1 year
    LONG_TERM = "long_term"  # Held > 1 year
    SECTION_1256 = "section_1256"  # 60% long-term, 40% short-term


@dataclass
class TaxLot:
    """
    Represents a single tax lot for cost basis tracking.

    A tax lot is created each time shares/contracts are acquired
    and tracks the cost basis for that specific acquisition.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str = ""
    asset_class: AssetClass = AssetClass.EQUITY

    # Position details
    quantity: Decimal = Decimal("0")
    remaining_quantity: Decimal = Decimal("0")
    cost_per_unit: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")

    # Option-specific fields
    option_type: Optional[OptionType] = None
    strike_price: Optional[Decimal] = None
    expiration_date: Optional[date] = None
    underlying_symbol: Optional[str] = None
    contract_multiplier: int = 100  # Standard options = 100 shares

    # Dates
    acquisition_date: datetime = field(default_factory=datetime.now)

    # Wash sale adjustments
    wash_sale_adjustment: Decimal = Decimal("0")
    wash_sale_disallowed_loss: Decimal = Decimal("0")
    adjusted_cost_basis: Decimal = Decimal("0")

    # Tracking
    order_id: Optional[str] = None
    is_closed: bool = False
    closed_date: Optional[datetime] = None

    def __post_init__(self):
        """Calculate adjusted cost basis after initialization."""
        if self.adjusted_cost_basis == Decimal("0"):
            self.adjusted_cost_basis = self.total_cost + self.wash_sale_adjustment
        if self.remaining_quantity == Decimal("0"):
            self.remaining_quantity = self.quantity

    @property
    def holding_period_days(self) -> int:
        """Calculate days held from acquisition."""
        end_date = self.closed_date or datetime.now()
        return (end_date - self.acquisition_date).days

    @property
    def is_long_term(self) -> bool:
        """Determine if position qualifies for long-term capital gains."""
        return self.holding_period_days > 365

    @property
    def gain_type(self) -> GainType:
        """Determine the gain type for tax purposes."""
        if self.asset_class == AssetClass.INDEX_OPTION:
            return GainType.SECTION_1256
        return GainType.LONG_TERM if self.is_long_term else GainType.SHORT_TERM


@dataclass
class Trade:
    """
    Represents a completed trade transaction.

    Captures all relevant details for tax reporting including
    proceeds, cost basis, and gain/loss calculations.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str = ""
    asset_class: AssetClass = AssetClass.EQUITY

    # Trade details
    side: OrderSide = OrderSide.SELL
    quantity: Decimal = Decimal("0")
    price: Decimal = Decimal("0")
    proceeds: Decimal = Decimal("0")

    # Cost basis
    cost_basis: Decimal = Decimal("0")
    adjusted_cost_basis: Decimal = Decimal("0")

    # Gain/Loss
    realized_gain_loss: Decimal = Decimal("0")
    gain_type: GainType = GainType.SHORT_TERM

    # Option-specific
    option_type: Optional[OptionType] = None
    strike_price: Optional[Decimal] = None
    expiration_date: Optional[date] = None
    underlying_symbol: Optional[str] = None

    # Dates
    trade_date: datetime = field(default_factory=datetime.now)
    settlement_date: Optional[datetime] = None
    acquisition_date: Optional[datetime] = None

    # Wash sale
    is_wash_sale: bool = False
    wash_sale_disallowed: Decimal = Decimal("0")

    # Fees
    commission: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")

    # Linking
    order_id: Optional[str] = None
    tax_lot_ids: List[str] = field(default_factory=list)

    def __post_init__(self):
        """Calculate realized gain/loss after initialization."""
        if self.realized_gain_loss == Decimal("0"):
            net_proceeds = self.proceeds - self.commission - self.fees
            self.realized_gain_loss = net_proceeds - self.adjusted_cost_basis


@dataclass
class Position:
    """
    Represents a current open position.

    Aggregates multiple tax lots for the same symbol and tracks
    overall position metrics including unrealized gains.
    """
    symbol: str = ""
    asset_class: AssetClass = AssetClass.EQUITY
    side: PositionSide = PositionSide.LONG

    # Quantities
    quantity: Decimal = Decimal("0")

    # Cost and value
    average_cost: Decimal = Decimal("0")
    total_cost_basis: Decimal = Decimal("0")
    current_price: Decimal = Decimal("0")
    market_value: Decimal = Decimal("0")

    # Unrealized gains
    unrealized_gain_loss: Decimal = Decimal("0")
    unrealized_gain_loss_pct: Decimal = Decimal("0")

    # Option-specific
    option_type: Optional[OptionType] = None
    strike_price: Optional[Decimal] = None
    expiration_date: Optional[date] = None
    underlying_symbol: Optional[str] = None

    # Tax lots
    tax_lots: List[TaxLot] = field(default_factory=list)

    # For tax-loss harvesting
    days_to_long_term: Optional[int] = None
    potential_harvest_value: Decimal = Decimal("0")

    def calculate_unrealized(self):
        """Calculate unrealized gain/loss based on current price."""
        self.market_value = self.quantity * self.current_price
        self.unrealized_gain_loss = self.market_value - self.total_cost_basis
        if self.total_cost_basis != Decimal("0"):
            self.unrealized_gain_loss_pct = (
                self.unrealized_gain_loss / self.total_cost_basis * 100
            )


@dataclass
class WashSale:
    """
    Represents a wash sale violation and adjustment.

    Tracks when a loss is disallowed due to purchasing a
    substantially identical security within 30 days.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # The sale that triggered the wash sale
    sale_trade_id: str = ""
    sale_symbol: str = ""
    sale_date: datetime = field(default_factory=datetime.now)
    sale_quantity: Decimal = Decimal("0")

    # The loss that was disallowed
    disallowed_loss: Decimal = Decimal("0")

    # The replacement purchase
    replacement_trade_id: str = ""
    replacement_date: datetime = field(default_factory=datetime.now)
    replacement_quantity: Decimal = Decimal("0")

    # The tax lot with adjusted basis
    adjusted_tax_lot_id: str = ""
    basis_adjustment: Decimal = Decimal("0")

    # Holding period adjustment
    original_acquisition_date: Optional[datetime] = None

    # Status
    is_resolved: bool = False
    resolved_date: Optional[datetime] = None


@dataclass
class CapitalGain:
    """
    Represents a capital gain or loss for tax reporting.

    Used for generating Form 8949 and Schedule D entries.
    """
    # IRS Form 8949 fields
    description: str = ""  # Description of property
    date_acquired: Optional[date] = None  # Column (b)
    date_sold: Optional[date] = None  # Column (c)
    proceeds: Decimal = Decimal("0")  # Column (d)
    cost_basis: Decimal = Decimal("0")  # Column (e)
    adjustment_code: str = ""  # Column (f) - e.g., "W" for wash sale
    adjustment_amount: Decimal = Decimal("0")  # Column (g)
    gain_or_loss: Decimal = Decimal("0")  # Column (h)

    # Classification
    gain_type: GainType = GainType.SHORT_TERM
    form_8949_box: str = ""  # A, B, C (short-term) or D, E, F (long-term)

    # Linking
    trade_id: str = ""
    tax_lot_id: str = ""


@dataclass
class TaxHarvestOpportunity:
    """
    Represents a tax-loss harvesting opportunity.

    Identifies positions with unrealized losses that could be
    harvested to offset capital gains.
    """
    symbol: str = ""
    position_id: str = ""

    # Current position
    quantity: Decimal = Decimal("0")
    cost_basis: Decimal = Decimal("0")
    current_value: Decimal = Decimal("0")
    unrealized_loss: Decimal = Decimal("0")

    # Tax benefit analysis
    estimated_tax_savings: Decimal = Decimal("0")
    gain_type_if_harvested: GainType = GainType.SHORT_TERM

    # Risk factors
    days_held: int = 0
    days_to_long_term: Optional[int] = None
    wash_sale_risk: bool = False
    wash_sale_lockout_until: Optional[date] = None

    # Recommendations
    harvest_recommended: bool = False
    recommendation_reason: str = ""

    # Replacement suggestions
    replacement_symbols: List[str] = field(default_factory=list)


@dataclass
class Section1256Contract:
    """
    Represents a Section 1256 contract for special tax treatment.

    Section 1256 contracts include regulated futures contracts,
    foreign currency contracts, non-equity options, dealer equity
    options, and dealer securities futures contracts.

    These receive 60% long-term / 40% short-term treatment regardless
    of holding period and are marked-to-market at year end.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str = ""
    contract_type: str = ""  # "index_option", "future", etc.

    # Position
    quantity: Decimal = Decimal("0")
    entry_price: Decimal = Decimal("0")

    # Mark-to-market
    year_end_value: Decimal = Decimal("0")
    mtm_gain_loss: Decimal = Decimal("0")

    # 60/40 split
    long_term_portion: Decimal = Decimal("0")  # 60%
    short_term_portion: Decimal = Decimal("0")  # 40%

    # For Form 6781
    aggregate_profit_loss: Decimal = Decimal("0")

    # Dates
    entry_date: datetime = field(default_factory=datetime.now)
    tax_year: int = field(default_factory=lambda: datetime.now().year)

    def calculate_60_40_split(self):
        """Calculate the 60/40 long-term/short-term split."""
        self.long_term_portion = self.mtm_gain_loss * Decimal("0.60")
        self.short_term_portion = self.mtm_gain_loss * Decimal("0.40")


@dataclass
class IronCondor:
    """
    Represents an iron condor options strategy for tax tracking.

    An iron condor consists of:
    - Short put (lower strike)
    - Long put (lowest strike)
    - Short call (higher strike)
    - Long call (highest strike)

    All legs share the same expiration date.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    underlying_symbol: str = ""
    expiration_date: Optional[date] = None

    # The four legs
    long_put_strike: Decimal = Decimal("0")
    short_put_strike: Decimal = Decimal("0")
    short_call_strike: Decimal = Decimal("0")
    long_call_strike: Decimal = Decimal("0")

    # Premiums
    net_credit: Decimal = Decimal("0")  # Credit received
    max_profit: Decimal = Decimal("0")  # = net credit
    max_loss: Decimal = Decimal("0")  # Width of spread - net credit

    # Tax lot tracking for each leg
    long_put_lot_id: Optional[str] = None
    short_put_lot_id: Optional[str] = None
    short_call_lot_id: Optional[str] = None
    long_call_lot_id: Optional[str] = None

    # Status
    is_open: bool = True
    opened_date: datetime = field(default_factory=datetime.now)
    closed_date: Optional[datetime] = None

    # Realized P&L
    realized_pnl: Decimal = Decimal("0")

    # For straddle rules
    is_mixed_straddle: bool = False  # True if any leg is Section 1256


@dataclass
class TaxSummary:
    """
    Annual tax summary for reporting.

    Aggregates all capital gains, losses, and carryforwards
    for a given tax year.
    """
    tax_year: int = field(default_factory=lambda: datetime.now().year)

    # Short-term
    short_term_gains: Decimal = Decimal("0")
    short_term_losses: Decimal = Decimal("0")
    net_short_term: Decimal = Decimal("0")

    # Long-term
    long_term_gains: Decimal = Decimal("0")
    long_term_losses: Decimal = Decimal("0")
    net_long_term: Decimal = Decimal("0")

    # Section 1256
    section_1256_gains: Decimal = Decimal("0")
    section_1256_losses: Decimal = Decimal("0")
    net_section_1256: Decimal = Decimal("0")

    # Totals
    total_gains: Decimal = Decimal("0")
    total_losses: Decimal = Decimal("0")
    net_capital_gain_loss: Decimal = Decimal("0")

    # Wash sales
    total_wash_sale_disallowed: Decimal = Decimal("0")

    # Carryforward
    loss_carryforward_used: Decimal = Decimal("0")
    loss_carryforward_remaining: Decimal = Decimal("0")

    # Ordinary income deduction (max $3,000)
    ordinary_income_deduction: Decimal = Decimal("0")

    # Estimated tax liability
    estimated_short_term_tax: Decimal = Decimal("0")
    estimated_long_term_tax: Decimal = Decimal("0")
    estimated_total_tax: Decimal = Decimal("0")
