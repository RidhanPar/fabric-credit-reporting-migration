# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {}
# META }

# MARKDOWN ********************

# # Silver: clean, type, deduplicate, quarantine, then gate
#
# Rebuilds the `silver_*` tables from bronze. Bad rows go to `silver_quarantine` with reasons, trailers to `silver_control_totals`, one audit row per entity to `silver_load_audit`. Then runs the silver data quality checks and fails the run if an ERROR check fails.
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
