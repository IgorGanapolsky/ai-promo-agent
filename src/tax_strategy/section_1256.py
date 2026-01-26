"""
Section 1256 contract handling for tax purposes.

Section 1256 contracts receive special tax treatment:
- 60% long-term / 40% short-term capital gains regardless of holding period
- Mark-to-market at year end
- NOT subject to wash sale rules
- Reported on Form 6781
"""

from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional, Dict
from collections import defaultdict
import logging

from .models import (
    Section1256Contract,
    Trade,
    TaxLot,
    AssetClass,
    GainType,
)
from .config import TaxConfig

logger = logging.getLogger(__name__)


# Symbols that qualify for Section 1256 treatment
SECTION_1256_INDICES = {
    # Broad-based index options
    "SPX",  # S&P 500 Index
    "SPXW",  # S&P 500 Weekly Options
    "XSP",  # Mini-SPX
    "NDX",  # Nasdaq-100 Index
    "RUT",  # Russell 2000 Index
    "DJX",  # Dow Jones Industrial Average
    "OEX",  # S&P 100 Index
    "XEO",  # S&P 100 European Style
    "VIX",  # CBOE Volatility Index
    "RVX",  # Russell 2000 Volatility Index
    # Note: ETF options (SPY, QQQ, IWM) do NOT qualify
}


