"""Silver: cleaned, typed, deduplicated. Bad rows go to quarantine with reasons.

Rules
* Parse with the source country's locale (CZ decimal comma, RO dd/mm/yyyy).
* Trim and upper case codes. That is a repair, not a rejection.
* A row that fails a validity rule goes to ``silver_quarantine`` with every
  reason it failed, its source file and the raw row as JSON. It is never dropped silently.
* Duplicates are resolved by business key: the latest delivery wins.
* Trailer records are split out into ``silver_control_totals`` for Phase 3.
* Every run appends one row per entity to ``silver_load_audit`` so that
  bronze rows = trailers + quarantined + duplicates + silver rows, provably.

Silver is rebuilt in full from bronze each run. At this volume (about 230k
balance rows) a full rebuild is simpler and cheaper than MERGE and is naturally
idempotent. At tens of millions of rows the right move is an incremental MERGE
on the business key.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import reduce

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F
from pyspark.sql.types import DecimalType, IntegerType, StringType, StructField, StructType, TimestampType

from portfolio_migration import config as cfg
from portfolio_migration.lakehouse.io import Lake

MONEY = DecimalType(18, 2)
RATE = DecimalType(18, 8)
LINEAGE = ["_country_feed", "_source_file", "_source_modified_at", "_ingested_at", "_batch_id"]
ACCOUNT_STATUSES = ["ACTIVE", "CLOSED", "WRITTEN_OFF"]
APPLICATION_STATUSES = ["APPROVED", "DECLINED", "WITHDRAWN", "INCOMPLETE", "PENDING"]
QUARANTINE = "silver_quarantine"
AUDIT = "silver_load_audit"
CONTROL_TOTALS = "silver_control_totals"

APPLICATION_SCHEMA = StructType([
    StructField("application_id", StringType()),
    StructField("submitted_at", StringType()),
    StructField("channel", StringType()),
    StructField("applicant", StructType([
        StructField("customer_id", StringType()),
        StructField("country", StringType()),
        StructField("risk_grade", StringType()),
    ])),
    StructField("product", StructType([
        StructField("code", StringType()),
        StructField("requested_amount", StringType()),
        StructField("currency", StringType()),
    ])),
    StructField("decision", StructType([
        StructField("status", StringType()),
        StructField("decided_on", StringType()),
    ])),
    StructField("event_ts", StringType()),
])


@dataclass
class EntityResult:
    entity: str
    bronze_rows: int
    control_rows: int
    quarantined_rows: int
    duplicate_rows: int
    silver_rows: int


# Parsing helpers -----------------------------------------------------------------

def _spark_date_format(py_format: str) -> str:
    return py_format.replace("%Y", "yyyy").replace("%m", "MM").replace("%d", "dd")


def clean_str(c: str) -> Column:
    """Trim; empty string becomes null."""
    t = F.trim(F.col(c))
    return F.when(t == "", None).otherwise(t)


def clean_code(c: str) -> Column:
    return F.upper(clean_str(c))


def parse_decimal(c: str, dtype: DecimalType = MONEY) -> Column:
    """Locale aware: CZ feeds use a decimal comma."""
    comma_feeds = [x.code for x in cfg.COUNTRIES if x.csv_decimal == ","]
    raw = clean_str(c)
    normalised = F.when(F.col("_country_feed").isin(comma_feeds), F.regexp_replace(raw, ",", ".")).otherwise(raw)
    return normalised.cast(dtype)


def parse_date(c: str) -> Column:
    """Locale aware: each country feed has its own date format. Group feeds use ISO."""
    expr = F.to_date(clean_str(c), "yyyy-MM-dd")
    for country in cfg.COUNTRIES:
        fmt = _spark_date_format(country.csv_date_format)
        if fmt != "yyyy-MM-dd":
            expr = F.when(F.col("_country_feed") == country.code, F.to_date(clean_str(c), fmt)).otherwise(expr)
    return expr


def failed(value: Column, raw: str) -> Column:
    """True when the raw field had content but did not parse."""
    return value.isNull() & clean_str(f"_raw_{raw}").isNotNull()


def carry(bronze: DataFrame) -> list[Column]:
    """Raw string columns (prefixed _raw_, kept for quarantine) plus lineage."""
    return ([F.col(c).alias(f"_raw_{c}") for c in bronze.columns if not c.startswith("_")]
            + [F.col(c) for c in LINEAGE])


# Validation, quarantine and dedup ---------------------------------------------------

def split_valid(df: DataFrame, rules: list[tuple[str, Column]]) -> tuple[DataFrame, DataFrame]:
    """Attach the list of failed rule codes; return (valid rows, rows with at least one failure).

    A rule that evaluates to null (for example comparing a null code) counts as failed:
    if a row cannot be proven valid, it is quarantined.
    """
    reasons = F.array_compact(F.array(*[F.when(F.coalesce(cond, F.lit(True)), F.lit(code)) for code, cond in rules]))
    tagged = df.withColumn("_reasons", reasons)
    return (tagged.filter(F.size("_reasons") == 0).drop("_reasons"),
            tagged.filter(F.size("_reasons") > 0))


def to_quarantine(bad: DataFrame, entity: str) -> DataFrame:
    raw = [c for c in bad.columns if c.startswith("_raw_")]
    return bad.select(
        F.lit(entity).alias("entity"),
        F.array_join("_reasons", ",").alias("reasons"),
        F.col("_source_file").alias("source_file"),
        F.to_json(F.struct(*[F.col(c).alias(c[len("_raw_"):]) for c in raw])).alias("raw_record"),
        F.col("_batch_id").alias("batch_id"),
        F.current_timestamp().alias("quarantined_at"),
    )


def latest_by_key(df: DataFrame, keys: list[str], order: list[Column]) -> DataFrame:
    """Keep one row per business key: the latest delivery."""
    w = Window.partitionBy(*keys).orderBy(*order)
    return df.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1").drop("_rn")


def latest_delivery() -> list[Column]:
    """Latest load first; within one load, the most recently modified file, then file name."""
    return [F.col("_ingested_at").desc(), F.col("_source_modified_at").desc(), F.col("_source_file").desc()]


# Entities -------------------------------------------------------------------------------

def silver_products(bronze: DataFrame) -> tuple[DataFrame, DataFrame]:
    typed = bronze.select(
        clean_code("product_code").alias("product_code"),
        clean_str("product_name").alias("product_name"),
        clean_str("product_line").alias("product_line"),
        (F.lower(clean_str("is_revolving")) == "true").alias("is_revolving"),
        F.to_date(clean_str("launch_date")).alias("launch_date"),
        clean_str("annual_interest_rate").cast(RATE).alias("annual_interest_rate"),
        *carry(bronze),
    )
    good, bad = split_valid(typed, [
        ("MISSING_PRODUCT_CODE", F.col("product_code").isNull()),
        ("INVALID_PRODUCT_LINE", ~F.col("product_line").isin("Personal Loans", "Credit Cards")),
    ])
    good = latest_by_key(good, ["product_code"], latest_delivery())
    return good, bad


def silver_fx_rates(bronze: DataFrame) -> tuple[DataFrame, DataFrame]:
    typed = bronze.select(
        F.to_date(clean_str("month_end")).alias("month_end"),
        clean_code("currency").alias("currency"),
        clean_str("rate_to_eur_month_end").cast(RATE).alias("rate_to_eur_month_end"),
        clean_str("rate_to_eur_month_avg").cast(RATE).alias("rate_to_eur_month_avg"),
        *carry(bronze),
    )
    good, bad = split_valid(typed, [
        ("INVALID_MONTH_END", F.col("month_end").isNull() | (F.col("month_end") != F.last_day("month_end"))),
        ("INVALID_RATE", F.col("rate_to_eur_month_end").isNull() | (F.col("rate_to_eur_month_end") <= 0)
         | F.col("rate_to_eur_month_avg").isNull() | (F.col("rate_to_eur_month_avg") <= 0)),
    ])
    good = latest_by_key(good, ["month_end", "currency"], latest_delivery())
    return good, bad


def silver_customers(bronze: DataFrame) -> tuple[DataFrame, DataFrame]:
    typed = bronze.select(
        clean_code("customer_id").alias("customer_id"),
        clean_code("country_code").alias("country_code"),
        parse_date("date_of_birth").alias("date_of_birth"),
        clean_code("employment_status").alias("employment_status"),
        parse_decimal("monthly_income_local").alias("monthly_income_local"),
        clean_code("risk_grade").alias("risk_grade"),
        parse_date("customer_since").alias("customer_since"),
        *carry(bronze),
    )
    good, bad = split_valid(typed, [
        ("MISSING_CUSTOMER_ID", F.col("customer_id").isNull()),
        ("COUNTRY_FEED_MISMATCH", F.col("country_code") != F.col("_country_feed")),
        ("INVALID_DATE_OF_BIRTH", failed(F.col("date_of_birth"), "date_of_birth")),
        ("INVALID_INCOME", failed(F.col("monthly_income_local"), "monthly_income_local")),
    ])
    good = latest_by_key(good, ["customer_id"], latest_delivery())
    return good, bad


def silver_accounts(bronze: DataFrame, products: list[str]) -> tuple[DataFrame, DataFrame]:
    typed = bronze.select(
        clean_code("account_id").alias("account_id"),
        clean_code("customer_id").alias("customer_id"),
        clean_code("application_id").alias("application_id"),
        clean_code("country_code").alias("country_code"),
        clean_code("product_code").alias("product_code"),
        clean_code("currency").alias("currency"),
        parse_date("open_date").alias("open_date"),
        parse_decimal("original_amount_local").alias("original_amount_local"),
        clean_str("term_months").cast(IntegerType()).alias("term_months"),
        parse_decimal("annual_interest_rate", RATE).alias("annual_interest_rate"),
        parse_date("close_date").alias("close_date"),
        parse_date("write_off_date").alias("write_off_date"),
        parse_decimal("write_off_amount_local").alias("write_off_amount_local"),
        *carry(bronze),
    )
    good, bad = split_valid(typed, [
        ("MISSING_ACCOUNT_ID", F.col("account_id").isNull()),
        ("MISSING_CUSTOMER_ID", F.col("customer_id").isNull()),
        ("COUNTRY_FEED_MISMATCH", F.col("country_code") != F.col("_country_feed")),
        ("UNKNOWN_PRODUCT", ~F.col("product_code").isin(products) | F.col("product_code").isNull()),
        ("INVALID_OPEN_DATE", F.col("open_date").isNull()),
        ("INVALID_AMOUNT", F.col("original_amount_local").isNull() | (F.col("original_amount_local") <= 0)),
        ("INVALID_CLOSE_DATE", failed(F.col("close_date"), "close_date")),
        ("INVALID_WRITE_OFF_DATE", failed(F.col("write_off_date"), "write_off_date")),
    ])
    good = latest_by_key(good, ["account_id"], latest_delivery())
    return good, bad


def split_trailers(bronze: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Trailer records: TRL, record count, control total (landed in the first three columns)."""
    is_trl = F.col("account_id") == "TRL"
    trailers = bronze.filter(is_trl).select(
        F.col("_source_file").alias("source_file"),
        F.col("_country_feed").alias("country_feed"),
        F.col("snapshot_date").cast("long").alias("expected_rows"),
        parse_decimal("country_code").alias("expected_amount"),
        F.col("_batch_id").alias("batch_id"),
    )
    return bronze.filter(~is_trl | F.col("account_id").isNull()), trailers


