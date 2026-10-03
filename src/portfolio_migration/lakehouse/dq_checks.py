"""The check list. This is the file a reviewer reads to know what is guaranteed.

Severity is set per check: ERROR stops the publish, WARN is recorded and the
publish continues. Gold checks run against the staging tables, so nothing that
fails an ERROR check ever reaches the tables the report reads.
"""
from __future__ import annotations

from pyspark.sql import functions as F

from portfolio_migration import config as cfg
from portfolio_migration.lakehouse import kpis, silver
from portfolio_migration.lakehouse.quality import (
    ERROR,
    WARN,
    Check,
    Resolver,
    accepted_values,
    expression,
    metric_at_most,
    not_null,
    referential,
    row_count_match,
    sum_match,
    unique,
    value_range,
)

COUNTRY_CODES = [c.code for c in cfg.COUNTRIES]
CURRENCIES = [c.currency for c in cfg.COUNTRIES]
DPD_LABELS = [label for label, _, _ in cfg.DPD_BUCKETS]
PRODUCT_LINES = ["Personal Loans", "Credit Cards"]


# Source specific checks -----------------------------------------------------------

def balance_file_control_totals(severity: str = ERROR) -> Check:
    """Every balance file's parsed rows and amount must match its trailer record.

    This is the only check that catches a parsing bug: a wrong decimal separator
    keeps the row count and changes the money.
    """
    def run(resolve: Resolver) -> tuple[int, int, str]:
        body = resolve("bronze_balances").filter("account_id <> 'TRL' OR account_id IS NULL")
        parsed = body.select("_source_file", "_country_feed",
                             silver.parse_decimal("balance_local").alias("_amount"))
        actual = parsed.groupBy("_source_file").agg(F.count("*").alias("_rows"),
                                                    F.sum("_amount").alias("_total"))
        totals = resolve("silver_control_totals")
        joined = totals.join(actual, totals.source_file == actual._source_file, "left")
        bad = joined.filter("_rows IS NULL OR _rows <> expected_rows "
                            "OR abs(coalesce(_total, 0) - expected_amount) > 0.01")
        detail = "; ".join(
            f"{r.source_file.split('/')[-1]}: trailer {r.expected_rows} rows / {r.expected_amount}, "
            f"parsed {r._rows} rows / {r._total}"
            for r in bad.limit(3).collect())
        return totals.count(), bad.count(), detail
    return Check("bronze_balances.control_totals", "silver", "bronze_balances", "control_total", severity,
                 "parsed rows and amount per file match the file's trailer record", run)


def quarantine_rate_at_most(entity: str, limit: float, severity: str = WARN) -> Check:
    """Quarantining a handful of rows is housekeeping. Quarantining a lot means the feed changed."""
    def metric(resolve: Resolver) -> tuple[float, str]:
        audit = resolve(silver.AUDIT)
        latest = audit.agg(F.max("loaded_at")).collect()[0][0]
        rows = audit.filter((F.col("entity") == entity) & (F.col("loaded_at") == F.lit(latest))).collect()
        if not rows:
            return 0.0, f"{entity}: no load recorded"
        r = rows[0]
        ratio = (r["quarantined_rows"] / r["bronze_rows"]) if r["bronze_rows"] else 0.0
        return ratio, f"{entity}: {r['quarantined_rows']} of {r['bronze_rows']} rows quarantined ({ratio:.4%})"
    return metric_at_most("silver", silver.AUDIT, f"quarantine_rate_{entity}", metric, limit, severity,
                          f"{entity} quarantine rate at most {limit:.2%}")


def fx_coverage_complete(severity: str = ERROR) -> Check:
    """A missing rate silently turns EUR balances into nulls, so the window must be fully covered."""
    months = [str(m) for m in cfg.reporting_months()]

    def metric(resolve: Resolver) -> tuple[float, str]:
        fx = resolve("silver_fx_rates").filter(F.col("month_end").cast("string").isin(months))
        have = fx.filter(F.col("currency").isin(CURRENCIES)).select("month_end", "currency").distinct().count()
        expected = len(months) * len(CURRENCIES)
        return expected - have, f"{have} of {expected} month and currency rates present"
    return metric_at_most("silver", "silver_fx_rates", "fx_coverage", metric, 0, severity,
                          "every reporting month has a rate for every currency")


