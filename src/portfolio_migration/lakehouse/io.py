"""Where tables and files live.

The medallion code never builds a path itself. It asks a ``Lake`` for a table or
a landing folder, so the same functions run in a Fabric notebook (tables in the
attached lakehouse) and in local tests (Delta folders on disk).
"""
from __future__ import annotations

import os
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession


class Lake:
    spark: SparkSession

    def landing(self, rel: str) -> str:
        raise NotImplementedError

    def exists(self, table: str) -> bool:
        raise NotImplementedError

    def read(self, table: str) -> DataFrame:
        raise NotImplementedError

    def write(self, df: DataFrame, table: str, mode: str = "overwrite") -> None:
        raise NotImplementedError


class FabricLake(Lake):
    """Tables in the notebook's default lakehouse, landing files under Files/landing."""

    def __init__(self, spark: SparkSession, landing_root: str = "Files/landing"):
        self.spark = spark
        self.landing_root = landing_root

    def landing(self, rel: str) -> str:
        return f"{self.landing_root}/{rel}"

    def exists(self, table: str) -> bool:
        return self.spark.catalog.tableExists(table)

    def read(self, table: str) -> DataFrame:
        return self.spark.read.table(table)

    def write(self, df: DataFrame, table: str, mode: str = "overwrite") -> None:
        (df.write.format("delta").mode(mode)
           .option("overwriteSchema", "true" if mode == "overwrite" else "false")
           .saveAsTable(table))


class LocalLake(Lake):
    """Delta folders under ``root/Tables``, the same layout a Fabric lakehouse uses in OneLake."""

    def __init__(self, spark: SparkSession, root: Path, landing_root: Path):
        self.spark = spark
        self.root = Path(root)
        self.landing_root = Path(landing_root)

    def _path(self, table: str) -> str:
        return str(self.root / "Tables" / table)

    def landing(self, rel: str) -> str:
        return str(self.landing_root / rel)

    def exists(self, table: str) -> bool:
        return os.path.isdir(os.path.join(self._path(table), "_delta_log"))

    def read(self, table: str) -> DataFrame:
        return self.spark.read.format("delta").load(self._path(table))

    def write(self, df: DataFrame, table: str, mode: str = "overwrite") -> None:
        (df.write.format("delta").mode(mode)
           .option("overwriteSchema", "true" if mode == "overwrite" else "false")
           .save(self._path(table)))


def local_spark(app: str = "portfolio-migration") -> SparkSession:
    """A small local Spark with Delta, matching Fabric Runtime 1.3 (Spark 3.5, Delta 3.2)."""
    from delta import configure_spark_with_delta_pip

    builder = (SparkSession.builder.appName(app).master("local[2]")
               .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
               .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
               .config("spark.sql.shuffle.partitions", "4")
               .config("spark.sql.session.timeZone", "UTC")
               .config("spark.ui.enabled", "false"))
    return configure_spark_with_delta_pip(builder).getOrCreate()
