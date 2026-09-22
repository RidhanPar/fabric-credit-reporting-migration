"""Static reference data for the synthetic lender.

Everything the generator, the legacy workbook and the tests agree on lives here,
so a change in one place cannot silently drift from the others.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

SEED = 42

# Reporting window: 24 complete month ends, Sep 2024 to Aug 2026.
WINDOW_START = date(2024, 9, 30)
WINDOW_MONTHS = 24
# The book is simulated from this point so the first reported month already
# holds a mature portfolio instead of starting at zero.
BURN_IN_MONTHS = 18

REPORTING_CURRENCY = "EUR"


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    currency: str
    fx_start: float          # EUR per 1 unit of local currency at burn in start
    fx_monthly_vol: float    # monthly random walk volatility of the rate
    fx_drift: float          # monthly drift, negative means local currency weakens
    monthly_applications: int
    csv_decimal: str         # decimal separator used by the local core banking extract
    csv_date_format: str     # date format used by the local core banking extract


COUNTRIES: tuple[Country, ...] = (
    Country("PL", "Poland", "PLN", 0.2310, 0.006, 0.0005, 300, ".", "%Y-%m-%d"),
    Country("CZ", "Czech Republic", "CZK", 0.0402, 0.005, 0.0003, 220, ",", "%Y-%m-%d"),
    Country("RO", "Romania", "RON", 0.2012, 0.004, -0.0014, 180, ".", "%d/%m/%Y"),
)
COUNTRY_BY_CODE = {c.code: c for c in COUNTRIES}


@dataclass(frozen=True)
class Product:
    code: str
    name: str
    product_line: str          # "Personal Loans" or "Credit Cards"
    is_revolving: bool
    launch_month_index: int    # index into the simulation calendar (burn in + window)
    amount_eur_range: tuple[int, int]   # principal (loans) or credit limit (cards)
    term_months_choices: tuple[int, ...]
    annual_rate: float
    mix_weight: float          # share of applications once launched
    base_approval: float


PRODUCTS: tuple[Product, ...] = (
    Product("PL_STD", "Personal Loan Standard", "Personal Loans", False, 0, (1_000, 8_000), (12, 24, 36), 0.149, 0.34, 0.58),
    Product("PL_PREM", "Personal Loan Premium", "Personal Loans", False, 0, (6_000, 20_000), (24, 36, 48, 60), 0.099, 0.14, 0.46),
    Product("CC_CLASSIC", "Classic Credit Card", "Credit Cards", True, 0, (500, 3_000), (), 0.229, 0.30, 0.62),
    Product("CC_GOLD", "Gold Credit Card", "Credit Cards", True, 0, (3_000, 9_000), (), 0.199, 0.12, 0.44),
    # Balance transfer style loan that pays off a customer's card debt.
    # Launched part way through the reporting window.
    Product("CC_CONSOL", "Card Debt Consolidation Loan", "Personal Loans", False, BURN_IN_MONTHS + 6,
            (2_000, 12_000), (24, 36, 48), 0.119, 0.10, 0.50),
)
PRODUCT_BY_CODE = {p.code: p for p in PRODUCTS}

# Days past due buckets used by the legacy DWH cube (label, lower bound, upper bound inclusive).
DPD_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("Current", 0, 0),
    ("1-29", 1, 29),
    ("30-59", 30, 59),
    ("60-89", 60, 89),
    ("90-179", 90, 179),
)
WRITE_OFF_DPD = 180


def month_ends(start: date, n: int) -> list[date]:
    return [d.date() for d in pd.date_range(start=start, periods=n, freq="ME")]


def simulation_calendar() -> list[date]:
    """Burn in months followed by the reporting window, as month end dates."""
    first = (pd.Timestamp(WINDOW_START) - pd.offsets.MonthEnd(BURN_IN_MONTHS)).date()
    return month_ends(first, BURN_IN_MONTHS + WINDOW_MONTHS)


def reporting_months() -> list[date]:
    return month_ends(WINDOW_START, WINDOW_MONTHS)


def dpd_bucket(dpd: int) -> str:
    for label, lo, hi in DPD_BUCKETS:
        if lo <= dpd <= hi:
            return label
    raise ValueError(f"days past due {dpd} is outside the reportable range")