# Silver ---------------------------------------------------------------------------

SILVER_CHECKS: list[Check] = [
    # Grain and completeness
    unique("silver", "silver_balances", ["account_id", "snapshot_date"]),
    not_null("silver", "silver_balances", ["account_id", "snapshot_date", "country_code", "product_code",
                                           "currency", "balance_local", "days_past_due", "account_status"]),
    unique("silver", "silver_accounts", ["account_id"]),
    not_null("silver", "silver_accounts", ["account_id", "customer_id", "country_code", "product_code",
                                           "open_date", "original_amount_local"]),
    unique("silver", "silver_customers", ["customer_id"]),
    not_null("silver", "silver_customers", ["customer_id", "country_code", "risk_grade"]),
    unique("silver", "silver_applications", ["application_id"]),
    not_null("silver", "silver_applications", ["application_id", "country_code", "product_code",
                                               "application_date", "status"]),
    unique("silver", "silver_application_events", ["application_id", "event_ts"]),
    unique("silver", "silver_repayments", ["payment_id"]),
    not_null("silver", "silver_repayments", ["payment_id", "account_id", "payment_date", "amount_local"]),
    unique("silver", "silver_products", ["product_code"]),
    unique("silver", "silver_fx_rates", ["month_end", "currency"]),

    # Referential integrity
    referential("silver", "silver_balances", "account_id", "silver_accounts"),
    referential("silver", "silver_balances", "product_code", "silver_products"),
    referential("silver", "silver_accounts", "customer_id", "silver_customers"),
    referential("silver", "silver_accounts", "application_id", "silver_applications"),
    referential("silver", "silver_repayments", "account_id", "silver_accounts"),
    referential("silver", "silver_applications", "product_code", "silver_products"),
    referential("silver", "silver_applications", "customer_id", "silver_customers"),

    # Accepted values
    accepted_values("silver", "silver_balances", "account_status", silver.ACCOUNT_STATUSES),
    accepted_values("silver", "silver_balances", "country_code", COUNTRY_CODES),
    accepted_values("silver", "silver_balances", "currency", CURRENCIES),
    accepted_values("silver", "silver_applications", "status", silver.APPLICATION_STATUSES),
    accepted_values("silver", "silver_products", "product_line", PRODUCT_LINES),
    accepted_values("silver", "silver_customers", "risk_grade", ["A", "B", "C", "D", "E"]),

    # Ranges
    value_range("silver", "silver_balances", "days_past_due", 0, cfg.WRITE_OFF_DPD - 1),
    value_range("silver", "silver_balances", "balance_local", 0, None),
    value_range("silver", "silver_accounts", "original_amount_local", 0.01, None),
    value_range("silver", "silver_repayments", "amount_local", 0.01, None),
    value_range("silver", "silver_fx_rates", "rate_to_eur_month_end", 0.000001, 100),
    value_range("silver", "silver_fx_rates", "rate_to_eur_month_avg", 0.000001, 100),

    # Business rules
    expression("silver", "silver_balances", "snapshot_is_month_end",
               "snapshot_date = last_day(snapshot_date)", "snapshots are month end dated",
               sample_columns=["account_id", "snapshot_date"]),
    expression("silver", "silver_balances", "closed_accounts_have_no_balance",
               "account_status = 'ACTIVE' OR balance_local = 0",
               "closed and written off accounts carry no balance",
               sample_columns=["account_id", "snapshot_date", "account_status", "balance_local"]),
    expression("silver", "silver_accounts", "closed_after_opened",
               "close_date IS NULL OR close_date >= open_date", "an account cannot close before it opens",
               sample_columns=["account_id", "open_date", "close_date"]),
    expression("silver", "silver_applications", "decided_applications_have_a_decision_date",
               "status NOT IN ('APPROVED', 'DECLINED') OR decision_date IS NOT NULL",
               "approved and declined applications carry a decision date",
               sample_columns=["application_id", "status", "decision_date"]),
    expression("silver", "silver_applications", "no_undecided_applications_left",
               "status <> 'PENDING'", "no application is still pending at month end",
               severity=WARN, sample_columns=["application_id", "application_date"]),

    # Reconciliation between layers, and against the source files
    expression("silver", silver.AUDIT, "layer_row_reconciliation",
               "bronze_rows = control_rows + quarantined_rows + duplicate_rows + silver_rows",
               "bronze rows = trailers + quarantined + duplicates + silver, for every entity and run",
               sample_columns=["batch_id", "entity", "bronze_rows", "silver_rows"]),
    balance_file_control_totals(),
    fx_coverage_complete(),
    quarantine_rate_at_most("balances", 0.01),
    quarantine_rate_at_most("repayments", 0.01),
    quarantine_rate_at_most("application_events", 0.01),
]


