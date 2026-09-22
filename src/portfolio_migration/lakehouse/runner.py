"""Run the medallion layers in order, timing each one. Used by the local CLI and the tests.

In Fabric the same three ``run`` functions are called by three notebooks, and the
data pipeline provides the ordering.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict

from portfolio_migration.lakehouse import bronze, gold, silver
from portfolio_migration.lakehouse.io import Lake


def run_all(lake: Lake, batch_id: str | None = None) -> dict:
    batch_id = batch_id or f"local-{uuid.uuid4()}"
    out: dict = {"batch_id": batch_id, "layers": {}}
    for name, layer in (("bronze", bronze), ("silver", silver), ("gold", gold)):
        t = time.perf_counter()
        results = layer.run(lake, batch_id)
        out["layers"][name] = {"seconds": round(time.perf_counter() - t, 1),
                               "results": [asdict(r) for r in results]}
    return out
