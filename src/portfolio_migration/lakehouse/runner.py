"""Run the medallion layers, the quality gates and the monitoring in order.

Used by the local CLI and the tests. In Fabric the same functions are called by
the notebooks, and the data pipeline provides the ordering. Either way every step
writes a row to ``ops_pipeline_run``, so a failed run can be read back without
opening a notebook.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta
from datetime import time as clock_time

from portfolio_migration import config as cfg
from portfolio_migration.lakehouse import bronze, dq_checks, gold, kpis, ops, quality, silver
from portfolio_migration.lakehouse.io import Lake


def _quality_summary(results) -> dict:
    return {
        "checks": len(results),
        "failed_error": len(quality.failures(results, quality.ERROR)),
        "failed_warn": len(quality.failures(results, quality.WARN)),
        "check_results": [asdict(r) for r in results],
    }


def simulated_close() -> datetime:
    """The morning after the last month end in the synthetic data.

    Freshness is measured against a clock. The generated data deliberately stops at
    a fixed month end, so a local run measures freshness as if it were the morning
    after that close. In Fabric the real clock is used.
    """
    return datetime.combine(cfg.reporting_months()[-1] + timedelta(days=1), clock_time(5, 0))


def run_all(lake: Lake, batch_id: str | None = None, as_of: datetime | None = None) -> dict:
    batch_id = batch_id or f"local-{uuid.uuid4()}"
    out: dict = {"batch_id": batch_id, "steps": {}}

    def step(name: str, fn):
        started = time.perf_counter()
        with ops.logged_step(lake, batch_id, name) as outcome:
            payload = fn()
            outcome["rows"] = payload.get("rows", 0)
            outcome["detail"] = payload.get("detail", "")
        payload["seconds"] = round(time.perf_counter() - started, 1)
        out["steps"][name] = payload

    step("bronze", lambda: {"tables": [asdict(r) for r in bronze.run(lake, batch_id)]})
    step("silver", lambda: {"entities": [asdict(r) for r in silver.run(lake, batch_id)]})
    step("silver_quality", lambda: _quality_summary(
        quality.run(lake, dq_checks.SILVER_CHECKS, batch_id, gate=True)))

    # Gold writes to staging, runs the gold checks there, and publishes only if they pass.
    def run_gold() -> dict:
        result = gold.run(lake, batch_id)
        payload = {"tables": [asdict(r) for r in result.tables]}
        payload.update(_quality_summary(result.checks))
        payload["rows"] = sum(t["rows"] for t in payload["tables"])
        return payload

    step("gold", run_gold)
    # The monthly KPI table is what the reconciliation and the semantic model read.
    step("kpis", lambda: {"rows": kpis.run(lake, batch_id).rows})
    step("kpi_quality", lambda: _quality_summary(
        quality.run(lake, dq_checks.KPI_CHECKS, batch_id, gate=True)))
    # Freshness and alerts last, so they see this run's own log rows.
    step("monitor", lambda: ops.monitor(lake, batch_id, as_of or simulated_close()))
    return out
