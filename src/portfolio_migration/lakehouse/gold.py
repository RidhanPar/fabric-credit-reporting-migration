"""Gold: the business ready star schema that the Power BI model reads in Direct Lake.

Facts                         Grain
  fact_balance_snapshot       one account at one month end
  fact_origination            one account, at the date it was opened
  fact_application            one credit application, current state
  fact_repayment              one payment
Dimensions
  dim_date, dim_country, dim_product, dim_customer, dim_account
Reference
  fx_rate_monthly             month end and monthly average EUR rates

Currency convention (IAS 21 style)
  * Balances are stocks, converted at the month end (closing) rate.
  * Originations, applications and repayments are flows, converted at the
    monthly average rate. The month end rate is also kept on originations so the
    effect of this choice can be measured.

Keys: natural keys for business entities (account_id, customer_id, product_code,
country_code) and an integer yyyymmdd ``date_key``. At 16k accounts surrogate
keys buy nothing and natural keys keep lineage readable; SCD2 history would change that.
"""
from __future__ import annotations

from dataclasses import dataclass

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

from portfolio_migration import config as cfg
from portfolio_migration.lakehouse.io import Lake
from portfolio_migration.lakehouse.silver import MONEY

GOLD_TABLES = ("dim_date", "dim_country", "dim_product", "dim_customer", "dim_account", "fx_rate_monthly",
               "fact_balance_snapshot", "fact_origination", "fact_application", "fact_repayment")


@dataclass
class GoldResult:
    table: str
    rows: int


def date_key(c: Column) -> Column:
    return F.date_format(c, "yyyyMMdd").cast("int")


def to_eur(amount: str, rate: str) -> Column:
    return F.round(F.col(amount) * F.col(rate), 2).cast(MONEY)


def dpd_bucket(dpd: Column) -> Column:
    expr = F.lit(None).cast("string")
    for label, lo, hi in reversed(cfg.DPD_BUCKETS):
        expr = F.when(dpd.between(lo, hi), F.lit(label)).otherwise(expr)
    return expr


def dpd_bucket_order(dpd: Column) -> Column:
    expr = F.lit(None).cast("int")
    for i, (_, lo, hi) in reversed(list(enumerate(cfg.DPD_BUCKETS))):
        expr = F.when(dpd.between(lo, hi), F.lit(i)).otherwise(expr)
    return expr


def build_dim_date(spark) -> DataFrame:
    cal = cfg.simulation_calendar()
    start, end = cal[0].replace(day=1), cal[-1]
    d = spark.sql(f"SELECT explode(sequence(DATE'{start}', DATE'{end}', INTERVAL 1 DAY)) AS date")
    return d.select(
        date_key(F.col("date")).alias("date_key"),
        "date",
        F.year("date").alias("year"),
        F.quarter("date").alias("quarter"),
        F.month("date").alias("month"),
        F.date_format("date", "MMM").alias("month_name"),
        F.date_format("date", "yyyy-MM").alias("year_month"),
        F.last_day("date").alias("month_end_date"),
        date_key(F.last_day("date")).alias("month_end_key"),
        (F.col("date") == F.last_day("date")).alias("is_month_end"),
    )


def build_dim_country(spark) -> DataFrame:
    return spark.createDataFrame([(c.code, c.name, c.currency) for c in cfg.COUNTRIES],
                                 "country_code string, country_name string, currency string")


def build_dim_product(products: DataFrame) -> DataFrame:
    return products.select("product_code", "product_name", "product_line", "is_revolving", "launch_date")


def build_dim_customer(customers: DataFrame) -> DataFrame:
    as_of = cfg.reporting_months()[-1]
    age = F.floor(F.months_between(F.lit(as_of), F.col("date_of_birth")) / 12)
    band = (F.when(age < 30, "18-29").when(age < 45, "30-44").when(age < 60, "45-59").otherwise("60+"))
    return customers.select("customer_id", "country_code", "employment_status", "risk_grade",
                            "customer_since", "monthly_income_local", band.alias("age_band"))


def build_dim_account(accounts: DataFrame) -> DataFrame:
    state = (F.when(F.col("write_off_date").isNotNull(), "WRITTEN_OFF")
              .when(F.col("close_date").isNotNull(), "CLOSED").otherwise("OPEN"))
    return accounts.select(
        "account_id", "customer_id", "application_id", "country_code", "product_code", "currency",
        "open_date", date_key(F.col("open_date")).alias("open_date_key"),
        "original_amount_local", "term_months", "annual_interest_rate",
        "close_date", "write_off_date", "write_off_amount_local", state.alias("account_state"),
    )


def build_fx(fx: DataFrame) -> DataFrame:
    return fx.select(date_key(F.col("month_end")).alias("month_end_key"), "month_end", "currency",
                     "rate_to_eur_month_end", "rate_to_eur_month_avg")


