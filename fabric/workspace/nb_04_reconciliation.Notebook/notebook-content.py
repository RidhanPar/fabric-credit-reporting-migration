# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {}
# META }

# MARKDOWN ********************

# # Reconciliation: legacy pack against gold
#
# Recalculates the legacy workbook as found and after each fix, compares every published figure against gold, and writes the bridge and the attribution of every difference to `recon_*` tables. Needs `pycel` in the Environment and `Files/legacy/Monthly_Portfolio_Pack.xlsx` uploaded.
#
# Requires the `env_portfolio` Environment (with the `portfolio_migration` wheel) and the `lh_portfolio` lakehouse attached as default. Logic and tests live in the GitHub repo; do not edit logic here.

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# PARAMETERS CELL ********************

# Set by the data pipeline to @pipeline().RunId. Empty means a manual run.
batch_id = ""

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

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

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

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

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

seconds = round(time.perf_counter() - started, 1)
print(f"finished in {seconds} s")
notebookutils.notebook.exit(json.dumps({"layer": LAYER, "batch_id": batch_id, "seconds": seconds,
                                        "output": output}))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
