"""KPI names, variant chains and what each variant step means.

Plain Python on purpose. Both the Spark KPI builder and the pandas reconciliation
read these definitions, and the reconciliation must not need Spark to run.
"""
from __future__ import annotations

KPI_TABLE = "gold_kpi_monthly"
GROUP_SCOPE = "GROUP"

# KPI -> ordered variant chain, from the legacy arithmetic to the published definition.
VARIANT_CHAINS: dict[str, tuple[str, ...]] = {
    "portfolio_balance_eur": ("legacy_def", "new"),
    "new_accounts": ("legacy_def",),
    "new_originations_eur": ("legacy_def", "avg_rate", "new"),
    "dpd30_rate": ("legacy_def", "new"),
    "dpd90_rate": ("legacy_def", "new"),
    "approval_rate": ("legacy_def", "new"),
    "avg_balance_per_customer_eur": ("legacy_def", "per_customer", "new"),
}

# What each step in a chain means, for the reconciliation report.
VARIANT_STEPS: dict[tuple[str, str, str], str] = {
    ("portfolio_balance_eur", "legacy_def", "new"):
        "convert each account's balance to EUR and sum, instead of converting the country total",
    ("new_originations_eur", "legacy_def", "avg_rate"):
        "convert flows at the monthly average rate, instead of the month end rate",
    ("new_originations_eur", "avg_rate", "new"):
        "convert each account's amount to EUR and sum, instead of converting the country total",
    ("dpd30_rate", "legacy_def", "new"):
        "weight the group rate by per account EUR balances, instead of converted country totals",
    ("dpd90_rate", "legacy_def", "new"):
        "weight the group rate by per account EUR balances, instead of converted country totals",
    ("approval_rate", "legacy_def", "new"):
        "divide by decisioned applications (approved plus declined), instead of all applications received",
    ("avg_balance_per_customer_eur", "legacy_def", "per_customer"):
        "divide by distinct customers, instead of active accounts",
    ("avg_balance_per_customer_eur", "per_customer", "new"):
        "convert each account's balance to EUR and sum, instead of converting the country total",
}
