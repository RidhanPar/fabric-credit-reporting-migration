"""Operations: a run log, freshness checks and alert conditions.

Three tables, all reportable:

* ``ops_pipeline_run``  one row per step per run: when, how long, status, rows written.
* ``ops_freshness``     the age of the newest data, per source, against an agreed limit.
* ``ops_alerts``        the alert conditions, evaluated, with the action to take.

The conditions live here, in code that is tested, because that is the part that
has to be right. Where an alert is delivered (a pipeline failure path, an
Activator rule, email) is portal configuration and is documented in
docs/fabric-steps/phase-6.md.

A green run on stale data is the failure mode that embarrasses you in a meeting,
so run status and freshness are separate conditions.
"""
from __future__ import annotations

import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from portfolio_migration.lakehouse import quality, silver
from portfolio_migration.lakehouse.io import Lake

RUN_LOG = "ops_pipeline_run"
FRESHNESS_TABLE = "ops_freshness"
ALERTS_TABLE = "ops_alerts"

SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
CRITICAL = "CRITICAL"
WARNING = "WARNING"

RUN_LOG_SCHEMA = ("batch_id string, step string, status string, seconds double, rows_written long, "
                  "detail string, started_at timestamp, finished_at timestamp")


class CriticalAlert(Exception):
    """A critical alert fired. Raised so the pipeline run goes red."""


def _now() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


# Run log -----------------------------------------------------------------------------

def log_step(lake: Lake, batch_id: str, step: str, status: str, seconds: float,
             rows_written: int, detail: str, started_at: datetime, finished_at: datetime) -> None:
    row = [(batch_id, step, status, float(seconds), int(rows_written), detail, started_at, finished_at)]
    lake.write(lake.spark.createDataFrame(row, RUN_LOG_SCHEMA), RUN_LOG, mode="append")


@contextmanager
def logged_step(lake: Lake, batch_id: str, step: str) -> Iterator[dict]:
    """Run a step, and log it either way.

    Yields a dict the caller can put ``rows`` and ``detail`` into. On an exception
    the step is logged as FAILED with the error, and the exception is re-raised so
    the pipeline stops.
    """
    outcome: dict = {"rows": 0, "detail": ""}
    started, clock = _now(), time.perf_counter()
    try:
        yield outcome
    except Exception as exc:
        log_step(lake, batch_id, step, FAILED, round(time.perf_counter() - clock, 1), 0,
                 f"{type(exc).__name__}: {exc}"[:900] + "\n" + traceback.format_exc()[-600:],
                 started, _now())
        raise
    log_step(lake, batch_id, step, SUCCEEDED, round(time.perf_counter() - clock, 1),
             int(outcome.get("rows", 0)), str(outcome.get("detail", ""))[:900], started, _now())


# Freshness ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Freshness:
    name: str
    table: str
    column: str
    max_age_hours: float
    description: str
    row_filter: str = ""


FRESHNESS_CHECKS: tuple[Freshness, ...] = (
    Freshness("gold_month_end", "fact_balance_snapshot", "snapshot_date", 24 * 45,
              "Newest month end in gold. The pack is monthly, so 45 days allows for a late close."),
    Freshness("silver_loaded", silver.AUDIT, "loaded_at", 36,
              "When silver last loaded. A daily pipeline that has not run in 36 hours is a problem."),
    Freshness("quality_checked", quality.RESULTS_TABLE, "checked_at", 36,
              "When the quality checks last ran. Gold should never be newer than its audit."),
    Freshness("pipeline_succeeded", RUN_LOG, "finished_at", 36,
              "When a run last finished successfully.", row_filter=f"status = '{SUCCEEDED}'"),
)