def _fx_on(df: DataFrame, fx: DataFrame, month_end: Column) -> DataFrame:
    rates = fx.select(F.col("month_end").alias("_fx_month"), F.col("currency").alias("_fx_ccy"),
                      "rate_to_eur_month_end", "rate_to_eur_month_avg")
    return (df.join(rates, (month_end == F.col("_fx_month")) & (F.col("currency") == F.col("_fx_ccy")), "left")
              .drop("_fx_month", "_fx_ccy"))


def build_fact_balance_snapshot(balances: DataFrame, accounts: DataFrame, fx: DataFrame) -> DataFrame:
    b = balances.join(accounts.select("account_id", "customer_id"), "account_id", "left")
    b = _fx_on(b, fx, F.col("snapshot_date"))
    dpd = F.col("days_past_due")
    active = F.col("account_status") == "ACTIVE"
    return b.select(
        "account_id", "customer_id", "country_code", "product_code", "currency",
        "snapshot_date", date_key(F.col("snapshot_date")).alias("date_key"),
        "account_status", active.alias("is_active"),
        "days_past_due", dpd_bucket(dpd).alias("dpd_bucket"), dpd_bucket_order(dpd).alias("dpd_bucket_order"),
        (active & (dpd >= 30)).alias("is_dpd30"), (active & (dpd >= 90)).alias("is_dpd90"),
        "balance_local", F.col("rate_to_eur_month_end").alias("fx_rate"),
        to_eur("balance_local", "rate_to_eur_month_end").alias("balance_eur"),
    )


def build_fact_origination(accounts: DataFrame, fx: DataFrame) -> DataFrame:
    a = _fx_on(accounts, fx, F.last_day("open_date"))
    return a.select(
        "account_id", "customer_id", "country_code", "product_code", "currency", "open_date",
        date_key(F.col("open_date")).alias("date_key"),
        date_key(F.last_day("open_date")).alias("month_end_key"),
        F.col("original_amount_local").alias("amount_local"),
        F.col("rate_to_eur_month_avg").alias("fx_rate_avg"),
        to_eur("original_amount_local", "rate_to_eur_month_avg").alias("amount_eur"),
        F.col("rate_to_eur_month_end").alias("fx_rate_month_end"),
        to_eur("original_amount_local", "rate_to_eur_month_end").alias("amount_eur_month_end_rate"),
    )


def build_fact_application(apps: DataFrame, fx: DataFrame) -> DataFrame:
    a = _fx_on(apps, fx, F.last_day("application_date"))
    return a.select(
        "application_id", "customer_id", "country_code", "product_code", "channel", "risk_grade", "status",
        "application_date", date_key(F.col("application_date")).alias("date_key"),
        date_key(F.last_day("application_date")).alias("month_end_key"),
        "decision_date",
        F.col("status").isin("APPROVED", "DECLINED").alias("is_decisioned"),
        (F.col("status") == "APPROVED").alias("is_approved"),
        "requested_amount_local",
        to_eur("requested_amount_local", "rate_to_eur_month_avg").alias("requested_amount_eur"),
    )


def build_fact_repayment(repayments: DataFrame, accounts: DataFrame, fx: DataFrame) -> DataFrame:
    r = repayments.join(accounts.select("account_id", "customer_id", "country_code", "product_code"),
                        "account_id", "left")
    r = _fx_on(r, fx, F.last_day("payment_date"))
    return r.select(
        "payment_id", "account_id", "customer_id", "country_code", "product_code", "currency",
        "payment_date", date_key(F.col("payment_date")).alias("date_key"), "payment_channel",
        "amount_local", to_eur("amount_local", "rate_to_eur_month_avg").alias("amount_eur"),
    )


def build(lake: Lake) -> dict[str, DataFrame]:
    s = {name: lake.read(f"silver_{name}") for name in
         ("products", "fx_rates", "customers", "accounts", "balances", "repayments", "applications")}
    return {
        "dim_date": build_dim_date(lake.spark),
        "dim_country": build_dim_country(lake.spark),
        "dim_product": build_dim_product(s["products"]),
        "dim_customer": build_dim_customer(s["customers"]),
        "dim_account": build_dim_account(s["accounts"]),
        "fx_rate_monthly": build_fx(s["fx_rates"]),
        "fact_balance_snapshot": build_fact_balance_snapshot(s["balances"], s["accounts"], s["fx_rates"]),
        "fact_origination": build_fact_origination(s["accounts"], s["fx_rates"]),
        "fact_application": build_fact_application(s["applications"], s["fx_rates"]),
        "fact_repayment": build_fact_repayment(s["repayments"], s["accounts"], s["fx_rates"]),
    }


def run(lake: Lake, batch_id: str | None = None) -> list[GoldResult]:
    results = []
    for name, df in build(lake).items():
        lake.write(df, name)
        results.append(GoldResult(name, lake.read(name).count()))
    return results
