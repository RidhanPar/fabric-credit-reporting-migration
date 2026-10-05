"""Write the Fabric items: notebooks and the data pipeline.

Two outputs, from one definition of each notebook:

* ``fabric/workspace/<name>.Notebook/`` and ``<name>.DataPipeline/`` in Fabric's own
  Git format, so a workspace can be synced from this repo (Phase 6). Each item is a
  folder with a ``.platform`` file, which is what Fabric looks for.
* ``fabric/notebooks/<name>.ipynb`` for importing a notebook by hand, which is how
  Phases 2 and 3 were set up before Git integration existed.

The notebooks stay thin. All logic is in the tested ``portfolio_migration`` package,
installed in Fabric through an Environment.

    py -3.11 scripts/build_fabric_items.py
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).parents[1]
WORKSPACE = ROOT / "fabric" / "workspace"
IPYNB = ROOT / "fabric" / "notebooks"
NAMESPACE = uuid.UUID("1b6e7a8c-0d4f-4c3a-9f51-6c0e6a1d2f30")
PIPELINE = "pl_portfolio_medallion"

HEADER = """
import json
import time
import uuid
from dataclasses import asdict

import portfolio_migration
from portfolio_migration.lakehouse import ops
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
# Every step logs itself to ops_pipeline_run, success or failure, so a 2am failure
# can be read back without opening a notebook.
with ops.logged_step(lake, batch_id, LAYER) as outcome:
    output = [asdict(r) for r in bronze.run(lake, batch_id)]
    outcome["rows"] = sum(r["new_rows"] for r in output)
display(spark.createDataFrame(output))
"""

SILVER = """
from portfolio_migration.lakehouse import dq_checks, quality, silver

LAYER = "silver"
with ops.logged_step(lake, batch_id, LAYER) as outcome:
    output = [asdict(r) for r in silver.run(lake, batch_id)]
    outcome["rows"] = sum(r["silver_rows"] for r in output)
display(spark.createDataFrame(output))

# Gate: raises DataQualityError if any ERROR check fails, which fails the pipeline
# and stops gold from running. Results are written to dq_results either way.
with ops.logged_step(lake, batch_id, "silver_quality") as outcome:
    checks = quality.run(lake, dq_checks.SILVER_CHECKS, batch_id, gate=True)
    outcome["detail"] = f"{len(checks)} checks, {len(quality.failures(checks, quality.WARN))} warnings"
display(spark.createDataFrame([asdict(c) for c in checks]))
"""

GOLD = """
from portfolio_migration.lakehouse import dq_checks, gold, kpis, quality

LAYER = "gold"
# Writes the star schema to stg_* tables, runs the gold checks against those, and
# publishes the gold tables only if no ERROR check fails.
with ops.logged_step(lake, batch_id, LAYER) as outcome:
    result = gold.run(lake, batch_id)
    output = [asdict(r) for r in result.tables]
    outcome["rows"] = sum(r["rows"] for r in output)
display(spark.createDataFrame(output))
display(spark.createDataFrame([asdict(c) for c in result.checks]))
print(f"{len(result.checks)} gold checks passed before publishing")

# The monthly KPI table the reconciliation and the semantic model read.
with ops.logged_step(lake, batch_id, "kpis") as outcome:
    outcome["rows"] = kpis.run(lake, batch_id).rows
with ops.logged_step(lake, batch_id, "kpi_quality") as outcome:
    kpi_checks = quality.run(lake, dq_checks.KPI_CHECKS, batch_id, gate=True)
    outcome["detail"] = f"{len(kpi_checks)} checks"
print(f"{kpis.KPI_TABLE}: {len(kpi_checks)} checks passed")
"""

RECONCILIATION = """
from pathlib import Path

import notebookutils
from portfolio_migration.lakehouse import kpis
from portfolio_migration.reconcile import bridge as br
from portfolio_migration.reconcile import report

LAYER = "reconciliation"
# pycel reads a local file, so copy the legacy pack out of OneLake first.
notebookutils.fs.cp("Files/legacy/Monthly_Portfolio_Pack.xlsx",
                    "file:///tmp/Monthly_Portfolio_Pack.xlsx")

