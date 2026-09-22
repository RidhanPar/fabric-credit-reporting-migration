"""Bronze: land every file exactly as delivered.

Rules
* Every column stays a string. Nothing is parsed, trimmed or filtered, so a wrong
  parse rule in silver can always be fixed and replayed from bronze.
* Every row carries lineage: country feed, source file, file modified time, load time, batch id.
* Loads are incremental by file. A file already in the bronze table is skipped,
  so rerunning bronze, or the whole pipeline, never duplicates data.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.utils import AnalysisException

from portfolio_migration import config as cfg
from portfolio_migration.lakehouse.io import Lake

# Per country core banking feeds: entity -> folder under landing/core_banking/<CC>/
CORE_BANKING_ENTITIES = ("customers", "accounts", "balances", "repayments")
# Group wide comma separated feeds: entity -> folder under landing/
REFERENCE_FEEDS = {"products": "reference/products", "fx_rates": "treasury/fx_rates"}
APPLICATIONS_FOLDER = "los/applications"
INGESTION_LOG = "ops_ingestion_log"


@dataclass
class LoadResult:
    table: str
    new_files: int
    new_rows: int


def csv_separator(country: cfg.Country) -> str:
    return ";" if country.csv_decimal == "," else ","


def _read_csv(lake: Lake, path: str, sep: str) -> DataFrame | None:
    try:
        return (lake.spark.read
                .option("header", True).option("sep", sep).option("inferSchema", False)
                .option("encoding", "UTF-8").option("mode", "PERMISSIVE")
                .csv(path))
    except AnalysisException as exc:  # no files in the folder yet
        if "PATH_NOT_FOUND" in str(exc) or "Path does not exist" in str(exc):
            return None
        raise


def _with_lineage(df: DataFrame, feed: str, batch_id: str) -> DataFrame:
    return (df.withColumn("_country_feed", F.lit(feed))
              .withColumn("_source_file", F.col("_metadata.file_path"))
              .withColumn("_source_modified_at", F.col("_metadata.file_modification_time"))
              .withColumn("_ingested_at", F.current_timestamp())
              .withColumn("_batch_id", F.lit(batch_id)))


def _append_new_files(lake: Lake, df: DataFrame | None, table: str, batch_id: str) -> LoadResult:
    if df is None:
        return LoadResult(table, 0, 0)
    if lake.exists(table):
        loaded = lake.read(table).select("_source_file").distinct()
        df = df.join(loaded, "_source_file", "left_anti")
    per_file = df.groupBy("_source_file").count().collect()
    if not per_file:
        return LoadResult(table, 0, 0)
    lake.write(df, table, mode="append")
    log = lake.spark.createDataFrame(
        [(batch_id, table, r["_source_file"], int(r["count"])) for r in per_file],
        "batch_id string, table_name string, source_file string, row_count long",
    ).withColumn("ingested_at", F.current_timestamp())
    lake.write(log, INGESTION_LOG, mode="append")
    return LoadResult(table, len(per_file), sum(int(r["count"]) for r in per_file))


def load_core_banking(lake: Lake, entity: str, batch_id: str) -> LoadResult:
    frames = []
    for c in cfg.COUNTRIES:
        df = _read_csv(lake, lake.landing(f"core_banking/{c.code}/{entity}/*.csv"), csv_separator(c))
        if df is not None:
            frames.append(_with_lineage(df, c.code, batch_id))
    union = None
    for f in frames:
        union = f if union is None else union.unionByName(f, allowMissingColumns=True)
    return _append_new_files(lake, union, f"bronze_{entity}", batch_id)


def load_reference(lake: Lake, entity: str, batch_id: str) -> LoadResult:
    df = _read_csv(lake, lake.landing(f"{REFERENCE_FEEDS[entity]}/*.csv"), ",")
    df = None if df is None else _with_lineage(df, "GROUP", batch_id)
    return _append_new_files(lake, df, f"bronze_{entity}", batch_id)


def load_applications(lake: Lake, batch_id: str) -> LoadResult:
    """JSON lines are stored as raw text. A truncated line must survive to be quarantined in silver."""
    try:
        df = lake.spark.read.text(lake.landing(f"{APPLICATIONS_FOLDER}/*.jsonl"))
    except AnalysisException as exc:
        if "PATH_NOT_FOUND" in str(exc) or "Path does not exist" in str(exc):
            return LoadResult("bronze_applications", 0, 0)
        raise
    df = _with_lineage(df.withColumnRenamed("value", "raw_json"), "GROUP", batch_id)
    return _append_new_files(lake, df, "bronze_applications", batch_id)


def run(lake: Lake, batch_id: str | None = None) -> list[LoadResult]:
    batch_id = batch_id or f"local-{uuid.uuid4()}"
    results = [load_reference(lake, e, batch_id) for e in REFERENCE_FEEDS]
    results += [load_core_banking(lake, e, batch_id) for e in CORE_BANKING_ENTITIES]
    results.append(load_applications(lake, batch_id))
    return results