def freshness(lake: Lake, as_of: datetime | None = None) -> DataFrame:
    """One row per freshness check: newest value, its age, the limit, and whether it is stale."""
    as_of = as_of or _now()
    rows = []
    for check in FRESHNESS_CHECKS:
        latest = None
        if lake.exists(check.table):
            df = lake.read(check.table)
            if check.row_filter:
                df = df.filter(check.row_filter)
            latest = df.agg(F.max(check.column)).collect()[0][0]
        if latest is None:
            rows.append((check.name, check.table, check.column, None, None, float(check.max_age_hours),
                         True, "no data found", check.description))
            continue
        latest_dt = datetime.combine(latest, datetime.min.time()) if not isinstance(latest, datetime) else latest
        age_hours = (as_of - latest_dt).total_seconds() / 3600
        rows.append((check.name, check.table, check.column, latest_dt, round(age_hours, 2),
                     float(check.max_age_hours), age_hours > check.max_age_hours,
                     f"newest {check.column} is {latest_dt:%Y-%m-%d %H:%M} UTC", check.description))
    schema = ("check_name string, table_name string, column_name string, latest_value timestamp, "
              "age_hours double, max_age_hours double, is_stale boolean, detail string, description string")
    return (lake.spark.createDataFrame(rows, schema)
            .withColumn("measured_at", F.lit(as_of).cast("timestamp")))


# Alerts ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Alert:
    id: str
    severity: str
    title: str
    action: str
    evaluate: Callable[[Lake, DataFrame], tuple[bool, str]]


def _latest_batch(lake: Lake, table: str, order_column: str) -> str | None:
    if not lake.exists(table):
        return None
    row = lake.read(table).orderBy(F.col(order_column).desc()).limit(1).collect()
    return row[0]["batch_id"] if row else None


def _a_step_failed(lake: Lake, _: DataFrame) -> tuple[bool, str]:
    batch = _latest_batch(lake, RUN_LOG, "finished_at")
    if batch is None:
        return True, "no pipeline run has been recorded"
    failed = lake.read(RUN_LOG).filter(f"batch_id = '{batch}' AND status = '{FAILED}'").collect()
    if not failed:
        return False, f"every step of batch {batch} succeeded"
    steps = ", ".join(r["step"] for r in failed)
    return True, f"batch {batch} failed at: {steps}"


def _data_is_stale(_: Lake, fresh: DataFrame) -> tuple[bool, str]:
    stale = fresh.filter("is_stale").collect()
    if not stale:
        return False, "every source is inside its freshness limit"
    worst = ", ".join(f"{r['check_name']} ({r['detail']})" for r in stale)
    return True, f"stale: {worst}"


def _quality_error(lake: Lake, _: DataFrame) -> tuple[bool, str]:
    batch = _latest_batch(lake, quality.RESULTS_TABLE, "checked_at")
    if batch is None:
        return True, "no quality checks have been recorded"
    failed = lake.read(quality.RESULTS_TABLE).filter(
        f"batch_id = '{batch}' AND NOT passed AND severity = '{quality.ERROR}'").collect()
    if not failed:
        return False, f"no ERROR check failed in batch {batch}"
    names = ", ".join(r["check_name"] for r in failed[:5])
    return True, f"{len(failed)} ERROR check(s) failed in batch {batch}: {names}"


def _quality_warning(lake: Lake, _: DataFrame) -> tuple[bool, str]:
    batch = _latest_batch(lake, quality.RESULTS_TABLE, "checked_at")
    if batch is None:
        return False, "no quality checks have been recorded"
    warned = lake.read(quality.RESULTS_TABLE).filter(
        f"batch_id = '{batch}' AND NOT passed AND severity = '{quality.WARN}'").collect()
    if not warned:
        return False, f"no warnings in batch {batch}"
    return True, f"{len(warned)} warning(s) in batch {batch}: " + ", ".join(r["check_name"] for r in warned[:5])


