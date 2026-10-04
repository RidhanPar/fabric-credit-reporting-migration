"""The seven board KPIs, computed from gold, month by month and country by country.

Each KPI is produced in more than one *variant*:

* ``legacy_def`` reproduces the legacy workbook's arithmetic exactly, from correct
  data. It is what makes the reconciliation close: if the legacy figures are fixed
  and the data is the same, this variant must equal them.
* intermediate variants (``avg_rate``, ``per_customer``) isolate one agreed
  definition change each, so the effect of that change is measured, not estimated.
* ``new`` is the definition the new model publishes.

The variants form an ordered chain per KPI, which Phase 4 walks to attribute
every difference to a named cause.

Components are built per country and month, and every component is additive, so
the GROUP scope is the sum of the three countries. That is also how the legacy
Summary tab rolls up, which keeps the two comparable.
"""
from __future__ import annotations

from dataclasses import dataclass

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from portfolio_migration.kpi_definitions import GROUP_SCOPE, KPI_TABLE
from portfolio_migration.lakehouse.io import Lake


@dataclass
class KpiResult:
    rows: int


def components(lake: Lake) -> DataFrame:
    """Additive building blocks per country and month end, in local currency and EUR."""
    balances = lake.read("fact_balance_snapshot").filter("is_active")
    fx = lake.read("fx_rate_monthly").select(
        F.col("month_end").alias("_fx_month"), F.col("currency").alias("_fx_ccy"),
        "rate_to_eur_month_end", "rate_to_eur_month_avg")

    bal = (balances.join(fx, (F.col("snapshot_date") == F.col("_fx_month"))
                         & (F.col("currency") == F.col("_fx_ccy")), "left")
           .groupBy(F.col("snapshot_date").alias("month_end"), F.col("country_code").alias("scope"))
           .agg(F.sum(F.col("balance_local") * F.col("rate_to_eur_month_end")).alias("bal_total_eur"),
                F.sum("balance_eur").alias("bal_rows_eur"),
                F.sum(F.when(F.col("is_dpd30"), F.col("balance_local") * F.col("rate_to_eur_month_end"))
                      .otherwise(0)).alias("dpd30_total_eur"),
                F.sum(F.when(F.col("is_dpd30"), F.col("balance_eur")).otherwise(0)).alias("dpd30_rows_eur"),
                F.sum(F.when(F.col("is_dpd90"), F.col("balance_local") * F.col("rate_to_eur_month_end"))
                      .otherwise(0)).alias("dpd90_total_eur"),
                F.sum(F.when(F.col("is_dpd90"), F.col("balance_eur")).otherwise(0)).alias("dpd90_rows_eur"),
                F.count("*").alias("active_accounts"),
                F.countDistinct("customer_id").alias("active_customers")))

    orig = (lake.read("fact_origination")
            .join(fx, (F.last_day("open_date") == F.col("_fx_month")) & (F.col("currency") == F.col("_fx_ccy")), "left")
            .groupBy(F.last_day("open_date").alias("month_end"), F.col("country_code").alias("scope"))
            .agg(F.count("*").alias("new_accounts"),
                 F.sum(F.col("amount_local") * F.col("rate_to_eur_month_end")).alias("orig_total_eur_month_end"),
                 F.sum(F.col("amount_local") * F.col("rate_to_eur_month_avg")).alias("orig_total_eur_avg"),
                 F.sum("amount_eur").alias("orig_rows_eur")))

    apps = (lake.read("fact_application")
            .groupBy(F.last_day("application_date").alias("month_end"), F.col("country_code").alias("scope"))
            .agg(F.count("*").alias("applications_received"),
                 F.sum(F.col("is_approved").cast("int")).alias("applications_approved"),
                 F.sum(F.col("is_decisioned").cast("int")).alias("applications_decisioned")))

    per_country = (bal.join(orig, ["month_end", "scope"], "full").join(apps, ["month_end", "scope"], "full")
                   .fillna(0))
    measures = [c for c in per_country.columns if c not in ("month_end", "scope")]
    group = (per_country.groupBy("month_end").agg(*[F.sum(c).alias(c) for c in measures])
             .withColumn("scope", F.lit(GROUP_SCOPE)))
    return per_country.unionByName(group.select(per_country.columns))


def _safe_divide(numerator: str, denominator: str):
    """Divide as doubles.

    Dividing two decimals in Spark caps the result scale (both operands here are
    decimal(38,10), so the result lands at 6 decimal places), which silently
    truncates a rate like 0.0282324989 to 0.028232. Rates are ratios, so double
    is the right type for them.
    """
    num, den = F.col(numerator).cast("double"), F.col(denominator).cast("double")
    return F.when(den == 0, None).otherwise(num / den)


def kpi_variants(comp: DataFrame) -> DataFrame:
    """Long table: month_end, scope, kpi, variant, value."""
    definitions = {
        ("portfolio_balance_eur", "legacy_def"): F.col("bal_total_eur"),
        ("portfolio_balance_eur", "new"): F.col("bal_rows_eur"),
        ("new_accounts", "legacy_def"): F.col("new_accounts").cast("double"),
        ("new_originations_eur", "legacy_def"): F.col("orig_total_eur_month_end"),
        ("new_originations_eur", "avg_rate"): F.col("orig_total_eur_avg"),
        ("new_originations_eur", "new"): F.col("orig_rows_eur"),
        ("dpd30_rate", "legacy_def"): _safe_divide("dpd30_total_eur", "bal_total_eur"),
        ("dpd30_rate", "new"): _safe_divide("dpd30_rows_eur", "bal_rows_eur"),
        ("dpd90_rate", "legacy_def"): _safe_divide("dpd90_total_eur", "bal_total_eur"),
        ("dpd90_rate", "new"): _safe_divide("dpd90_rows_eur", "bal_rows_eur"),
        ("approval_rate", "legacy_def"): _safe_divide("applications_approved", "applications_received"),
        ("approval_rate", "new"): _safe_divide("applications_approved", "applications_decisioned"),
        ("avg_balance_per_customer_eur", "legacy_def"): _safe_divide("bal_total_eur", "active_accounts"),
        ("avg_balance_per_customer_eur", "per_customer"): _safe_divide("bal_total_eur", "active_customers"),
        ("avg_balance_per_customer_eur", "new"): _safe_divide("bal_rows_eur", "active_customers"),
    }
    frames = [comp.select("month_end", "scope", F.lit(kpi).alias("kpi"), F.lit(variant).alias("variant"),
                          expr.cast("double").alias("value"))
              for (kpi, variant), expr in definitions.items()]
    out = frames[0]
    for f in frames[1:]:
        out = out.unionByName(f)
    return out.filter("value IS NOT NULL")


def build(lake: Lake) -> DataFrame:
    return kpi_variants(components(lake))


def run(lake: Lake, batch_id: str | None = None) -> KpiResult:
    df = build(lake)
    lake.write(df, KPI_TABLE)
    return KpiResult(lake.read(KPI_TABLE).count())
