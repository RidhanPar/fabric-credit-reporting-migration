# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {}
# META }

# MARKDOWN ********************

# # Gold: star schema, published only if the checks pass
#
# Builds the dimensions and facts into `stg_*` tables, audits them, and publishes the gold tables the Direct Lake model reads only when every ERROR check passes. Then builds the monthly KPI table and checks it.
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