def _gold_behind_silver(lake: Lake, _: DataFrame) -> tuple[bool, str]:
    if not lake.exists(RUN_LOG):
        return True, "no pipeline run has been recorded"
    log = lake.read(RUN_LOG).filter(f"status = '{SUCCEEDED}'")
    latest = {r["step"]: r["finished_at"] for r in
              log.groupBy("step").agg(F.max("finished_at").alias("finished_at")).collect()}
    silver_at, gold_at = latest.get("silver"), latest.get("gold")
    if silver_at is None or gold_at is None:
        return True, "silver or gold has never completed successfully"
    if gold_at >= silver_at:
        return False, f"gold published at {gold_at:%Y-%m-%d %H:%M}, after silver at {silver_at:%H:%M}"
    return True, (f"silver loaded at {silver_at:%Y-%m-%d %H:%M} but gold last published at "
                  f"{gold_at:%Y-%m-%d %H:%M}, so the report is behind the data")


def _reconciliation_unexplained(lake: Lake, _: DataFrame) -> tuple[bool, str]:
    if not lake.exists("recon_attribution"):
        return False, "the reconciliation has not been run in this lakehouse"
    rows = lake.read("recon_attribution").filter("classification = 'UNEXPLAINED'").count()
    if rows == 0:
        return False, "every difference against the legacy pack has a named cause"
    return True, f"{rows} published figure(s) differ from the legacy pack with no named cause"


ALERTS: tuple[Alert, ...] = (
    Alert("PIPELINE_FAILED", CRITICAL, "A pipeline step failed",
          "Open the run log, find the failed step, read its detail, fix and rerun. Every layer is "
          "idempotent, so a rerun is safe.", _a_step_failed),
    Alert("DATA_STALE", CRITICAL, "Data is older than agreed",
          "Check whether the source files landed. A green run on stale data usually means the source "
          "never delivered.", _data_is_stale),
    Alert("QUALITY_ERROR", CRITICAL, "An ERROR quality check failed",
          "Gold did not publish, so the report is showing the previous day's data. Read dq_results for "
          "the failing check and its sample rows.", _quality_error),
    Alert("GOLD_BEHIND_SILVER", CRITICAL, "Gold is behind silver",
          "Silver loaded but gold did not publish, which means the audit stopped it. Treat as above.",
          _gold_behind_silver),
    Alert("RECONCILIATION_UNEXPLAINED", CRITICAL, "The reconciliation does not close",
          "A difference against the legacy pack has no named cause. Do not sign off the month until it "
          "is explained.", _reconciliation_unexplained),
    Alert("QUALITY_WARNING", WARNING, "A warning level quality check failed",
          "No action tonight. Review in the weekly data quality review.", _quality_warning),
)


def evaluate_alerts(lake: Lake, fresh: DataFrame) -> list[tuple[Alert, bool, str]]:
    return [(alert, *alert.evaluate(lake, fresh)) for alert in ALERTS]


def monitor(lake: Lake, batch_id: str, as_of: datetime | None = None, gate: bool = True) -> dict:
    """Write the freshness and alert tables, and raise if a critical alert fired."""
    fresh = freshness(lake, as_of).cache()
    lake.write(fresh, FRESHNESS_TABLE)
    results = evaluate_alerts(lake, fresh)
    rows = [(batch_id, alert.id, alert.severity, alert.title, fired, detail, alert.action)
            for alert, fired, detail in results]
    alerts_df = (lake.spark.createDataFrame(
        rows, "batch_id string, alert_id string, severity string, title string, fired boolean, "
              "detail string, action string")
        .withColumn("evaluated_at", F.current_timestamp()))
    lake.write(alerts_df, ALERTS_TABLE, mode="append")

    firing = [(a, d) for a, fired, d in results if fired]
    summary = {
        "alerts_evaluated": len(results),
        "fired": [{"alert_id": a.id, "severity": a.severity, "detail": d} for a, d in firing],
        "stale_sources": [r["check_name"] for r in fresh.filter("is_stale").collect()],
    }
    fresh.unpersist()
    critical = [f"{a.id}: {d}" for a, d in firing if a.severity == CRITICAL]
    if critical and gate:
        raise CriticalAlert(f"{len(critical)} critical alert(s):\n" + "\n".join(critical))
    return summary


def hours(value: float) -> timedelta:
    return timedelta(hours=value)