def silver_balances(bronze: DataFrame, products: list[str]) -> tuple[DataFrame, DataFrame]:
    typed = bronze.select(
        clean_code("account_id").alias("account_id"),
        parse_date("snapshot_date").alias("snapshot_date"),
        clean_code("country_code").alias("country_code"),
        clean_code("product_code").alias("product_code"),
        clean_code("currency").alias("currency"),
        parse_decimal("balance_local").alias("balance_local"),
        clean_str("days_past_due").cast(IntegerType()).alias("days_past_due"),
        clean_code("account_status").alias("account_status"),
        *carry(bronze),
    )
    good, bad = split_valid(typed, [
        ("MISSING_ACCOUNT_ID", F.col("account_id").isNull()),
        ("INVALID_SNAPSHOT_DATE", F.col("snapshot_date").isNull()
         | (F.col("snapshot_date") != F.last_day("snapshot_date"))),
        ("COUNTRY_FEED_MISMATCH", F.col("country_code") != F.col("_country_feed")),
        ("UNKNOWN_PRODUCT", ~F.col("product_code").isin(products) | F.col("product_code").isNull()),
        ("INVALID_BALANCE", F.col("balance_local").isNull() | (F.col("balance_local") < 0)),
        ("INVALID_DPD", F.col("days_past_due").isNull() | (F.col("days_past_due") < 0)),
        ("UNKNOWN_STATUS", ~F.col("account_status").isin(ACCOUNT_STATUSES) | F.col("account_status").isNull()),
    ])
    good = latest_by_key(good, ["account_id", "snapshot_date"], latest_delivery())
    return good, bad