with ops.logged_step(lake, batch_id, LAYER) as outcome:
    stages = br.evaluate_legacy_stages(Path("/tmp/Monthly_Portfolio_Pack.xlsx"), Path("/tmp/recon"))
    gold_kpis = br.restrict_to_window(lake.read(kpis.KPI_TABLE).toPandas())
    bridge = br.build_bridge(stages, gold_kpis)
    attributions = br.attribution(bridge)
    summary = br.findings_summary(bridge, attributions)
    numbers = report.summary_numbers(bridge, attributions)
    for name, frame in (("recon_bridge", bridge), ("recon_attribution", attributions),
                        ("recon_findings_summary", summary)):
        lake.write(spark.createDataFrame(frame), name)
    outcome["rows"] = len(bridge)
    outcome["detail"] = f"{numbers['legacy_faults_found']} legacy faults, {numbers['unexplained_cells']} unexplained"

output = numbers
display(spark.createDataFrame([numbers]))
assert numbers["unexplained_cells"] == 0, "the reconciliation does not close"
print(f"{numbers['legacy_faults_found']} legacy faults, "
      f"{numbers['cells_affected_by_a_legacy_error']} published figures affected, "
      f"{numbers['unexplained_cells']} unexplained")
"""

MONITORING = """
LAYER = "monitoring"
# Runs whether the earlier steps succeeded or not, which is the point: a failed run
# still has to raise the alarm. Writes ops_freshness and ops_alerts, then raises if a
# critical alert fired, so this activity goes red and the pipeline's failure path fires.
output = ops.monitor(lake, batch_id, gate=False)
display(lake.read(ops.FRESHNESS_TABLE))
display(lake.read(ops.ALERTS_TABLE).filter(f"batch_id = '{batch_id}'"))

critical = [a for a in output["fired"] if a["severity"] == ops.CRITICAL]
if critical:
    raise ops.CriticalAlert(json.dumps(critical, indent=2))
print(f"{output['alerts_evaluated']} alert conditions evaluated, none critical")
"""

NOTEBOOKS = {
    "nb_01_bronze": ("Bronze: land every file as delivered", BRONZE,
                     "Reads new files under `Files/landing`, keeps every column as a string, adds lineage "
                     "and appends to the `bronze_*` tables. Files already loaded are skipped, so a rerun is safe."),
    "nb_02_silver": ("Silver: clean, type, deduplicate, quarantine, then gate", SILVER,
                     "Rebuilds the `silver_*` tables from bronze. Bad rows go to `silver_quarantine` with reasons, "
                     "trailers to `silver_control_totals`, one audit row per entity to `silver_load_audit`. "
                     "Then runs the silver data quality checks and fails the run if an ERROR check fails."),
    "nb_03_gold": ("Gold: star schema, published only if the checks pass", GOLD,
                   "Builds the dimensions and facts into `stg_*` tables, audits them, and publishes the "
                   "gold tables the Direct Lake model reads only when every ERROR check passes. Then builds "
                   "the monthly KPI table and checks it."),
    "nb_04_reconciliation": ("Reconciliation: legacy pack against gold", RECONCILIATION,
                             "Recalculates the legacy workbook as found and after each fix, compares every "
                             "published figure against gold, and writes the bridge and the attribution of "
                             "every difference to `recon_*` tables. Needs `pycel` in the Environment and "
                             "`Files/legacy/Monthly_Portfolio_Pack.xlsx` uploaded."),
    "nb_05_monitoring": ("Monitoring: freshness, alerts, and going red when it matters", MONITORING,
                         "Writes `ops_freshness` and `ops_alerts`, then raises if a critical alert fired. "
                         "Runs on success, failure or skip of the earlier steps, because a failed run still "
                         "has to raise the alarm."),
}

# The pipeline. Monitoring runs whatever happened upstream; everything else runs on success.
ACTIVITIES = [
    ("Bronze", "nb_01_bronze", None, ["Succeeded"]),
    ("Silver", "nb_02_silver", "Bronze", ["Succeeded"]),
    ("Gold", "nb_03_gold", "Silver", ["Succeeded"]),
    ("Monitoring", "nb_05_monitoring", "Gold", ["Succeeded", "Failed", "Skipped"]),
]


def tag(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, "/".join(parts)))


def platform_file(name: str, item_type: str) -> str:
    return json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": item_type, "displayName": name},
        "config": {"version": "2.0", "logicalId": tag("item", name)},
    }, indent=2) + "\n"


def _cells(title: str, body: str, about: str) -> list[tuple[str, str]]:
    """(kind, source) pairs shared by both output formats."""
    markdown = (f"# {title}\n\n{about}\n\n"
                "Requires the `env_portfolio` Environment (with the `portfolio_migration` wheel) and the "
                "`lh_portfolio` lakehouse attached as default. Logic and tests live in the GitHub repo; "
                "do not edit logic here.")
    parameters = '# Set by the data pipeline to @pipeline().RunId. Empty means a manual run.\nbatch_id = ""'
    return [("markdown", markdown), ("parameters", parameters), ("code", HEADER.strip("\n")),
            ("code", body.strip("\n")), ("code", FOOTER.strip("\n"))]


def _ipynb_source(text: str) -> list[str]:
    lines = text.split("\n")
    return [ln + "\n" for ln in lines[:-1]] + [lines[-1]]


def notebook_ipynb(title: str, body: str, about: str) -> dict:
    cells = []
    for kind, source in _cells(title, body, about):
        if kind == "markdown":
            cells.append({"cell_type": "markdown", "metadata": {}, "source": _ipynb_source(source)})
        else:
            metadata = {"tags": ["parameters"]} if kind == "parameters" else {}
            cells.append({"cell_type": "code", "metadata": metadata, "execution_count": None,
                          "outputs": [], "source": _ipynb_source(source)})
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"name": "synapse_pyspark", "display_name": "Synapse PySpark", "language": "Python"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def notebook_content_py(title: str, body: str, about: str) -> str:
    """Fabric's own notebook format: a .py file with METADATA and CELL markers."""
    out = ["# Fabric notebook source", "", "# METADATA ********************", ""]
    header = {"kernel_info": {"name": "synapse_pyspark"}, "dependencies": {}}
    out += ["# META " + line for line in json.dumps(header, indent=2).split("\n")]
    markers = {"markdown": "# MARKDOWN ********************",
               "parameters": "# PARAMETERS CELL ********************",
               "code": "# CELL ********************"}
    for kind, source in _cells(title, body, about):
        out += ["", markers[kind], ""]
        if kind == "markdown":
            out += [f"# {line}".rstrip() for line in source.split("\n")]
        else:
            out += source.split("\n")
        out += ["", "# METADATA ********************", ""]
        meta = {"language": "python", "language_group": "synapse_pyspark"}
        out += ["# META " + line for line in json.dumps(meta, indent=2).split("\n")]
    return "\n".join(out) + "\n"


