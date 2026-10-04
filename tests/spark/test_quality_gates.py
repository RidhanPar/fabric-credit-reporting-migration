"""The gate must stop a bad gold build from reaching the published tables."""
import shutil

import pytest

from portfolio_migration.lakehouse import dq_checks, gold, quality
from portfolio_migration.lakehouse.io import LocalLake


@pytest.fixture
def mutable_lake(lake_run, spark, tmp_path):
    """A copy of the good lake, so a test can break it without affecting other tests."""
    lake, _, _ = lake_run
    root = tmp_path / "lake"
    shutil.copytree(lake.root, root)
    return LocalLake(spark, root, lake.landing_root)


def test_every_check_passes_on_a_clean_run(lake_run):
    _, summary, _ = lake_run
    silver = summary["steps"]["silver_quality"]
    gold_step = summary["steps"]["gold"]
    kpi_step = summary["steps"]["kpi_quality"]
    assert silver["checks"] == len(dq_checks.SILVER_CHECKS)
    assert gold_step["checks"] == len(dq_checks.GOLD_CHECKS)
    assert kpi_step["checks"] == len(dq_checks.KPI_CHECKS)
    for step in (silver, gold_step, kpi_step):
        assert step["failed_error"] == 0
        assert step["failed_warn"] == 0


def test_results_are_recorded_for_reporting(lake_run):
    lake, _, _ = lake_run
    results = lake.read(quality.RESULTS_TABLE).filter("batch_id = 'test-batch-1'")
    assert results.count() == len(dq_checks.ALL_CHECKS)
    by_layer = {r["layer"]: r["count"] for r in results.groupBy("layer").count().collect()}
    assert by_layer == {"silver": len(dq_checks.SILVER_CHECKS), "gold": len(dq_checks.GOLD_CHECKS),
                        "kpi": len(dq_checks.KPI_CHECKS)}
    assert set(results.select("kind").distinct().toPandas()["kind"]) == {
        "not_null", "unique", "accepted_values", "range", "referential_integrity", "expression",
        "reconciliation", "control_total", "metric"}
    assert results.filter("rows_checked = 0").count() == 0


def test_a_duplicate_in_silver_stops_gold_publishing(mutable_lake):
    before = mutable_lake.read("fact_balance_snapshot").count()
    duplicate = mutable_lake.read("silver_balances").limit(1)
    mutable_lake.write(duplicate, "silver_balances", mode="append")

    with pytest.raises(quality.DataQualityError) as err:
        gold.run(mutable_lake, "gate-test")
    assert "fact_balance_snapshot.unique" in str(err.value)

    # The published table is untouched: the report keeps yesterday's good data.
    assert mutable_lake.read("fact_balance_snapshot").count() == before
    # The bad data got as far as staging, which is where it was caught.
    assert mutable_lake.read("stg_fact_balance_snapshot").count() == before + 1

    failed = mutable_lake.read(quality.RESULTS_TABLE).filter("batch_id = 'gate-test' AND NOT passed").collect()
    assert [r["check_name"] for r in failed if r["severity"] == "ERROR"] == ["fact_balance_snapshot.unique"]
    assert failed[0]["rows_failed"] == 1


def test_a_missing_fx_rate_stops_gold_publishing(mutable_lake):
    fx = mutable_lake.read("silver_fx_rates")
    mutable_lake.write(fx.filter("month_end <> DATE'2026-08-31'"), "silver_fx_rates")
    with pytest.raises(quality.DataQualityError) as err:
        gold.run(mutable_lake, "gate-fx")
    message = str(err.value)
    assert "fact_balance_snapshot.not_null" in message and "balance_eur" in message


def test_warnings_do_not_stop_the_run(mutable_lake):
    strict = [dq_checks.quarantine_rate_at_most("balances", 0.0)]
    results = quality.run(mutable_lake, strict, "warn-test", gate=True)
    assert results[0].passed is False
    assert results[0].severity == quality.WARN
    assert "9 of 228308 rows quarantined" in results[0].detail


def test_control_totals_catch_a_decimal_parsing_bug(spark, tmp_path):
    """A wrong decimal separator keeps the row count and changes the money."""
    from datetime import datetime

    from pyspark.sql import functions as F

    lake = LocalLake(spark, tmp_path / "lake", tmp_path)
    rows = [("CZ00000001", "2026-08-31", "CZ", "PL_STD", "CZK", "100,50", "0", "ACTIVE"),
            ("TRL", "1", "100,50", None, None, None, None, None)]
    bronze = (spark.createDataFrame(rows, "account_id string, snapshot_date string, country_code string, "
                                         "product_code string, currency string, balance_local string, "
                                         "days_past_due string, account_status string")
              .withColumn("_country_feed", F.lit("CZ")).withColumn("_source_file", F.lit("balances_CZ_202608.csv"))
              .withColumn("_source_modified_at", F.lit(datetime(2026, 9, 1)))
              .withColumn("_ingested_at", F.lit(datetime(2026, 9, 2))).withColumn("_batch_id", F.lit("b1")))
    lake.write(bronze, "bronze_balances")
    from portfolio_migration.lakehouse.silver import split_trailers

    _, trailers = split_trailers(lake.read("bronze_balances"))
    lake.write(trailers, "silver_control_totals")

    check = dq_checks.balance_file_control_totals()
    assert quality.evaluate([check], lake.read)[0].passed

    # Now pretend a parser dropped the decimal comma, so 100,50 became 10050.00.
    # The row count still matches the trailer and the money does not.
    broken = lake.read("bronze_balances").withColumn(
        "balance_local", F.when(F.col("account_id") == "TRL", F.col("balance_local")).otherwise(F.lit("10050.00")))
    lake.write(broken, "bronze_balances")
    result = quality.evaluate([check], lake.read)[0]
    assert not result.passed
    assert "trailer 1 rows / 100.50" in result.detail
    assert "parsed 1 rows / 10050.00" in result.detail