class Section1256Handler:
    """
    Handles Section 1256 contracts for special tax treatment.

    Section 1256 contracts include:
    - Regulated futures contracts
    - Foreign currency contracts
    - Non-equity options (index options)
    - Dealer equity options
    - Dealer securities futures contracts

    Benefits:
    - 60/40 tax treatment (60% long-term, 40% short-term)
    - Applies regardless of actual holding period
    - Can be advantageous for short-term traders
    - Mark-to-market at year end (recognize unrealized gains/losses)
    - Exempt from wash sale rules
    """

    def __init__(self, config: Optional[TaxConfig] = None):
        """
        Initialize the Section 1256 handler.

        Args:
            config: Tax configuration
        """
        self.config = config or TaxConfig()

        # Active Section 1256 positions
        self._contracts: Dict[str, List[Section1256Contract]] = defaultdict(list)

        # Closed contracts
        self._closed_contracts: List[Section1256Contract] = []

        # Year-end MTM records
        self._mtm_records: Dict[int, List[Dict]] = defaultdict(list)

    def is_section_1256(self, symbol: str, underlying: Optional[str] = None) -> bool:
        """
        Determine if a symbol qualifies for Section 1256 treatment.

        Args:
            symbol: The trading symbol
            underlying: The underlying symbol for options

        Returns:
            True if qualifies for Section 1256
        """
        # Check underlying for options
        check_symbol = underlying or symbol

        # Extract root symbol (remove option suffixes)
        root = self._extract_root_symbol(check_symbol)

        return root.upper() in SECTION_1256_INDICES

    def _extract_root_symbol(self, symbol: str) -> str:
        """Extract the root symbol from an option symbol."""
        # OCC format: ROOT + YYMMDD + C/P + STRIKE
        # Simple approach: return first non-digit characters
        root = ""
        for char in symbol:
            if char.isdigit():
                break
            root += char
        return root if root else symbol

    def add_contract(
        self,
        symbol: str,
        quantity: Decimal,
        entry_price: Decimal,
        entry_date: Optional[datetime] = None,
        underlying: Optional[str] = None,
    ) -> Section1256Contract:
        """
        Add a new Section 1256 contract position.

        Args:
            symbol: Contract symbol
            quantity: Number of contracts (negative for short)
            entry_price: Entry price per contract
            entry_date: Entry date
            underlying: Underlying symbol

        Returns:
            The created contract
        """
        if not self.is_section_1256(symbol, underlying):
            raise ValueError(f"{symbol} does not qualify for Section 1256 treatment")

        contract = Section1256Contract(
            symbol=symbol,
            contract_type="index_option",
            quantity=quantity,
            entry_price=entry_price,
            entry_date=entry_date or datetime.now(),
            tax_year=(entry_date or datetime.now()).year,
        )

        self._contracts[symbol].append(contract)

        logger.info(
            f"Added Section 1256 contract: {quantity} {symbol} @ ${entry_price}"
        )

        return contract

    def close_contract(
        self,
        contract_id: str,
        exit_price: Decimal,
        exit_date: Optional[datetime] = None,
    ) -> Dict[str, Decimal]:
        """
        Close a Section 1256 contract and calculate gain/loss.

        Args:
            contract_id: ID of contract to close
            exit_price: Exit price per contract
            exit_date: Exit date

        Returns:
            Dictionary with gain/loss breakdown
        """
        exit_date = exit_date or datetime.now()

        # Find the contract
        contract = None
        for symbol_contracts in self._contracts.values():
            for c in symbol_contracts:
                if c.id == contract_id:
                    contract = c
                    break

        if not contract:
            raise ValueError(f"Contract {contract_id} not found")

        # Calculate gain/loss
        multiplier = 100  # Standard option contract
        entry_value = contract.quantity * contract.entry_price * multiplier
        exit_value = contract.quantity * exit_price * multiplier

        # For long positions: exit - entry
        # For short positions: entry - exit (quantity is negative)
        gain_loss = exit_value - entry_value

        # Apply 60/40 split
        long_term_portion = gain_loss * Decimal("0.60")
        short_term_portion = gain_loss * Decimal("0.40")

        # Update contract
        contract.mtm_gain_loss = gain_loss
        contract.long_term_portion = long_term_portion
        contract.short_term_portion = short_term_portion

        # Move to closed
        self._closed_contracts.append(contract)
        for symbol_contracts in self._contracts.values():
            if contract in symbol_contracts:
                symbol_contracts.remove(contract)
                break

        logger.info(
            f"Closed Section 1256 contract {contract_id}: "
            f"Total ${gain_loss:.2f} (60% LT: ${long_term_portion:.2f}, "
            f"40% ST: ${short_term_portion:.2f})"
        )

        return {
            "total_gain_loss": gain_loss,
            "long_term_portion": long_term_portion,
            "short_term_portion": short_term_portion,
            "holding_period_days": (exit_date - contract.entry_date).days,
        }

    def perform_year_end_mtm(
        self,
        year_end_prices: Dict[str, Decimal],
        tax_year: Optional[int] = None,
    ) -> List[Dict]:
        """
        Perform year-end mark-to-market for all open Section 1256 contracts.

        Per IRS rules, Section 1256 contracts are marked to market at
        year end - unrealized gains/losses are recognized as if sold.

        Args:
            year_end_prices: Dictionary of symbol -> year-end price
            tax_year: Tax year (defaults to current year)

        Returns:
            List of MTM adjustments
        """
        tax_year = tax_year or datetime.now().year
        adjustments = []

        for symbol, contracts in self._contracts.items():
            if symbol not in year_end_prices:
                logger.warning(f"No year-end price for {symbol}, skipping MTM")
                continue

            year_end_price = year_end_prices[symbol]

            for contract in contracts:
                if contract.tax_year != tax_year:
                    continue

                # Calculate MTM gain/loss
                multiplier = 100
                entry_value = contract.quantity * contract.entry_price * multiplier
                year_end_value = contract.quantity * year_end_price * multiplier

                mtm_gain_loss = year_end_value - entry_value

                # Apply 60/40 split
                lt_portion = mtm_gain_loss * Decimal("0.60")
                st_portion = mtm_gain_loss * Decimal("0.40")

                # Update contract
                contract.year_end_value = year_end_value
                contract.mtm_gain_loss = mtm_gain_loss
                contract.long_term_portion = lt_portion
                contract.short_term_portion = st_portion

                adjustment = {
                    "contract_id": contract.id,
                    "symbol": symbol,
                    "quantity": contract.quantity,
                    "entry_price": contract.entry_price,
                    "year_end_price": year_end_price,
                    "mtm_gain_loss": mtm_gain_loss,
                    "long_term_portion": lt_portion,
                    "short_term_portion": st_portion,
                    "tax_year": tax_year,
                }

                adjustments.append(adjustment)
                self._mtm_records[tax_year].append(adjustment)

                logger.info(
                    f"MTM for {symbol}: ${mtm_gain_loss:.2f} "
                    f"(60% LT: ${lt_portion:.2f}, 40% ST: ${st_portion:.2f})"
                )

                # Reset cost basis for next year
                contract.entry_price = year_end_price
                contract.tax_year = tax_year + 1

        return adjustments

    def get_form_6781_data(self, tax_year: Optional[int] = None) -> Dict:
        """
        Get data for IRS Form 6781.

        Form 6781 is used to report gains/losses from
        Section 1256 contracts and straddles.

        Args:
            tax_year: Tax year to report

        Returns:
            Dictionary with Form 6781 data
        """
        tax_year = tax_year or datetime.now().year

        # Aggregate all Section 1256 gains/losses
        total_gain_loss = Decimal("0")

        # From closed contracts
        for contract in self._closed_contracts:
            if contract.tax_year == tax_year:
                total_gain_loss += contract.mtm_gain_loss

        # From MTM adjustments
        for adjustment in self._mtm_records.get(tax_year, []):
            total_gain_loss += adjustment["mtm_gain_loss"]

        # Calculate 60/40 split
        long_term = total_gain_loss * Decimal("0.60")
        short_term = total_gain_loss * Decimal("0.40")

        return {
            "tax_year": tax_year,
            "line_1": "Section 1256 contracts",
            "aggregate_profit_or_loss": total_gain_loss,  # Line 7
            "short_term_portion": short_term,  # Line 8 (40%)
            "long_term_portion": long_term,  # Line 9 (60%)
            "contracts": [
                {
                    "symbol": c.symbol,
                    "gain_loss": c.mtm_gain_loss,
                }
                for c in self._closed_contracts
                if c.tax_year == tax_year
            ],
            "mtm_adjustments": self._mtm_records.get(tax_year, []),
        }

    def calculate_tax_benefit(
        self,
        gain_loss: Decimal,
        marginal_rate: Optional[Decimal] = None,
    ) -> Dict[str, Decimal]:
        """
        Calculate tax benefit of Section 1256 treatment vs regular.

        Args:
            gain_loss: The gain or loss amount
            marginal_rate: Short-term (marginal) tax rate

        Returns:
            Comparison of tax treatment
        """
        marginal_rate = marginal_rate or self.config.tax_rates.default_marginal_rate
        lt_rate = self.config.tax_rates.long_term_rate_15  # Assume 15%

        # Regular short-term treatment (all at marginal rate)
        regular_tax = gain_loss * marginal_rate

        # Section 1256 treatment (60/40)
        lt_portion = gain_loss * Decimal("0.60")
        st_portion = gain_loss * Decimal("0.40")

        section_1256_tax = (lt_portion * lt_rate) + (st_portion * marginal_rate)

        benefit = regular_tax - section_1256_tax

        return {
            "gain_loss": gain_loss,
            "regular_short_term_tax": regular_tax,
            "section_1256_tax": section_1256_tax,
            "tax_savings": benefit,
            "effective_rate_regular": marginal_rate,
            "effective_rate_1256": section_1256_tax / gain_loss if gain_loss else Decimal("0"),
        }

    def get_open_contracts(self, symbol: Optional[str] = None) -> List[Section1256Contract]:
        """
        Get open Section 1256 contracts.

        Args:
            symbol: Filter to specific symbol

        Returns:
            List of open contracts
        """
        if symbol:
            return list(self._contracts.get(symbol, []))

        all_contracts = []
        for contracts in self._contracts.values():
            all_contracts.extend(contracts)
        return all_contracts

    def get_closed_contracts(self, tax_year: Optional[int] = None) -> List[Section1256Contract]:
        """
        Get closed Section 1256 contracts.

        Args:
            tax_year: Filter to specific year

        Returns:
            List of closed contracts
        """
        if tax_year:
            return [
                c for c in self._closed_contracts
                if c.tax_year == tax_year
            ]
        return list(self._closed_contracts)

    def export_contracts(self) -> List[Dict]:
        """
        Export all contracts for persistence.

        Returns:
            List of contract dictionaries
        """
        all_contracts = self.get_open_contracts() + self._closed_contracts

        return [
            {
                "id": c.id,
                "symbol": c.symbol,
                "contract_type": c.contract_type,
                "quantity": str(c.quantity),
                "entry_price": str(c.entry_price),
                "entry_date": c.entry_date.isoformat(),
                "year_end_value": str(c.year_end_value),
                "mtm_gain_loss": str(c.mtm_gain_loss),
                "long_term_portion": str(c.long_term_portion),
                "short_term_portion": str(c.short_term_portion),
                "tax_year": c.tax_year,
            }
            for c in all_contracts
        ]
