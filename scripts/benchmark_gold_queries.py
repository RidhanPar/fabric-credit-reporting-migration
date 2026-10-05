"""Time the queries the report pages will run, against the local gold tables.

These are the aggregations behind the visuals in docs/REPORT_SPEC.md, written as
Spark SQL over the same gold tables the semantic model reads. They are a local
baseline on two cores, not Direct Lake timings: Direct Lake runs in the Power BI
engine on a Fabric capacity, and those numbers go in the README only once they
have been measured there.

    docker run --rm -v "%cd%:/repo" -e PYTHONPATH=/repo/src portfolio-spark:3.5 \\
        python scripts/benchmark_gold_queries.py --lake /tmp/lake --out docs/results/phase6_query_benchmark.json
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from pathlib import Path

QUERIES: dict[str, tuple[str, str]] = {
    "exec_balance_by_month": (
        "Executive summary, portfolio balance by month and country",
        """
        SELECT d.year_month, f.country_code, SUM(f.balance_eur) AS balance_eur
        FROM fact_balance_snapshot f JOIN dim_date d ON d.date_key = f.date_key
        WHERE f.is_active
        GROUP BY d.year_month, f.country_code
        ORDER BY d.year_month, f.country_code
        """),
    "exec_country_summary": (
        "Executive summary, the country table for the latest month end",
        """
        SELECT f.country_code,
               SUM(f.balance_eur) AS balance_eur,
               COUNT(DISTINCT f.customer_id) AS active_customers,
               SUM(CASE WHEN f.is_dpd30 THEN f.balance_eur ELSE 0 END) / SUM(f.balance_eur) AS dpd30_rate
        FROM fact_balance_snapshot f
        WHERE f.is_active AND f.date_key = (SELECT MAX(date_key) FROM fact_balance_snapshot)
        GROUP BY f.country_code
        """),
    "delinquency_trend": (
        "Delinquency page, 30+ and 90+ rates by month and country",
        """
        SELECT d.year_month, f.country_code,
               SUM(CASE WHEN f.is_dpd30 THEN f.balance_eur ELSE 0 END) / SUM(f.balance_eur) AS dpd30_rate,
               SUM(CASE WHEN f.is_dpd90 THEN f.balance_eur ELSE 0 END) / SUM(f.balance_eur) AS dpd90_rate
        FROM fact_balance_snapshot f JOIN dim_date d ON d.date_key = f.date_key
        WHERE f.is_active
        GROUP BY d.year_month, f.country_code
        """),
    "delinquency_buckets": (
        "Delinquency page, balance by DPD bucket and month",
        """
        SELECT d.year_month, f.dpd_bucket, SUM(f.balance_eur) AS balance_eur
        FROM fact_balance_snapshot f JOIN dim_date d ON d.date_key = f.date_key
        WHERE f.is_active
        GROUP BY d.year_month, f.dpd_bucket
        """),
    "delinquency_matrix": (
        "Delinquency page, country and product matrix with rates",
        """
        SELECT c.country_name, p.product_name,
               SUM(f.balance_eur) AS balance_eur,
               SUM(CASE WHEN f.is_dpd30 THEN f.balance_eur ELSE 0 END) / SUM(f.balance_eur) AS dpd30_rate
        FROM fact_balance_snapshot f
        JOIN dim_country c ON c.country_code = f.country_code
        JOIN dim_product p ON p.product_code = f.product_code
        WHERE f.is_active AND f.date_key = (SELECT MAX(date_key) FROM fact_balance_snapshot)
        GROUP BY c.country_name, p.product_name
        """),
    "account_drillthrough": (
        "Account detail page, one account's balance and payment history",
        """
        SELECT d.year_month, f.days_past_due, f.balance_eur,
               (SELECT SUM(r.amount_eur) FROM fact_repayment r
                WHERE r.account_id = f.account_id AND r.date_key <= f.date_key) AS paid_to_date
        FROM fact_balance_snapshot f JOIN dim_date d ON d.date_key = f.date_key
        WHERE f.account_id = (SELECT MAX(account_id) FROM fact_balance_snapshot)
        ORDER BY d.year_month
        """),
    "kpi_table_scan": (
        "Migration evidence page, the whole monthly KPI table",
        "SELECT kpi, variant, scope, COUNT(*) AS months, AVG(value) AS mean_value "
        "FROM gold_kpi_monthly GROUP BY kpi, variant, scope"),
}
REPEATS = 5


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lake", type=Path, default=Path("build/lake"))
    parser.add_argument("--landing", type=Path, default=Path("data/landing"))
    parser.add_argument("--out", type=Path, default=Path("docs/results/phase6_query_benchmark.json"))
    parser.add_argument("--repeats", type=int, default=REPEATS)
    args = parser.parse_args()

    from portfolio_migration.lakehouse.io import LocalLake, local_spark

    spark = local_spark("benchmark")
    spark.sparkContext.setLogLevel("ERROR")
    lake = LocalLake(spark, args.lake, args.landing)

    for table in ("fact_balance_snapshot", "fact_repayment", "dim_date", "dim_country", "dim_product",
                  "gold_kpi_monthly"):
        lake.read(table).createOrReplaceTempView(table)

    results = {}
    for name, (description, sql) in QUERIES.items():
        timings = []
        rows = 0
        for _ in range(args.repeats):
            started = time.perf_counter()
            rows = spark.sql(sql).count()
            timings.append((time.perf_counter() - started) * 1000)
        results[name] = {
            "description": description,
            "rows_returned": rows,
            "runs": args.repeats,
            "first_run_ms": round(timings[0], 1),
            "median_ms": round(statistics.median(timings), 1),
            "best_ms": round(min(timings), 1),
        }
        print(f"{name:26} median {results[name]['median_ms']:8.1f} ms  rows {rows}")

    payload = {
        "measured_on": "local Spark in Docker, not Direct Lake",
        "environment": {
            "spark": spark.version,
            "python": platform.python_version(),
            "cores": spark.sparkContext.defaultParallelism,
        },
        "fact_balance_snapshot_rows": lake.read("fact_balance_snapshot").count(),
        "queries": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"written to {args.out}")


if __name__ == "__main__":
    main()