# Gold ----------------------------------------------------------------------------

GOLD_CHECKS: list[Check] = [
    # Grain
    unique("gold", "fact_balance_snapshot", ["account_id", "date_key"]),
    unique("gold", "fact_origination", ["account_id"]),
    unique("gold", "fact_application", ["application_id"]),
    unique("gold", "fact_repayment", ["payment_id"]),
    unique("gold", "dim_account", ["account_id"]),
    unique("gold", "dim_customer", ["customer_id"]),
    unique("gold", "dim_product", ["product_code"]),
    unique("gold", "dim_country", ["country_code"]),
    unique("gold", "dim_date", ["date_key"]),
    unique("gold", "fx_rate_monthly", ["month_end_key", "currency"]),

    # Completeness
    not_null("gold", "fact_balance_snapshot", ["account_id", "customer_id", "country_code", "product_code",
                                               "date_key", "balance_local", "balance_eur", "fx_rate",
                                               "days_past_due", "dpd_bucket"]),
    not_null("gold", "fact_origination", ["account_id", "date_key", "amount_local", "amount_eur"]),
    not_null("gold", "fact_application", ["application_id", "date_key", "status"]),
    not_null("gold", "fact_repayment", ["payment_id", "account_id", "date_key", "amount_local", "amount_eur"]),

    # Referential integrity, fact to dimension
    referential("gold", "fact_balance_snapshot", "account_id", "dim_account"),
    referential("gold", "fact_balance_snapshot", "customer_id", "dim_customer"),
    referential("gold", "fact_balance_snapshot", "product_code", "dim_product"),
    referential("gold", "fact_balance_snapshot", "country_code", "dim_country"),
    referential("gold", "fact_balance_snapshot", "date_key", "dim_date"),
    referential("gold", "fact_origination", "account_id", "dim_account"),
    referential("gold", "fact_origination", "date_key", "dim_date"),
    referential("gold", "fact_application", "product_code", "dim_product"),
    referential("gold", "fact_application", "date_key", "dim_date"),
    referential("gold", "fact_repayment", "account_id", "dim_account"),
    referential("gold", "fact_repayment", "date_key", "dim_date"),

    # Accepted values and ranges
    accepted_values("gold", "fact_balance_snapshot", "dpd_bucket", DPD_LABELS),
    accepted_values("gold", "fact_balance_snapshot", "account_status", silver.ACCOUNT_STATUSES),
    accepted_values("gold", "dim_account", "account_state", ["OPEN", "CLOSED", "WRITTEN_OFF"]),
    accepted_values("gold", "dim_customer", "age_band", ["18-29", "30-44", "45-59", "60+"]),
    value_range("gold", "fact_balance_snapshot", "balance_eur", 0, None),
    value_range("gold", "fact_balance_snapshot", "days_past_due", 0, cfg.WRITE_OFF_DPD - 1),

    # Measure logic
    expression("gold", "fact_balance_snapshot", "eur_conversion_is_consistent",
               "abs(balance_eur - round(balance_local * fx_rate, 2)) <= 0.01",
               "balance_eur equals balance_local times the month end rate",
               sample_columns=["account_id", "date_key", "balance_local", "fx_rate", "balance_eur"]),
    expression("gold", "fact_balance_snapshot", "dpd90_implies_dpd30",
               "NOT is_dpd90 OR is_dpd30", "90+ days past due is always also 30+",
               sample_columns=["account_id", "date_key", "days_past_due"]),
    expression("gold", "fact_balance_snapshot", "inactive_rows_are_not_delinquent",
               "is_active OR (NOT is_dpd30 AND NOT is_dpd90)",
               "closed and written off rows are never counted as delinquent",
               sample_columns=["account_id", "date_key", "account_status"]),
    expression("gold", "fact_application", "approved_implies_decisioned",
               "NOT is_approved OR is_decisioned", "an approved application is a decisioned application",
               sample_columns=["application_id", "status"]),

    # Reconciliation, gold against silver
    row_count_match("gold", "fact_balance_snapshot", "silver_balances"),
    row_count_match("gold", "fact_origination", "silver_accounts"),
    row_count_match("gold", "fact_application", "silver_applications"),
    row_count_match("gold", "fact_repayment", "silver_repayments"),
    row_count_match("gold", "dim_customer", "silver_customers"),
    sum_match("gold", "fact_balance_snapshot", "balance_local", "silver_balances"),
    sum_match("gold", "fact_repayment", "amount_local", "silver_repayments"),
    sum_match("gold", "fact_origination", "amount_local", "silver_accounts", "original_amount_local"),
]

