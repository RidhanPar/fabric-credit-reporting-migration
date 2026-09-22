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

LAYERS = {
    "nb_01_bronze": ("bronze", "Bronze: land every file as delivered",
                     "Reads new files under `Files/landing`, keeps every column as a string, adds lineage "
                     "and appends to the `bronze_*` tables. Files already loaded are skipped, so a rerun is safe."),
    "nb_02_silver": ("silver", "Silver: clean, type, deduplicate, quarantine",
                     "Rebuilds the `silver_*` tables from bronze. Bad rows go to `silver_quarantine` with reasons, "
                     "trailers to `silver_control_totals`, and one audit row per entity to `silver_load_audit`."),
    "nb_03_gold": ("gold", "Gold: star schema",
                   "Rebuilds the dimensions and facts that the Direct Lake semantic model reads."),
}


def _src(text: str) -> list[str]:
    lines = text.strip("\n").split("\n")
    return [ln + "\n" for ln in lines[:-1]] + [lines[-1]]


def notebook(name: str, layer: str, title: str, about: str) -> dict:
    cells = [
        {"cell_type": "markdown", "metadata": {}, "source": _src(
            f"# {title}\n\n{about}\n\n"
            "Requires the `env_portfolio` Environment (with the `portfolio_migration` wheel) and the "
            "`lh_portfolio` lakehouse attached as default. Logic and tests live in the GitHub repo; "
            "do not edit logic here.")},
        {"cell_type": "code", "metadata": {"tags": ["parameters"]}, "execution_count": None, "outputs": [],
         "source": _src('# Set by the data pipeline to @pipeline().RunId. Empty means a manual run.\nbatch_id = ""')},
        {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": _src(f"""
import json
import time
import uuid
from dataclasses import asdict

import portfolio_migration
from portfolio_migration.lakehouse import {layer}
from portfolio_migration.lakehouse.io import FabricLake

batch_id = batch_id or f"manual-{{uuid.uuid4()}}"
print(f"portfolio_migration {{portfolio_migration.__version__}}, batch {{batch_id}}")

started = time.perf_counter()
results = [asdict(r) for r in {layer}.run(FabricLake(spark), batch_id)]
seconds = round(time.perf_counter() - started, 1)
print(f"{layer} finished in {{seconds}} s")
display(spark.createDataFrame(results))
""")},
        {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": _src(f"""
# Returned to the pipeline as the activity output.
notebookutils.notebook.exit(json.dumps({{"layer": "{layer}", "batch_id": batch_id, "seconds": seconds, "results": results}}))
""")},
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
    for name, (layer, title, about) in LAYERS.items():
        path = OUT / f"{name}.ipynb"
        path.write_text(json.dumps(notebook(name, layer, title, about), indent=1) + "\n", encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
