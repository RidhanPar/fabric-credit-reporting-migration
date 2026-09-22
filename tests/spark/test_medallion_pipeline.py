"""End to end: generated landing files through bronze, silver and gold.

The truth is known, so silver and gold must reproduce it exactly, and every
bronze row must be accounted for.
"""
import pandas as pd

from portfolio_migration.lakehouse import bronze, gold, silver


def _audit(lake, batch="test-batch-1"):
    return {r.entity: r for r in lake.read(silver.AUDIT).filter(f"batch_id = '{batch}'").collect()}


def test_every_bronze_row_is_accounted_for(lake_run):
    lake, _, _ = lake_run
    for r in _audit(lake).values():
        assert r.bronze_rows == r.control_rows + r.quarantined_rows + r.duplicate_rows + r.silver_rows, r.entity
        assert r.duplicate_rows >= 0, r.entity


def test_injected_defects_are_all_caught(lake_run):
    lake, _, manifest = lake_run
    a, d = _audit(lake), manifest.defects
    assert a["balances"].control_rows == 73
    assert a["balances"].quarantined_rows == d["blank_account_id"]
    assert a["balances"].duplicate_rows == d["resent_balance_file_rows"]
    assert a["repayments"].duplicate_rows == d["duplicate_repayment_rows"]
    assert a["application_events"].quarantined_rows == d["truncated_json_line"]
    q = {(r.entity, r.reasons.split(",")[0]): r["count"]
         for r in lake.read(silver.QUARANTINE).groupBy("entity", "reasons").count().collect()}
    assert q == {("balances", "MISSING_ACCOUNT_ID"): d["blank_account_id"],
                 ("application_events", "MALFORMED_JSON"): d["truncated_json_line"]}


def test_silver_balances_equal_the_truth(lake_run, core):
    lake, _, _ = lake_run
    got = lake.read("silver_balances").select(
        "account_id", "snapshot_date", "product_code", "balance_local", "days_past_due", "account_status").toPandas()
    got["balance_local"] = got.balance_local.astype(float)
    truth = core.balances[["account_id", "snapshot_date", "product_code", "balance_local",
                           "days_past_due", "account_status"]]
    m = truth.merge(got, on=["account_id", "snapshot_date"], how="outer", suffixes=("_t", "_s"), indicator=True)
    assert (m._merge == "both").all()
    assert (m.product_code_t == m.product_code_s).all()
    assert ((m.balance_local_t - m.balance_local_s).abs() < 0.005).all()
    assert (m.days_past_due_t == m.days_past_due_s).all()
    assert (m.account_status_t == m.account_status_s).all()


def test_silver_applications_equal_the_truth(lake_run, core):
    lake, _, _ = lake_run
    got = lake.read("silver_applications").select("application_id", "status", "product_code").toPandas()
    m = core.applications.merge(got, on="application_id", suffixes=("_t", "_s"))
    assert len(m) == len(core.applications) == len(got)
    assert (m.status_t == m.status_s).all() and (m.product_code_t == m.product_code_s).all()


def test_control_totals_match_the_rows_in_each_file(lake_run):
    lake, _, _ = lake_run
    per_file = (lake.read("bronze_balances").filter("account_id <> 'TRL' OR account_id IS NULL")
                .groupBy("_source_file").count().withColumnRenamed("_source_file", "source_file"))
    ct = lake.read(silver.CONTROL_TOTALS).join(per_file, "source_file").collect()
    assert len(ct) == 73
    assert all(r.expected_rows == r["count"] for r in ct)


def test_gold_facts_are_complete_and_converted(lake_run, core):
    lake, _, _ = lake_run
    f = lake.read("fact_balance_snapshot")
    assert f.count() == len(core.balances)
    assert f.filter("balance_eur IS NULL OR fx_rate IS NULL OR customer_id IS NULL").count() == 0
    assert f.filter("is_active AND dpd_bucket IS NULL").count() == 0
    assert lake.read("fact_application").count() == len(core.applications)
    assert lake.read("fact_origination").filter("amount_eur IS NULL").count() == 0
    assert lake.read("fact_repayment").count() == len(core.repayments)
    total = float(f.filter("is_active").agg({"balance_local": "sum"}).collect()[0][0])
    truth = core.balances[core.balances.account_status == "ACTIVE"].balance_local.sum()
    assert abs(total - truth) < 0.01


def test_gold_keys_resolve_to_dimensions(lake_run):
    lake, _, _ = lake_run
    for fact in ("fact_balance_snapshot", "fact_origination", "fact_repayment"):
        f = lake.read(fact)
        assert f.join(lake.read("dim_account"), "account_id", "left_anti").count() == 0, fact
        assert f.join(lake.read("dim_product"), "product_code", "left_anti").count() == 0, fact
        assert f.join(lake.read("dim_date"), "date_key", "left_anti").count() == 0, fact


def test_rerunning_is_idempotent(lake_run):
    lake, summary, _ = lake_run
    assert all(r.new_rows == 0 for r in bronze.run(lake, "test-batch-2"))
    silver.run(lake, "test-batch-2")
    gold.run(lake, "test-batch-2")
    first = {r["table"]: r["rows"] for r in summary["layers"]["gold"]["results"]}
    assert {t: lake.read(t).count() for t in gold.GOLD_TABLES} == first
    runs = pd.DataFrame([r.asDict() for r in lake.read(silver.AUDIT).collect()])
    by_batch = runs.pivot_table(index="entity", columns="batch_id", values="silver_rows")
    assert (by_batch["test-batch-1"] == by_batch["test-batch-2"]).all()