def silver_repayments(bronze: DataFrame) -> tuple[DataFrame, DataFrame]:
    typed = bronze.select(
        clean_code("payment_id").alias("payment_id"),
        clean_code("account_id").alias("account_id"),
        parse_date("payment_date").alias("payment_date"),
        parse_decimal("amount_local").alias("amount_local"),
        clean_code("currency").alias("currency"),
        clean_code("payment_channel").alias("payment_channel"),
        *carry(bronze),
    )
    good, bad = split_valid(typed, [
        ("MISSING_PAYMENT_ID", F.col("payment_id").isNull()),
        ("MISSING_ACCOUNT_ID", F.col("account_id").isNull()),
        ("INVALID_PAYMENT_DATE", F.col("payment_date").isNull()),
        ("INVALID_AMOUNT", F.col("amount_local").isNull() | (F.col("amount_local") <= 0)),
    ])
    good = latest_by_key(good, ["payment_id"], latest_delivery())
    return good, bad


def silver_application_events(bronze: DataFrame, products: list[str]) -> tuple[DataFrame, DataFrame]:
    p = bronze.withColumn("_j", F.from_json("raw_json", APPLICATION_SCHEMA))
    typed = p.select(
        clean_code("_j.application_id").alias("application_id"),
        F.to_timestamp("_j.submitted_at").alias("submitted_at"),
        F.to_date(F.to_timestamp("_j.submitted_at")).alias("application_date"),
        F.upper(F.col("_j.channel")).alias("channel"),
        F.upper(F.col("_j.applicant.customer_id")).alias("customer_id"),
        F.upper(F.col("_j.applicant.country")).alias("country_code"),
        F.upper(F.col("_j.applicant.risk_grade")).alias("risk_grade"),
        F.upper(F.trim(F.col("_j.product.code"))).alias("product_code"),
        F.col("_j.product.requested_amount").cast(MONEY).alias("requested_amount_local"),
        F.upper(F.col("_j.product.currency")).alias("currency"),
        F.upper(F.col("_j.decision.status")).alias("status"),
        F.to_date("_j.decision.decided_on").alias("decision_date"),
        F.to_timestamp("_j.event_ts").cast(TimestampType()).alias("event_ts"),
        *carry(bronze),
    )
    good, bad = split_valid(typed, [
        ("MALFORMED_JSON", F.get_json_object("_raw_raw_json", "$").isNull()),
        ("MISSING_APPLICATION_ID", F.col("application_id").isNull()),
        ("INVALID_EVENT_TS", F.col("event_ts").isNull()),
        ("UNKNOWN_PRODUCT", ~F.col("product_code").isin(products) | F.col("product_code").isNull()),
        ("UNKNOWN_STATUS", ~F.col("status").isin(APPLICATION_STATUSES) | F.col("status").isNull()),
    ])
    # The same event delivered twice is a duplicate; different events are history and are kept.
    good = latest_by_key(good, ["application_id", "event_ts"], latest_delivery())
    return good, bad


