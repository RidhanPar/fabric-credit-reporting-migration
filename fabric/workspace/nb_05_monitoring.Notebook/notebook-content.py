# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {}
# META }

# MARKDOWN ********************

# # Monitoring: freshness, alerts, and going red when it matters
#
# Writes `ops_freshness` and `ops_alerts`, then raises if a critical alert fired. Runs on success, failure or skip of the earlier steps, because a failed run still has to raise the alarm.
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
