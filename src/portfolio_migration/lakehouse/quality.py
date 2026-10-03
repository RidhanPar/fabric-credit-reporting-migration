"""Data quality engine: declarative checks, severities, results table, publish gate.

A check is data, not code: its type, table, columns and severity. The engine
knows how to run each type, so adding a rule cannot break the engine, and a
reviewer can read the rule list without reading Python.

Every run appends to ``dq_results``. If any ERROR check fails, ``run`` raises
``DataQualityError`` after writing the results, which stops the pipeline. Gold
uses that to decide whether to publish (see ``gold.run``).
"""
from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from portfolio_migration.lakehouse.io import Lake

ERROR = "ERROR"
WARN = "WARN"
RESULTS_TABLE = "dq_results"
SAMPLE_ROWS = 3

# Resolves a logical table name to a DataFrame. Gold checks resolve to staging
# tables, so the audit runs on what is about to be published, not on what is live.
Resolver = Callable[[str], DataFrame]


class DataQualityError(Exception):
    """Raised when at least one ERROR severity check fails."""


@dataclass(frozen=True)
class Check:
    name: str
    layer: str
    table: str
    kind: str
    severity: str
    description: str
    evaluate: Callable[[Resolver], tuple[int, int, str]] = field(repr=False)


@dataclass
class CheckResult:
    check: str
    layer: str
    table_name: str
    kind: str
    severity: str
    rows_checked: int
    rows_failed: int
    passed: bool
    detail: str
    seconds: float


def _sample(df: DataFrame, columns: Sequence[str]) -> str:
    cols = [c for c in columns if c in df.columns] or df.columns[:2]
    rows = df.select(*cols).limit(SAMPLE_ROWS).collect()
    return "; ".join(", ".join(f"{c}={r[c]}" for c in cols) for r in rows)


# Check constructors ---------------------------------------------------------------

def not_null(layer: str, table: str, columns: Sequence[str], severity: str = ERROR) -> Check:
    def run(resolve: Resolver) -> tuple[int, int, str]:
        df = resolve(table)
        missing = F.lit(False)
        for c in columns:
            missing = missing | F.col(c).isNull()
        bad = df.filter(missing)
        per_column = df.agg(*[F.count(F.when(F.col(c).isNull(), 1)).alias(c) for c in columns]).collect()[0]
        detail = ", ".join(f"{c}={per_column[c]}" for c in columns if per_column[c])
        return df.count(), bad.count(), detail
    return Check(f"{table}.not_null", layer, table, "not_null", severity,
                 f"{', '.join(columns)} must be present", run)


def unique(layer: str, table: str, keys: Sequence[str], severity: str = ERROR) -> Check:
    def run(resolve: Resolver) -> tuple[int, int, str]:
        df = resolve(table)
        dupes = df.groupBy(*keys).count().filter("count > 1")
        extra = dupes.agg(F.coalesce(F.sum(F.col("count") - 1), F.lit(0))).collect()[0][0]
        return df.count(), int(extra), _sample(dupes, keys)
    return Check(f"{table}.unique", layer, table, "unique", severity,
                 f"one row per {', '.join(keys)}", run)


def accepted_values(layer: str, table: str, column: str, values: Sequence[str], severity: str = ERROR) -> Check:
    def run(resolve: Resolver) -> tuple[int, int, str]:
        df = resolve(table)
        bad = df.filter(~F.col(column).isin(list(values)) | F.col(column).isNull())
        return df.count(), bad.count(), _sample(bad.select(column).distinct(), [column])
    return Check(f"{table}.{column}.accepted_values", layer, table, "accepted_values", severity,
                 f"{column} in ({', '.join(map(str, values))})", run)


def value_range(layer: str, table: str, column: str, low: float | None = None, high: float | None = None,
                allow_null: bool = False, severity: str = ERROR) -> Check:
    def run(resolve: Resolver) -> tuple[int, int, str]:
        df = resolve(table)
        bad = F.lit(False)
        if low is not None:
            bad = bad | (F.col(column) < F.lit(low))
        if high is not None:
            bad = bad | (F.col(column) > F.lit(high))
        if not allow_null:
            bad = bad | F.col(column).isNull()
        failing = df.filter(bad)
        return df.count(), failing.count(), _sample(failing.select(column), [column])
    bounds = f"{low if low is not None else '-inf'} to {high if high is not None else '+inf'}"
    return Check(f"{table}.{column}.range", layer, table, "range", severity,
                 f"{column} between {bounds}", run)


def referential(layer: str, table: str, column: str, parent: str, parent_column: str | None = None,
                severity: str = ERROR) -> Check:
    parent_col = parent_column or column

    def run(resolve: Resolver) -> tuple[int, int, str]:
        df = resolve(table).filter(F.col(column).isNotNull())
        keys = resolve(parent).select(F.col(parent_col).alias("_k")).distinct()
        orphans = df.join(keys, F.col(column) == F.col("_k"), "left_anti")
        return df.count(), orphans.count(), _sample(orphans.select(column).distinct(), [column])
    return Check(f"{table}.{column}.ref_{parent}", layer, table, "referential_integrity", severity,
                 f"every {column} exists in {parent}.{parent_col}", run)


