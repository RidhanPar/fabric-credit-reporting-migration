"""Run the medallion layers and the quality gates in order, timing each step.

Used by the local CLI and the tests. In Fabric the same functions are called by
the three notebooks, and the data pipeline provides the ordering.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict

from portfolio_migration.lakehouse import bronze, dq_checks, gold, quality, silver
from portfolio_migration.lakehouse.io import Lake


def _quality_summary(results) -> dict:
    return {
        "checks": len(results),
        "failed_error": len(quality.failures(results, quality.ERROR)),
        "failed_warn": len(quality.failures(results, quality.WARN)),
        "check_results": [asdict(r) for r in results],
    }


def run_all(lake: Lake, batch_id: str | None = None) -> dict:
    batch_id = batch_id or f"local-{uuid.uuid4()}"
    out: dict = {"batch_id": batch_id, "steps": {}}

    def step(name: str, fn):
        started = time.perf_counter()
        payload = fn()
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
        return payload

    step("gold", run_gold)
    return out
