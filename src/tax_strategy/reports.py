"""
Tax report generation for IRS forms and analysis.

Generates reports compatible with IRS Form 8949, Schedule D,
Form 6781, and provides analysis summaries.
"""

from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional, Dict, Any
from pathlib import Path
import csv
import json
import logging

from .models import (
    CapitalGain,
    TaxSummary,
    WashSale,
    TaxHarvestOpportunity,
    GainType,
)
from .config import ReportingConfig, TaxConfig
from .capital_gains import CapitalGainsCalculator
from .wash_sale import WashSaleDetector
from .section_1256 import Section1256Handler
from .tax_loss_harvesting import TaxLossHarvester

logger = logging.getLogger(__name__)


class TaxReportGenerator:
    """
    Generates comprehensive tax reports for trading activity.

    Supports:
    - Form 8949 (Sales and Other Dispositions of Capital Assets)
    - Schedule D (Capital Gains and Losses)
    - Form 6781 (Section 1256 Contracts and Straddles)
    - Wash sale reports
    - Tax-loss harvesting analysis
    - Year-end summary
    """

    def __init__(
        self,
        config: Optional[ReportingConfig] = None,
        tax_config: Optional[TaxConfig] = None,
        capital_gains_calc: Optional[CapitalGainsCalculator] = None,
        wash_sale_detector: Optional[WashSaleDetector] = None,
        section_1256_handler: Optional[Section1256Handler] = None,
        tax_loss_harvester: Optional[TaxLossHarvester] = None,
    ):
        """
        Initialize the report generator.

        Args:
            config: Reporting configuration
            tax_config: Overall tax configuration
            capital_gains_calc: Capital gains calculator
            wash_sale_detector: Wash sale detector
            section_1256_handler: Section 1256 handler
            tax_loss_harvester: Tax loss harvester
        """
        self.config = config or ReportingConfig()
        self.tax_config = tax_config or TaxConfig()
        self.capital_gains = capital_gains_calc or CapitalGainsCalculator()
        self.wash_sales = wash_sale_detector or WashSaleDetector()
        self.section_1256 = section_1256_handler or Section1256Handler()
        self.harvester = tax_loss_harvester or TaxLossHarvester()

        # Ensure output directory exists
        Path(self.config.output_directory).mkdir(parents=True, exist_ok=True)

    def generate_form_8949(
        self,
        tax_year: Optional[int] = None,
        output_format: str = "csv",
    ) -> Dict[str, Any]:
        """
        Generate Form 8949 data.

        Form 8949 reports sales and dispositions of capital assets.
        Part I: Short-term transactions
        Part II: Long-term transactions

        Args:
            tax_year: Tax year to report
            output_format: Output format (csv, json)

        Returns:
            Dictionary with form data and file paths
        """
        tax_year = tax_year or self.config.tax_year
        gains_by_box = self.capital_gains.get_form_8949_data(tax_year)

        result = {
            "tax_year": tax_year,
            "form": "8949",
            "generated_at": datetime.now().isoformat(),
            "parts": {},
            "files": [],
        }

        # Part I: Short-term (Boxes A, B, C)
        short_term_boxes = ["A", "B", "C"]
        part_i_gains = []
        for box in short_term_boxes:
            part_i_gains.extend(gains_by_box.get(box, []))

        result["parts"]["part_i"] = {
            "description": "Short-Term Capital Gains and Losses",
            "transactions": len(part_i_gains),
            "total_proceeds": sum(g.proceeds for g in part_i_gains),
            "total_cost": sum(g.cost_basis for g in part_i_gains),
            "total_adjustments": sum(g.adjustment_amount for g in part_i_gains),
            "total_gain_loss": sum(g.gain_or_loss for g in part_i_gains),
        }

        # Part II: Long-term (Boxes D, E, F)
        long_term_boxes = ["D", "E", "F"]
        part_ii_gains = []
        for box in long_term_boxes:
            part_ii_gains.extend(gains_by_box.get(box, []))

        result["parts"]["part_ii"] = {
            "description": "Long-Term Capital Gains and Losses",
            "transactions": len(part_ii_gains),
            "total_proceeds": sum(g.proceeds for g in part_ii_gains),
            "total_cost": sum(g.cost_basis for g in part_ii_gains),
            "total_adjustments": sum(g.adjustment_amount for g in part_ii_gains),
            "total_gain_loss": sum(g.gain_or_loss for g in part_ii_gains),
        }

        # Generate output files
        if self.config.generate_csv:
            for box, gains in gains_by_box.items():
                if gains:
                    filepath = self._write_form_8949_csv(box, gains, tax_year)
                    result["files"].append(filepath)

        if self.config.generate_json:
            filepath = self._write_form_8949_json(gains_by_box, tax_year)
            result["files"].append(filepath)

        return result

    def _write_form_8949_csv(
        self,
        box: str,
        gains: List[CapitalGain],
        tax_year: int,
    ) -> str:
        """Write Form 8949 data to CSV file."""
        filename = f"form_8949_box_{box}_{tax_year}.csv"
        filepath = Path(self.config.output_directory) / filename

        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)

            # Header row
            writer.writerow([
                "(a) Description of property",
                "(b) Date acquired",
                "(c) Date sold",
                "(d) Proceeds",
                "(e) Cost or other basis",
                "(f) Adjustment code",
                "(g) Adjustment amount",
                "(h) Gain or (loss)",
            ])

            # Data rows
            for gain in gains:
                writer.writerow([
                    gain.description,
                    gain.date_acquired.strftime("%m/%d/%Y") if gain.date_acquired else "VARIOUS",
                    gain.date_sold.strftime("%m/%d/%Y") if gain.date_sold else "",
                    f"{gain.proceeds:.2f}",
                    f"{gain.cost_basis:.2f}",
                    gain.adjustment_code,
                    f"{gain.adjustment_amount:.2f}" if gain.adjustment_amount else "",
                    f"{gain.gain_or_loss:.2f}",
                ])

            # Totals row
            writer.writerow([
                "TOTALS",
                "",
                "",
                f"{sum(g.proceeds for g in gains):.2f}",
                f"{sum(g.cost_basis for g in gains):.2f}",
                "",
                f"{sum(g.adjustment_amount for g in gains):.2f}",
                f"{sum(g.gain_or_loss for g in gains):.2f}",
            ])

        logger.info(f"Generated Form 8949 Box {box}: {filepath}")
        return str(filepath)

    def _write_form_8949_json(
        self,
        gains_by_box: Dict[str, List[CapitalGain]],
        tax_year: int,
    ) -> str:
        """Write Form 8949 data to JSON file."""
        filename = f"form_8949_{tax_year}.json"
        filepath = Path(self.config.output_directory) / filename

        data = {
            "form": "8949",
            "tax_year": tax_year,
            "generated_at": datetime.now().isoformat(),
            "boxes": {},
        }

        for box, gains in gains_by_box.items():
            data["boxes"][box] = [
                {
                    "description": g.description,
                    "date_acquired": g.date_acquired.isoformat() if g.date_acquired else None,
                    "date_sold": g.date_sold.isoformat() if g.date_sold else None,
                    "proceeds": str(g.proceeds),
                    "cost_basis": str(g.cost_basis),
                    "adjustment_code": g.adjustment_code,
                    "adjustment_amount": str(g.adjustment_amount),
                    "gain_or_loss": str(g.gain_or_loss),
                }
                for g in gains
            ]

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Generated Form 8949 JSON: {filepath}")
        return str(filepath)

    def generate_schedule_d(self, tax_year: Optional[int] = None) -> Dict[str, Any]:
        """
        Generate Schedule D (Capital Gains and Losses) summary.

        Args:
            tax_year: Tax year to report

        Returns:
            Schedule D data dictionary
        """
        tax_year = tax_year or self.config.tax_year
        summary = self.capital_gains.get_tax_summary(
            tax_year=tax_year,
            prior_carryforward=self.tax_config.prior_year_loss_carryforward,
        )

        # Get Form 6781 data for Section 1256
        form_6781 = self.section_1256.get_form_6781_data(tax_year)

        schedule_d = {
            "form": "Schedule D",
            "tax_year": tax_year,
            "generated_at": datetime.now().isoformat(),
            "part_i": {
                "description": "Short-Term Capital Gains and Losses",
                "line_1": {
                    "description": "Short-term totals from Form 8949 Box A",
                    "proceeds": str(summary.short_term_gains + summary.short_term_losses),
                    "cost": str(summary.short_term_gains),
                    "adjustments": "0",
                    "gain_loss": str(summary.net_short_term),
                },
                "line_4": {
                    "description": "Short-term gain from Form 6781",
                    "amount": str(form_6781.get("short_term_portion", 0)),
                },
                "line_7": {
                    "description": "Net short-term capital gain or (loss)",
                    "amount": str(summary.net_short_term + form_6781.get("short_term_portion", Decimal("0"))),
                },
            },
            "part_ii": {
                "description": "Long-Term Capital Gains and Losses",
                "line_8": {
                    "description": "Long-term totals from Form 8949 Box D",
                    "proceeds": str(summary.long_term_gains + summary.long_term_losses),
                    "cost": str(summary.long_term_gains),
                    "adjustments": "0",
                    "gain_loss": str(summary.net_long_term),
                },
                "line_11": {
                    "description": "Long-term gain from Form 6781",
                    "amount": str(form_6781.get("long_term_portion", 0)),
                },
                "line_15": {
                    "description": "Net long-term capital gain or (loss)",
                    "amount": str(summary.net_long_term + form_6781.get("long_term_portion", Decimal("0"))),
                },
            },
            "part_iii": {
                "description": "Summary",
                "line_16": {
                    "description": "Combine lines 7 and 15",
                    "amount": str(summary.net_capital_gain_loss),
                },
                "line_21": {
                    "description": "Loss limitation ($3,000)",
                    "amount": str(summary.ordinary_income_deduction),
                },
            },
            "carryforward": {
                "prior_year_used": str(summary.loss_carryforward_used),
                "remaining": str(summary.loss_carryforward_remaining),
            },
        }

        # Write to file
        if self.config.generate_json:
            filename = f"schedule_d_{tax_year}.json"
            filepath = Path(self.config.output_directory) / filename
            with open(filepath, "w") as f:
                json.dump(schedule_d, f, indent=2)
            schedule_d["file"] = str(filepath)

        return schedule_d

    def generate_form_6781(self, tax_year: Optional[int] = None) -> Dict[str, Any]:
        """
        Generate Form 6781 (Section 1256 Contracts and Straddles).

        Args:
            tax_year: Tax year to report

        Returns:
            Form 6781 data dictionary
        """
        tax_year = tax_year or self.config.tax_year
        data = self.section_1256.get_form_6781_data(tax_year)

        form_6781 = {
            "form": "6781",
            "tax_year": tax_year,
            "generated_at": datetime.now().isoformat(),
            "part_i": {
                "description": "Section 1256 Contracts Marked to Market",
                "line_1": "Gains from section 1256 contracts",
                "line_2": "Losses from section 1256 contracts",
                "line_7": {
                    "description": "Net gain or (loss)",
                    "amount": str(data["aggregate_profit_or_loss"]),
                },
                "line_8": {
                    "description": "Short-term capital gain or (loss) (40%)",
                    "amount": str(data["short_term_portion"]),
                },
                "line_9": {
                    "description": "Long-term capital gain or (loss) (60%)",
                    "amount": str(data["long_term_portion"]),
                },
            },
            "contracts": data.get("contracts", []),
            "mtm_adjustments": data.get("mtm_adjustments", []),
        }

        # Write to file
        if self.config.generate_json:
            filename = f"form_6781_{tax_year}.json"
            filepath = Path(self.config.output_directory) / filename
            with open(filepath, "w") as f:
                json.dump(form_6781, f, indent=2, default=str)
            form_6781["file"] = str(filepath)

        return form_6781

    def generate_wash_sale_report(
        self,
        tax_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Generate wash sale disclosure report.

        Args:
            tax_year: Tax year to report

        Returns:
            Wash sale report dictionary
        """
        tax_year = tax_year or self.config.tax_year
        wash_sales = self.wash_sales.get_wash_sales(tax_year=tax_year)
        total_disallowed = self.wash_sales.get_total_disallowed_losses(tax_year)

        report = {
            "report": "Wash Sale Disclosure",
            "tax_year": tax_year,
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "total_wash_sales": len(wash_sales),
                "total_disallowed_losses": str(total_disallowed),
            },
            "wash_sales": self.wash_sales.export_wash_sales(),
        }

        # Write to file
        if self.config.generate_json:
            filename = f"wash_sales_{tax_year}.json"
            filepath = Path(self.config.output_directory) / filename
            with open(filepath, "w") as f:
                json.dump(report, f, indent=2)
            report["file"] = str(filepath)

        if self.config.generate_csv:
            filename = f"wash_sales_{tax_year}.csv"
            filepath = Path(self.config.output_directory) / filename

            with open(filepath, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Sale Date",
                    "Symbol",
                    "Quantity",
                    "Disallowed Loss",
                    "Replacement Date",
                    "Basis Adjustment",
                ])

                for ws in wash_sales:
                    writer.writerow([
                        ws.sale_date.strftime("%m/%d/%Y"),
                        ws.sale_symbol,
                        str(ws.sale_quantity),
                        f"{ws.disallowed_loss:.2f}",
                        ws.replacement_date.strftime("%m/%d/%Y"),
                        f"{ws.basis_adjustment:.2f}",
                    ])

            report["csv_file"] = str(filepath)

        return report

    def generate_harvest_opportunities_report(
        self,
        opportunities: List[TaxHarvestOpportunity],
    ) -> Dict[str, Any]:
        """
        Generate tax-loss harvesting opportunities report.

        Args:
            opportunities: List of harvesting opportunities

        Returns:
            Opportunities report dictionary
        """
        summary = self.harvester.calculate_harvest_summary(opportunities)

        report = {
            "report": "Tax-Loss Harvesting Opportunities",
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "total_opportunities": summary["total_opportunities"],
                "recommended_count": summary["recommended_count"],
                "total_harvestable_loss": str(summary["total_harvestable_loss"]),
                "estimated_tax_savings": str(summary["estimated_tax_savings"]),
            },
            "opportunities": [
                {
                    "symbol": opp.symbol,
                    "quantity": str(opp.quantity),
                    "cost_basis": str(opp.cost_basis),
                    "current_value": str(opp.current_value),
                    "unrealized_loss": str(opp.unrealized_loss),
                    "estimated_tax_savings": str(opp.estimated_tax_savings),
                    "days_held": opp.days_held,
                    "days_to_long_term": opp.days_to_long_term,
                    "wash_sale_risk": opp.wash_sale_risk,
                    "harvest_recommended": opp.harvest_recommended,
                    "recommendation": opp.recommendation_reason,
                    "replacement_suggestions": opp.replacement_symbols,
                }
                for opp in opportunities
            ],
        }

        # Write to file
        if self.config.generate_json:
            filename = f"harvest_opportunities_{datetime.now().strftime('%Y%m%d')}.json"
            filepath = Path(self.config.output_directory) / filename
            with open(filepath, "w") as f:
                json.dump(report, f, indent=2)
            report["file"] = str(filepath)

        return report

    def generate_year_end_summary(
        self,
        tax_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Generate comprehensive year-end tax summary.

        Args:
            tax_year: Tax year to summarize

        Returns:
            Comprehensive summary dictionary
        """
        tax_year = tax_year or self.config.tax_year

        # Get tax summary
        summary = self.capital_gains.get_tax_summary(
            tax_year=tax_year,
            prior_carryforward=self.tax_config.prior_year_loss_carryforward,
        )

        # Get Form 6781 data
        form_6781 = self.section_1256.get_form_6781_data(tax_year)

        # Get wash sale total
        wash_sale_total = self.wash_sales.get_total_disallowed_losses(tax_year)

        year_end = {
            "report": "Year-End Tax Summary",
            "tax_year": tax_year,
            "generated_at": datetime.now().isoformat(),
            "taxpayer_profile": {
                "filing_status": self.tax_config.filing_status,
                "estimated_income": str(self.tax_config.estimated_taxable_income),
                "marginal_rate": str(self.tax_config.get_effective_marginal_rate()),
                "long_term_rate": str(self.tax_config.get_effective_long_term_rate()),
            },
            "capital_gains_summary": {
                "short_term": {
                    "gains": str(summary.short_term_gains),
                    "losses": str(summary.short_term_losses),
                    "net": str(summary.net_short_term),
                },
                "long_term": {
                    "gains": str(summary.long_term_gains),
                    "losses": str(summary.long_term_losses),
                    "net": str(summary.net_long_term),
                },
                "section_1256": {
                    "total": str(form_6781.get("aggregate_profit_or_loss", 0)),
                    "long_term_portion": str(form_6781.get("long_term_portion", 0)),
                    "short_term_portion": str(form_6781.get("short_term_portion", 0)),
                },
                "net_capital_gain_loss": str(summary.net_capital_gain_loss),
            },
            "adjustments": {
                "wash_sales_disallowed": str(wash_sale_total),
                "prior_year_carryforward_used": str(summary.loss_carryforward_used),
                "ordinary_income_deduction": str(summary.ordinary_income_deduction),
                "carryforward_to_next_year": str(summary.loss_carryforward_remaining),
            },
            "estimated_tax_liability": {
                "short_term_tax": str(summary.estimated_short_term_tax),
                "long_term_tax": str(summary.estimated_long_term_tax),
                "total_estimated_tax": str(summary.estimated_total_tax),
            },
            "forms_required": [],
        }

        # Determine required forms
        if summary.short_term_gains or summary.short_term_losses or summary.long_term_gains or summary.long_term_losses:
            year_end["forms_required"].append("Form 8949")
            year_end["forms_required"].append("Schedule D")

        if form_6781.get("aggregate_profit_or_loss", 0) != 0:
            year_end["forms_required"].append("Form 6781")

        # Write to file
        if self.config.generate_json:
            filename = f"year_end_summary_{tax_year}.json"
            filepath = Path(self.config.output_directory) / filename
            with open(filepath, "w") as f:
                json.dump(year_end, f, indent=2)
            year_end["file"] = str(filepath)

        logger.info(f"Generated year-end summary for {tax_year}")
        return year_end

    def generate_all_reports(self, tax_year: Optional[int] = None) -> Dict[str, Any]:
        """
        Generate all tax reports for a year.

        Args:
            tax_year: Tax year to report

        Returns:
            Dictionary with all generated reports
        """
        tax_year = tax_year or self.config.tax_year

        logger.info(f"Generating all tax reports for {tax_year}")

        reports = {
            "tax_year": tax_year,
            "generated_at": datetime.now().isoformat(),
            "reports": {},
        }

        # Generate each report
        reports["reports"]["form_8949"] = self.generate_form_8949(tax_year)
        reports["reports"]["schedule_d"] = self.generate_schedule_d(tax_year)
        reports["reports"]["form_6781"] = self.generate_form_6781(tax_year)
        reports["reports"]["wash_sales"] = self.generate_wash_sale_report(tax_year)
        reports["reports"]["year_end_summary"] = self.generate_year_end_summary(tax_year)

        # Collect all files
        all_files = []
        for report_data in reports["reports"].values():
            if isinstance(report_data, dict):
                if "file" in report_data:
                    all_files.append(report_data["file"])
                if "files" in report_data:
                    all_files.extend(report_data["files"])
                if "csv_file" in report_data:
                    all_files.append(report_data["csv_file"])

        reports["all_files"] = all_files

        logger.info(f"Generated {len(all_files)} report files")
        return reports