def expression(layer: str, table: str, name: str, expr: str, description: str,
               severity: str = ERROR, sample_columns: Sequence[str] = ()) -> Check:
    def run(resolve: Resolver) -> tuple[int, int, str]:
        df = resolve(table)
        failing = df.filter(f"NOT coalesce({expr}, false)")
        return df.count(), failing.count(), _sample(failing, list(sample_columns))
    return Check(f"{table}.{name}", layer, table, "expression", severity, description, run)


def row_count_match(layer: str, table: str, other: str, severity: str = ERROR,
                    table_filter: str | None = None, other_filter: str | None = None) -> Check:
    def run(resolve: Resolver) -> tuple[int, int, str]:
        a = resolve(table)
        b = resolve(other)
        if table_filter:
            a = a.filter(table_filter)
        if other_filter:
            b = b.filter(other_filter)
        n_a, n_b = a.count(), b.count()
        return n_a, abs(n_a - n_b), f"{table}={n_a}, {other}={n_b}"
    return Check(f"{table}.rows_match_{other}", layer, table, "reconciliation", severity,
                 f"row count equals {other}", run)


def sum_match(layer: str, table: str, column: str, other: str, other_column: str | None = None,
              tolerance: float = 0.01, severity: str = ERROR, table_filter: str | None = None) -> Check:
    other_col = other_column or column

    def run(resolve: Resolver) -> tuple[int, int, str]:
        a = resolve(table)
        if table_filter:
            a = a.filter(table_filter)
        total_a = float(a.agg(F.coalesce(F.sum(column), F.lit(0))).collect()[0][0])
        total_b = float(resolve(other).agg(F.coalesce(F.sum(other_col), F.lit(0))).collect()[0][0])
        gap = abs(total_a - total_b)
        return a.count(), 0 if gap <= tolerance else 1, f"{table}={total_a:.2f}, {other}={total_b:.2f}, gap={gap:.2f}"
    return Check(f"{table}.{column}.sum_matches_{other}", layer, table, "reconciliation", severity,
                 f"sum of {column} equals {other}.{other_col} within {tolerance}", run)


def metric_at_most(layer: str, table: str, name: str, metric: Callable[[Resolver], tuple[float, str]],
                   limit: float, severity: str = ERROR, description: str = "") -> Check:
    def run(resolve: Resolver) -> tuple[int, int, str]:
        value, detail = metric(resolve)
        return 1, 0 if value <= limit else 1, f"{detail} (limit {limit})"
    return Check(f"{table}.{name}", layer, table, "metric", severity,
                 description or f"{name} at most {limit}", run)


# Running and gating -----------------------------------------------------------------

def evaluate(checks: Sequence[Check], resolve: Resolver) -> list[CheckResult]:
    results = []
    for check in checks:
        started = time.perf_counter()
        checked, failed, detail = check.evaluate(resolve)
        results.append(CheckResult(check.name, check.layer, check.table, check.kind, check.severity,
                                   int(checked), int(failed), failed == 0, detail,
                                   round(time.perf_counter() - started, 2)))
    return results


def failures(results: Sequence[CheckResult], severity: str = ERROR) -> list[CheckResult]:
    return [r for r in results if r.severity == severity and not r.passed]


def write_results(lake: Lake, results: Sequence[CheckResult], batch_id: str) -> None:
    rows = [(batch_id, r.layer, r.table_name, r.check, r.kind, r.severity,
             r.rows_checked, r.rows_failed, r.passed, r.detail, r.seconds) for r in results]
    df = lake.spark.createDataFrame(
        rows,
        "batch_id string, layer string, table_name string, check_name string, kind string, severity string, "
        "rows_checked long, rows_failed long, passed boolean, detail string, seconds double",
    ).withColumn("checked_at", F.current_timestamp())
    lake.write(df, RESULTS_TABLE, mode="append")


def caching(resolve: Resolver) -> tuple[Resolver, Callable[[], None]]:
    """Most checks read the same few tables. Resolve each one once and keep it cached."""
    cache: dict[str, DataFrame] = {}

    def resolver(table: str) -> DataFrame:
        if table not in cache:
            cache[table] = resolve(table).cache()
        return cache[table]

    def release() -> None:
        for df in cache.values():
            df.unpersist()
        cache.clear()

    return resolver, release


def run(lake: Lake, checks: Sequence[Check], batch_id: str | None = None, resolve: Resolver | None = None,
        gate: bool = True) -> list[CheckResult]:
    """Evaluate the checks, record them, and raise if an ERROR check failed."""
    batch_id = batch_id or f"local-{uuid.uuid4()}"
    resolver, release = caching(resolve or lake.read)
    try:
        results = evaluate(checks, resolver)
    finally:
        release()
    write_results(lake, results, batch_id)
    bad = failures(results)
    if bad and gate:
        lines = [f"{r.check} ({r.table_name}): {r.rows_failed} rows failed. {r.detail}" for r in bad]
        raise DataQualityError(f"{len(bad)} ERROR check(s) failed:\n" + "\n".join(lines))
    return results
