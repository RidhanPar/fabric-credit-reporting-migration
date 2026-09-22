"""Command line entry point.

    python -m portfolio_migration generate [--out data] [--seed 42]
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from portfolio_migration import config as cfg
from portfolio_migration.generate.core import simulate
from portfolio_migration.generate.landing import write_landing
from portfolio_migration.legacy.evaluate import published_kpis
from portfolio_migration.legacy.workbook import build_workbook

TRUTH_TABLES = ("products", "fx_rates", "customers", "applications", "accounts", "balances", "repayments")


def generate(out: Path, seed: int) -> dict:
    timings = {}
    t0 = time.perf_counter()
    core = simulate(seed)
    timings["simulate_s"] = round(time.perf_counter() - t0, 1)

    landing = out / "landing"
    if landing.exists():
        shutil.rmtree(landing)
    t = time.perf_counter()
    manifest = write_landing(core, landing, seed)
    timings["write_landing_s"] = round(time.perf_counter() - t, 1)

    # Test oracle only. Never uploaded to Fabric: the lakehouse must rebuild this from the landed files.
    truth = out / "_truth"
    truth.mkdir(parents=True, exist_ok=True)
    for name in TRUTH_TABLES:
        getattr(core, name).to_parquet(truth / f"{name}.parquet", index=False)

    t = time.perf_counter()
    wb = build_workbook(core, out / "legacy" / "Monthly_Portfolio_Pack.xlsx")
    kpis = published_kpis(wb)
    kpis.to_csv(out / "legacy" / "legacy_published_kpis.csv", index=False)
    timings["workbook_and_recalc_s"] = round(time.perf_counter() - t, 1)

    summary = {
        "seed": seed,
        "reporting_window": [str(cfg.reporting_months()[0]), str(cfg.reporting_months()[-1])],
        "truth_rows": {name: len(getattr(core, name)) for name in TRUTH_TABLES},
        "landing": asdict(manifest),
        "legacy_published_kpi_cells": len(kpis),
        "timings": timings,
    }
    (out / "generation_manifest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(prog="portfolio_migration")
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="simulate the lender, write landed files and the legacy workbook")
    g.add_argument("--out", type=Path, default=Path("data"))
    g.add_argument("--seed", type=int, default=cfg.SEED)
    args = parser.parse_args()
    if args.cmd == "generate":
        print(json.dumps(generate(args.out, args.seed), indent=2))


if __name__ == "__main__":
    main()
