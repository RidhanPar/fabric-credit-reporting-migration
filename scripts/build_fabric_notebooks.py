"""Write the Fabric notebooks (.ipynb) for the medallion layers.

The notebooks are deliberately thin: all logic lives in the tested
``portfolio_migration`` package, installed in Fabric through an Environment.
A notebook only wires the attached lakehouse to the package and reports results.

    py -3.11 scripts/build_fabric_notebooks.py
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parents[1] / "fabric" / "notebooks"

HEADER = """
import json
import time
import uuid
from dataclasses import asdict

import portfolio_migration
from portfolio_migration.lakehouse.io import FabricLake

batch_id = batch_id or f"manual-{uuid.uuid4()}"
print(f"portfolio_migration {portfolio_migration.__version__}, batch {batch_id}")
lake = FabricLake(spark)
started = time.perf_counter()
"""

FOOTER = """
seconds = round(time.perf_counter() - started, 1)
print(f"finished in {seconds} s")
notebookutils.notebook.exit(json.dumps({"layer": LAYER, "batch_id": batch_id, "seconds": seconds,
                                        "output": output}))
"""

BRONZE = """
from portfolio_migration.lakehouse import bronze

LAYER = "bronze"
output = [asdict(r) for r in bronze.run(lake, batch_id)]
display(spark.createDataFrame(output))
"""

SILVER = """
from portfolio_migration.lakehouse import dq_checks, quality, silver

LAYER = "silver"
output = [asdict(r) for r in silver.run(lake, batch_id)]
display(spark.createDataFrame(output))

# Gate: raises DataQualityError if any ERROR check fails, which fails the pipeline
# and stops gold from running. Results are written to dq_results either way.
checks = quality.run(lake, dq_checks.SILVER_CHECKS, batch_id, gate=True)
display(spark.createDataFrame([asdict(c) for c in checks]))
print(f"{len(checks)} silver checks, "
      f"{len(quality.failures(checks, quality.WARN))} warnings")
"""

GOLD = """
from portfolio_migration.lakehouse import gold

LAYER = "gold"
# Writes the star schema to stg_* tables, runs the gold checks against those, and
# publishes the gold tables only if no ERROR check fails.
result = gold.run(lake, batch_id)
output = [asdict(r) for r in result.tables]
display(spark.createDataFrame(output))
display(spark.createDataFrame([asdict(c) for c in result.checks]))
print(f"{len(result.checks)} gold checks passed before publishing")
"""

LAYERS = {
    "nb_01_bronze": ("Bronze: land every file as delivered", BRONZE,
                     "Reads new files under `Files/landing`, keeps every column as a string, adds lineage "
                     "and appends to the `bronze_*` tables. Files already loaded are skipped, so a rerun is safe."),
    "nb_02_silver": ("Silver: clean, type, deduplicate, quarantine, then gate", SILVER,
                     "Rebuilds the `silver_*` tables from bronze. Bad rows go to `silver_quarantine` with reasons, "
                     "trailers to `silver_control_totals`, one audit row per entity to `silver_load_audit`. "
                     "Then runs the silver data quality checks and fails the run if an ERROR check fails."),
    "nb_03_gold": ("Gold: star schema, published only if the checks pass", GOLD,
                   "Builds the dimensions and facts into `stg_*` tables, audits them, and publishes the "
                   "gold tables the Direct Lake model reads only when every ERROR check passes."),
}


def _src(text: str) -> list[str]:
    lines = text.strip("\n").split("\n")
    return [ln + "\n" for ln in lines[:-1]] + [lines[-1]]


def notebook(title: str, body: str, about: str) -> dict:
    cells = [
        {"cell_type": "markdown", "metadata": {}, "source": _src(
            f"# {title}\n\n{about}\n\n"
            "Requires the `env_portfolio` Environment (with the `portfolio_migration` wheel) and the "
            "`lh_portfolio` lakehouse attached as default. Logic and tests live in the GitHub repo; "
            "do not edit logic here.")},
        {"cell_type": "code", "metadata": {"tags": ["parameters"]}, "execution_count": None, "outputs": [],
         "source": _src('# Set by the data pipeline to @pipeline().RunId. Empty means a manual run.\nbatch_id = ""')},
        {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": _src(HEADER)},
        {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": _src(body)},
        {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": _src(FOOTER)},
    ]
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"name": "synapse_pyspark", "display_name": "Synapse PySpark", "language": "Python"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (title, body, about) in LAYERS.items():
        path = OUT / f"{name}.ipynb"
        path.write_text(json.dumps(notebook(title, body, about), indent=1) + "\n", encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
