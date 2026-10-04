"""Command line entry point.

    python -m portfolio_migration generate [--out data] [--seed 42]
    python -m portfolio_migration lakehouse [--landing data/landing] [--lake build/lake]   (needs Spark)
    python -m portfolio_migration reconcile [--gold-kpis docs/results/gold_kpi_monthly.csv]
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


def reconcile(workbook: Path, gold_kpis: Path, out_dir: Path, findings: Path) -> dict:
    """Walk every legacy figure to its gold counterpart and explain every difference."""
    import pandas as pd

    from portfolio_migration.reconcile import bridge as br
    from portfolio_migration.reconcile import report

    t0 = time.perf_counter()
    stages = br.evaluate_legacy_stages(workbook, out_dir / "_workbooks")
    gold = br.restrict_to_window(pd.read_csv(gold_kpis, parse_dates=["month_end"]))
    bridge = br.build_bridge(stages, gold)
    attributions = br.attribution(bridge)
    summary = br.findings_summary(bridge, attributions)
    numbers = report.summary_numbers(bridge, attributions)
    numbers["seconds"] = round(time.perf_counter() - t0, 1)
    report.write_outputs(out_dir, bridge, attributions, summary, numbers)
    report.write_findings(findings, bridge, attributions, summary, numbers)
    closing = br.check_bridge_closes(bridge)
    numbers["bridge_rows_that_do_not_close"] = int(len(closing))
    return numbers


def main() -> None:
    parser = argparse.ArgumentParser(prog="portfolio_migration")
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="simulate the lender, write landed files and the legacy workbook")
    g.add_argument("--out", type=Path, default=Path("data"))
    g.add_argument("--seed", type=int, default=cfg.SEED)
    lh = sub.add_parser("lakehouse", help="run bronze, silver and gold on local Spark with Delta")
    lh.add_argument("--landing", type=Path, default=Path("data/landing"))
    lh.add_argument("--lake", type=Path, default=Path("build/lake"))
    lh.add_argument("--summary", type=Path, help="also write the run summary JSON here")
    lh.add_argument("--kpi-csv", type=Path, help="export the monthly KPI table to this CSV")

    rc = sub.add_parser("reconcile", help="reconcile the legacy workbook against the gold KPIs")
    rc.add_argument("--workbook", type=Path, default=Path("data/legacy/Monthly_Portfolio_Pack.xlsx"))
    rc.add_argument("--gold-kpis", type=Path, default=Path("docs/results/gold_kpi_monthly.csv"))
    rc.add_argument("--out", type=Path, default=Path("data/reconciliation"))
    rc.add_argument("--findings", type=Path, default=Path("docs/RECONCILIATION_FINDINGS.md"))
    args = parser.parse_args()
    if args.cmd == "generate":
        print(json.dumps(generate(args.out, args.seed), indent=2))
    elif args.cmd == "lakehouse":
        from portfolio_migration.lakehouse.io import LocalLake, local_spark
        from portfolio_migration.lakehouse.runner import run_all

        lake = LocalLake(local_spark(), args.lake, args.landing)
        result = run_all(lake)
        if args.kpi_csv:
            from portfolio_migration.lakehouse.kpis import KPI_TABLE

            args.kpi_csv.parent.mkdir(parents=True, exist_ok=True)
            (lake.read(KPI_TABLE).toPandas()
             .sort_values(["month_end", "scope", "kpi", "variant"])
             .to_csv(args.kpi_csv, index=False))
        summary = json.dumps(result, indent=2)
        if args.summary:
            args.summary.write_text(summary + "\n", encoding="utf-8")
        print(summary)
    elif args.cmd == "reconcile":
        print(json.dumps(reconcile(args.workbook, args.gold_kpis, args.out, args.findings), indent=2))


if __name__ == "__main__":
    main()