def pipeline_content() -> str:
    activities = []
    for name, notebook, depends_on, conditions in ACTIVITIES:
        activities.append({
            "name": name,
            "type": "TridentNotebook",
            "dependsOn": ([] if depends_on is None
                          else [{"activity": depends_on, "dependencyConditions": conditions}]),
            "policy": {"timeout": "0.01:00:00", "retry": 1, "retryIntervalInSeconds": 60,
                       "secureOutput": False, "secureInput": False},
            "typeProperties": {
                "notebookId": f"<{notebook} item id, set by the portal on first sync>",
                "workspaceId": "<workspace id, set by the portal on first sync>",
                "parameters": {"batch_id": {"value": {"value": "@pipeline().RunId", "type": "Expression"},
                                            "type": "string"}},
            },
        })
    return json.dumps({
        "properties": {
            "description": ("Bronze, then silver, then gold, each on success. Monitoring runs whatever "
                            "happened upstream, so a failed run still raises the alarm. Retries are safe "
                            "because every layer is idempotent."),
            "activities": activities,
        }
    }, indent=2) + "\n"


def main() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    IPYNB.mkdir(parents=True, exist_ok=True)
    for path in list(IPYNB.glob("*.ipynb")):
        path.unlink()

    for name, (title, body, about) in NOTEBOOKS.items():
        (IPYNB / f"{name}.ipynb").write_text(
            json.dumps(notebook_ipynb(title, body, about), indent=1) + "\n", encoding="utf-8")
        item = WORKSPACE / f"{name}.Notebook"
        item.mkdir(parents=True, exist_ok=True)
        (item / ".platform").write_text(platform_file(name, "Notebook"), encoding="utf-8")
        (item / "notebook-content.py").write_text(notebook_content_py(title, body, about), encoding="utf-8")

    pipeline = WORKSPACE / f"{PIPELINE}.DataPipeline"
    pipeline.mkdir(parents=True, exist_ok=True)
    (pipeline / ".platform").write_text(platform_file(PIPELINE, "DataPipeline"), encoding="utf-8")
    (pipeline / "pipeline-content.json").write_text(pipeline_content(), encoding="utf-8")

    print(f"{len(NOTEBOOKS)} notebooks and 1 pipeline written to "
          f"{WORKSPACE.relative_to(ROOT)} and {IPYNB.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
