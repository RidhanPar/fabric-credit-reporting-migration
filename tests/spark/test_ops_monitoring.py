"""The run log, the freshness checks and the alert conditions.

The alerts are the part that has to be right, because nobody checks a dashboard at
04:00. Each condition is proved by breaking the lakehouse on a copy and showing the
alert fires, and by showing it stays quiet on a clean run.
"""
from datetime import datetime, timedelta

import pytest

from portfolio_migration.lakehouse import ops
from portfolio_migration.lakehouse.runner import simulated_close

EXPECTED_STEPS = ["bronze", "silver", "silver_quality", "gold", "kpis", "kpi_quality", "monitor"]


def test_the_run_log_has_a_row_for_every_step(lake_run):
    lake, summary, _ = lake_run
    log = lake.read(ops.RUN_LOG).filter(f"batch_id = '{summary['batch_id']}'").collect()
    assert sorted(r["step"] for r in log) == sorted(EXPECTED_STEPS)
    for row in log:
        assert row["status"] == ops.SUCCEEDED, row["step"]
        assert row["seconds"] > 0, row["step"]
        assert row["started_at"] <= row["finished_at"], row["step"]
    gold = [r for r in log if r["step"] == "gold"][0]
    assert gold["rows_written"] > 0


def test_a_failing_step_is_logged_and_the_error_is_not_swallowed(mutable_lake):
    with pytest.raises(ValueError, match="source file was empty"):
        with ops.logged_step(mutable_lake, "failing-batch", "silver"):
            raise ValueError("source file was empty")
    row = mutable_lake.read(ops.RUN_LOG).filter("batch_id = 'failing-batch'").collect()[0]
    assert row["status"] == ops.FAILED
    assert "ValueError: source file was empty" in row["detail"]


def test_freshness_measures_the_age_of_the_data_not_the_run(lake_run):
    lake, _, _ = lake_run
    fresh = {r["check_name"]: r for r in ops.freshness(lake, simulated_close()).collect()}
    assert set(fresh) == {c.name for c in ops.FRESHNESS_CHECKS}
    assert not any(r["is_stale"] for r in fresh.values())
    # The newest month end in gold is the last month of the generated window.
    assert fresh["gold_month_end"]["latest_value"].date().isoformat() == "2026-08-31"


def test_freshness_goes_stale_when_nothing_new_arrives(lake_run):
    lake, _, _ = lake_run
    much_later = simulated_close() + timedelta(days=120)
    stale = [r["check_name"] for r in ops.freshness(lake, much_later).collect() if r["is_stale"]]
    assert set(stale) == {c.name for c in ops.FRESHNESS_CHECKS}


def test_no_alert_fires_on_a_clean_run(lake_run):
    lake, _, _ = lake_run
    fresh = ops.freshness(lake, simulated_close())
    fired = [(a.id, detail) for a, flag, detail in ops.evaluate_alerts(lake, fresh) if flag]
    assert fired == []


def test_a_failed_step_fires_a_critical_alert(mutable_lake):
    ops.log_step(mutable_lake, "broken-batch", "silver", ops.FAILED, 3.0, 0,
                 "OSError: the file never arrived", datetime(2026, 9, 1, 4), datetime(2026, 9, 1, 4, 1))
    with pytest.raises(ops.CriticalAlert, match="PIPELINE_FAILED"):
        ops.monitor(mutable_lake, "broken-batch", simulated_close())
    alerts = {r["alert_id"]: r for r in
              mutable_lake.read(ops.ALERTS_TABLE).filter("batch_id = 'broken-batch'").collect()}
    assert alerts["PIPELINE_FAILED"]["fired"]
    assert "silver" in alerts["PIPELINE_FAILED"]["detail"]
    assert alerts["PIPELINE_FAILED"]["action"]


def test_stale_data_fires_a_critical_alert_even_though_every_run_succeeded(mutable_lake):
    """The failure mode that embarrasses you: green runs, month old numbers."""
    much_later = simulated_close() + timedelta(days=120)
    with pytest.raises(ops.CriticalAlert, match="DATA_STALE"):
        ops.monitor(mutable_lake, "stale-batch", much_later)
    stale = mutable_lake.read(ops.FRESHNESS_TABLE).filter("is_stale").count()
    assert stale == len(ops.FRESHNESS_CHECKS)


def test_gold_falling_behind_silver_fires_an_alert(mutable_lake):
    """Silver loaded, gold did not publish, so the report is behind the data."""
    ops.log_step(mutable_lake, "late-silver", "silver", ops.SUCCEEDED, 10.0, 100, "",
                 datetime(2026, 9, 2, 4), datetime(2026, 9, 2, 4, 5))
    fresh = ops.freshness(mutable_lake, simulated_close())
    fired = {a.id: detail for a, flag, detail in ops.evaluate_alerts(mutable_lake, fresh) if flag}
    assert "GOLD_BEHIND_SILVER" in fired
    assert "behind the data" in fired["GOLD_BEHIND_SILVER"]


def test_a_failed_error_check_fires_an_alert(mutable_lake):
    from portfolio_migration.lakehouse import quality

    result = quality.CheckResult("fact_balance_snapshot.unique", "gold", "fact_balance_snapshot",
                                 "unique", quality.ERROR, 100, 2, False, "two duplicates", 0.1)
    quality.write_results(mutable_lake, [result], "bad-quality-batch")
    fresh = ops.freshness(mutable_lake, simulated_close())
    fired = {a.id: detail for a, flag, detail in ops.evaluate_alerts(mutable_lake, fresh) if flag}
    assert "QUALITY_ERROR" in fired
    assert "fact_balance_snapshot.unique" in fired["QUALITY_ERROR"]


def test_every_alert_says_what_to_do_about_it(lake_run):
    lake, _, _ = lake_run
    assert {a.severity for a in ops.ALERTS} <= {ops.CRITICAL, ops.WARNING}
    for alert in ops.ALERTS:
        assert alert.title and alert.action, alert.id
        assert len(alert.action) > 40, alert.id
    rows = lake.read(ops.ALERTS_TABLE).collect()
    assert {r["alert_id"] for r in rows} == {a.id for a in ops.ALERTS}
