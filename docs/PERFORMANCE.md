# Measured performance

Every number here came from a run whose output is committed in `docs/results/`.
Nothing in this file is an estimate. Where a number can only be measured on a
Fabric capacity, the row says "not measured yet" rather than carrying a guess.

## What is being measured, and where

| Number | Where it comes from | Status |
|---|---|---|
| Pipeline run time, per step | `ops_pipeline_run`, and the local run summary | Measured locally |
| Gold query response time | `scripts/benchmark_gold_queries.py` over the gold tables | Measured locally |
| Data refresh time | Direct Lake has no refresh. The equivalent is landing to gold published, which is the pipeline run time | Measured locally |
| Report query response time | Power BI Performance Analyzer, per visual | Not measured yet, needs a capacity |
| Fabric pipeline run time | The pipeline's own run output in the Fabric monitor | Not measured yet, needs a capacity |

## Pipeline run time

Local run in Docker, Spark 3.5.5 and Delta 3.2.1 on 2 cores, seed 42, 225,357
balance rows. From
[docs/results/phase6_local_run.json](results/phase6_local_run.json).

| Step | Seconds | What it does |
|---|---|---|
| bronze | 68.2 | Land 195 files as strings, incrementally by file |
| silver | 76.7 | Parse, validate, deduplicate, quarantine, audit |
| silver_quality | 37.0 | 43 checks |
| gold | 92.4 | Build the star schema into staging, 43 checks, publish 519,185 rows |
| kpis | 24.8 | Build the 2,016 row monthly KPI table |
| kpi_quality | 5.3 | 10 checks |
| monitor | 16.7 | Freshness and 6 alert conditions |
| **Total** | **321.1** | |

Adding up the per check timings inside those steps, the 96 data quality checks
account for 78.7 seconds, about 24 percent of the run: 35.3 on silver, 39.7 on
gold (inside the gold step, before publishing) and 3.6 on the KPI table. That is
the price of the publish gate, and it is worth saying out loud when someone asks
why the pipeline takes five minutes rather than four.

A Fabric run will not match these numbers. A starter pool session has its own
start up time, and the capacity has more cores than this container. The number
to compare is the shape: which step dominates, and whether that changes.

## Gold query response time

The aggregations behind the report pages, run as Spark SQL against the same gold
tables the semantic model reads, five times each. From
[docs/results/phase6_query_benchmark.json](results/phase6_query_benchmark.json).

| Query | First run (ms) | Median (ms) | Best (ms) | Rows |
|---|---|---|---|---|
| Executive summary, balance by month and country | 10,223.2 | 1,264.0 | 1,104.9 | 72 |
| Executive summary, country table for the latest month | 2,490.1 | 1,673.6 | 1,465.5 | 3 |
| Delinquency, 30+ and 90+ rates by month and country | 992.5 | 992.5 | 982.1 | 72 |
| Delinquency, balance by DPD bucket and month | 1,182.2 | 1,027.4 | 969.4 | 120 |
| Delinquency, country and product matrix | 5,138.3 | 2,327.5 | 2,159.2 | 15 |
| Account detail drillthrough, one account | 2,833.0 | 1,409.9 | 1,198.9 | 1 |
| Migration evidence, whole KPI table | 2,925.9 | 1,334.4 | 1,262.3 | 60 |

Read these as a baseline for the shape of the work, not as a report experience.
They are Spark jobs on two cores, where a second of the time is scheduling, and
the first run of each query carries the file scan. Direct Lake answers the same
aggregations from column segments already in memory in the Power BI engine, which
is a different order of magnitude. That comparison goes here once it is measured.

The first run against the best run is the more useful signal locally: the
executive summary query drops from 10.2 seconds to 1.1 once the Parquet files are
in the page cache.

## Report query response time

Not measured yet. Method, for when a capacity exists:

1. Open the report, go to **Optimize**, **Performance analyzer**, **Start recording**.
2. Refresh visuals, then interact with each slicer once.
3. Export the recording, and record per visual: DAX query, visual display, other.
4. Repeat on a warm model, because the first query on a cold Direct Lake model
   pays for loading column segments into memory.

Rule of thumb to hold the model to: under a second per visual on a warm model,
and no page over three seconds to fully render. If a visual misses that, the
first thing to check is whether Direct Lake fell back to DirectQuery, which shows
in the capacity metrics app.

## How to reproduce the local numbers

```bash
docker run --rm -v "%cd%:/repo" -e PYTHONPATH=/repo/src portfolio-spark:3.5 python -m portfolio_migration lakehouse --landing /repo/data/landing --lake /repo/build/lake --summary /repo/docs/results/phase6_local_run.json
```

```bash
docker run --rm -v "%cd%:/repo" -e PYTHONPATH=/repo/src portfolio-spark:3.5 python scripts/benchmark_gold_queries.py --lake /repo/build/lake
```