# KPI table ----------------------------------------------------------------------

def rates_are_not_truncated(severity: str = ERROR) -> Check:
    """Catch a rate column that has silently lost precision.

    Dividing two decimals in Spark caps the result scale, which once truncated
    every rate in this table to 6 decimal places. A genuine rate almost never
    lands exactly on 6 decimals, so if most of them do, precision has been lost.
    """
    def metric(resolve: Resolver) -> tuple[float, str]:
        rates = resolve(kpis.KPI_TABLE).filter(F.col("kpi").endswith("_rate")).filter("value <> 0")
        total = rates.count()
        if not total:
            return 0.0, "no rate values"
        exact = rates.filter(F.abs(F.col("value") - F.round("value", 6)) == 0).count()
        share = exact / total
        return share, f"{exact} of {total} rate values sit exactly on 6 decimals ({share:.1%})"
    return metric_at_most("kpi", kpis.KPI_TABLE, "rates_not_truncated", metric, 0.5, severity,
                          "rates have not been truncated to 6 decimal places")


def group_scope_equals_the_sum_of_countries(kpi: str, severity: str = ERROR) -> Check:
    """The group figure must be the three countries added up, as the legacy pack rolls up."""
    def metric(resolve: Resolver) -> tuple[float, str]:
        table = resolve(kpis.KPI_TABLE).filter((F.col("kpi") == kpi) & (F.col("variant") == "legacy_def"))
        countries = (table.filter(F.col("scope").isin(COUNTRY_CODES))
                     .groupBy("month_end").agg(F.sum("value").alias("summed")))
        group = table.filter(F.col("scope") == kpis.GROUP_SCOPE).select("month_end", F.col("value").alias("group"))
        joined = group.join(countries, "month_end", "inner")
        worst = joined.select(F.max(F.abs(F.col("group") - F.col("summed"))).alias("gap")).collect()[0]["gap"]
        return float(worst or 0.0), f"{kpi}: largest gap between group and the sum of countries is {worst}"
    return metric_at_most("kpi", kpis.KPI_TABLE, f"group_equals_sum_{kpi}", metric, 0.05, severity,
                          f"{kpi} at group level equals the sum of the three countries")


KPI_CHECKS: list[Check] = [
    unique("kpi", kpis.KPI_TABLE, ["month_end", "scope", "kpi", "variant"]),
    not_null("kpi", kpis.KPI_TABLE, ["month_end", "scope", "kpi", "variant", "value"]),
    accepted_values("kpi", kpis.KPI_TABLE, "kpi", list(kpis.VARIANT_CHAINS)),
    accepted_values("kpi", kpis.KPI_TABLE, "scope", [*COUNTRY_CODES, kpis.GROUP_SCOPE]),
    expression("kpi", kpis.KPI_TABLE, "rates_between_zero_and_one",
               "NOT kpi LIKE '%_rate' OR (value >= 0 AND value <= 1)",
               "every rate is between 0 and 1", sample_columns=["month_end", "scope", "kpi", "value"]),
    expression("kpi", kpis.KPI_TABLE, "amounts_not_negative",
               "kpi LIKE '%_rate' OR value >= 0", "no negative amounts or counts",
               sample_columns=["month_end", "scope", "kpi", "value"]),
    rates_are_not_truncated(),
    group_scope_equals_the_sum_of_countries("portfolio_balance_eur"),
    group_scope_equals_the_sum_of_countries("new_originations_eur"),
    group_scope_equals_the_sum_of_countries("new_accounts"),
]

ALL_CHECKS = SILVER_CHECKS + GOLD_CHECKS + KPI_CHECKS