def current_applications(events: DataFrame) -> DataFrame:
    """One row per application: its latest event is its current state."""
    w = Window.partitionBy("application_id").orderBy(F.col("event_ts").desc())
    return events.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1").drop("_rn")


# Orchestration --------------------------------------------------------------------------

def finalize(good: DataFrame) -> DataFrame:
    """Typed business columns plus lineage; raw strings and helpers dropped."""
    return good.select(*[c for c in good.columns if not c.startswith("_") or c in LINEAGE])


def run(lake: Lake, batch_id: str | None = None) -> list[EntityResult]:
    batch_id = batch_id or f"local-{uuid.uuid4()}"
    results: list[EntityResult] = []
    quarantines: list[DataFrame] = []

    def finish(entity: str, bronze: DataFrame, good: DataFrame, bad: DataFrame, table: str,
               control_rows: int = 0, bronze_rows: int | None = None) -> DataFrame:
        lake.write(finalize(good), table)
        written = lake.read(table)
        n_bronze = bronze.count() if bronze_rows is None else bronze_rows
        n_bad = bad.count()
        n_good = written.count()
        results.append(EntityResult(entity, n_bronze, control_rows, n_bad,
                                    n_bronze - control_rows - n_bad - n_good, n_good))
        quarantines.append(to_quarantine(bad, entity))
        return written

    b = lake.read("bronze_products")
    good, bad = silver_products(b)
    products_df = finish("products", b, good, bad, "silver_products")
    products = [r.product_code for r in products_df.select("product_code").collect()]

    b = lake.read("bronze_fx_rates")
    finish("fx_rates", b, *silver_fx_rates(b), "silver_fx_rates")

    b = lake.read("bronze_customers")
    finish("customers", b, *silver_customers(b), "silver_customers")

    b = lake.read("bronze_accounts")
    finish("accounts", b, *silver_accounts(b, products), "silver_accounts")

    b = lake.read("bronze_balances")
    body, trailers = split_trailers(b)
    lake.write(trailers, CONTROL_TOTALS)
    n_trl = trailers.count()
    finish("balances", b, *silver_balances(body, products), "silver_balances", control_rows=n_trl)

    b = lake.read("bronze_repayments")
    finish("repayments", b, *silver_repayments(b), "silver_repayments")

    b = lake.read("bronze_applications")
    events = finish("application_events", b, *silver_application_events(b, products), "silver_application_events")
    lake.write(current_applications(events), "silver_applications")

    lake.write(reduce(DataFrame.unionByName, quarantines), QUARANTINE)
    audit = lake.spark.createDataFrame(
        [(batch_id, r.entity, r.bronze_rows, r.control_rows, r.quarantined_rows, r.duplicate_rows, r.silver_rows)
         for r in results],
        "batch_id string, entity string, bronze_rows long, control_rows long, quarantined_rows long, "
        "duplicate_rows long, silver_rows long",
    ).withColumn("loaded_at", F.current_timestamp())
    lake.write(audit, AUDIT, mode="append")
    return results

