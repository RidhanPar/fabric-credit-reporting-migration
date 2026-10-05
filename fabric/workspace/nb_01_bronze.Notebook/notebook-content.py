# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {}
# META }

# MARKDOWN ********************

# # Bronze: land every file as delivered
#
# Reads new files under `Files/landing`, keeps every column as a string, adds lineage and appends to the `bronze_*` tables. Files already loaded are skipped, so a rerun is safe.
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

from portfolio_migration.lakehouse import bronze

LAYER = "bronze"
# Every step logs itself to ops_pipeline_run, success or failure, so a 2am failure
# can be read back without opening a notebook.
with ops.logged_step(lake, batch_id, LAYER) as outcome:
    output = [asdict(r) for r in bronze.run(lake, batch_id)]
    outcome["rows"] = sum(r["new_rows"] for r in output)
display(spark.createDataFrame(output))

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
