# Legacy Excel to Microsoft Fabric: consumer credit portfolio reporting migration

A consumer lender (personal loans and credit cards in Poland, the Czech Republic
and Romania) runs its monthly board portfolio pack from a formula driven Excel
workbook. This project migrates it to a Microsoft Fabric medallion lakehouse
and a Direct Lake Power BI model, and proves with a reconciliation which of the
old numbers were wrong.

All data is synthetic and generated from a fixed seed. The legacy workbook is a
real formula workbook, and its published figures come from recalculating its own
formulas.

> **Status:** Phases 1, 2 and 3 of 7 built. This README grows each phase. Every number
> in it traces to a committed run output.
>
> **Where it has run:** every result quoted here was measured locally, on Spark
> 3.5.5 and Delta 3.2.1, the versions Fabric Runtime 1.3 uses, in a container and
> in GitHub Actions. Nothing has run in a Fabric tenant yet, because a Fabric
> trial is not available for the author's account. The Fabric items (Environment,
> notebooks, data pipeline) are authored and version controlled here, and no
> Fabric timing or screenshot is quoted until one exists.

## Phases

| # | Phase | Status |
|---|---|---|
| 1 | Legacy world: source data, landed files, legacy workbook | Done |
| 2 | Medallion lakehouse: bronze, silver, gold notebooks and pipeline | Built, awaiting Fabric run |
| 3 | Data quality gates | Built, awaiting Fabric run |
| 4 | Reconciliation, legacy vs gold | Next |
| 5 | Semantic model (PBIP, TMDL, Direct Lake, RLS) | |
| 6 | Production operations (Git, deployment pipeline, monitoring) | |
| 7 | Documentation, runbook, teardown | |

## Phase 1 output (seed 42, from `data/generation_manifest.json`)

| Core system table | Rows |
|---|---|
| Customers | 30,545 |
| Applications | 34,714 |
| Accounts | 16,569 |
| Month end balance snapshots (24 months) | 225,357 |
| Repayments | 194,017 |

Landed files: 195 files in local formats (CZ `;` and `,` decimals, RO
`dd/mm/yyyy`), with trailer control totals and 6 kinds of transport defect.
See [docs/concepts/01-legacy-world.md](docs/concepts/01-legacy-world.md).

Legacy workbook: [data/legacy/Monthly_Portfolio_Pack.xlsx](data/legacy/Monthly_Portfolio_Pack.xlsx),
672 published KPI cells (24 months x 4 scopes x 7 KPIs) in
[data/legacy/legacy_published_kpis.csv](data/legacy/legacy_published_kpis.csv).

## Phase 2: medallion lakehouse

```
Files/landing (CSV, JSONL)
   |  nb_01_bronze   strings only, lineage, load by file (skip files already loaded)
   v
bronze_* ----------> ops_ingestion_log
   |  nb_02_silver   locale parsing, typing, dedup (latest delivery wins), rules
   v
silver_* ----------> silver_quarantine, silver_control_totals, silver_load_audit
   |  nb_03_gold     star schema, EUR at month end (stocks) and monthly average (flows)
   v
dim_date, dim_country, dim_product, dim_customer, dim_account, fx_rate_monthly,
fact_balance_snapshot, fact_origination, fact_application, fact_repayment
```

Orchestrated by the data pipeline `pl_portfolio_medallion` (bronze, then silver,
then gold, each on success, retry 1). All logic lives in the tested package
`portfolio_migration.lakehouse`, shipped to Fabric as a wheel in the
`env_portfolio` Environment. The notebooks only call it.

Local run of the same code (Docker, Spark 3.5.5, Delta 3.2.1, seed 42), output in
[docs/results/phase2_local_medallion_run.json](docs/results/phase2_local_medallion_run.json):

| Entity | Bronze rows | Trailers | Quarantined | Duplicates | Silver rows |
|---|---|---|---|---|---|
| balances | 228,308 | 73 | 9 | 2,869 | 225,357 |
| repayments | 194,393 | 0 | 0 | 376 | 194,017 |
| application events | 35,720 | 0 | 37 | 0 | 35,683 |

Silver equals the simulated truth row for row (tested). Fabric run times will be
added once measured in Fabric. Design and defence:
[docs/concepts/03-medallion-design.md](docs/concepts/03-medallion-design.md).

## Phase 3: data quality gates

86 declarative checks, 43 on silver and 43 on gold, 82 of them severity ERROR and
4 WARN. The full list is [dq_checks.py](src/portfolio_migration/lakehouse/dq_checks.py).

| Check type | Count | Example |
|---|---|---|
| Uniqueness | 18 | one row per account per month end |
| Referential integrity | 18 | every `account_id` in a fact exists in `dim_account` |
| Accepted values | 10 | `account_status` in ACTIVE, CLOSED, WRITTEN_OFF |
| Business rule (expression) | 10 | 90+ days past due is always also 30+ |
| Not null | 9 | `balance_eur` is never null |
| Range | 8 | `days_past_due` between 0 and 179 |
| Reconciliation between layers | 8 | gold row count and amount totals equal silver's |
| Threshold (metric) | 4 | quarantine rate at most 1 percent, severity WARN |
| Control total | 1 | parsed rows and amount per file match the file's trailer |

**Gold is never written directly.** It is built into `stg_*` tables, audited
there, and published only if no ERROR check fails. A failure leaves the previous
gold data in place for the report to keep using, and raises so the run goes red.
This is the write, audit, publish pattern.

Proof the gate works, from `tests/spark/test_quality_gates.py`:

| Injected fault | Result |
|---|---|
| One duplicate balance row in silver | Publish refused; `fact_balance_snapshot` keeps its row count; the duplicate is found in `stg_fact_balance_snapshot` |
| August 2026 FX rates deleted | Publish refused, because `balance_eur` would have been null |
| Quarantine tolerance set to zero | WARN recorded, run continues |
| A Czech amount parsed as if the comma were a thousands separator | Row count still matches the trailer, the amount does not, and the control total check catches it |

Measured locally (Docker, Spark 3.5.5, Delta 3.2.1, seed 42), from
[docs/results/phase3_local_run.json](docs/results/phase3_local_run.json):

| Step | Seconds |
|---|---|
| Bronze | 51.6 |
| Silver | 76.2 |
| Silver checks (43) | 34.7 |
| Gold, including its 43 checks, staging and publish | 92.1 |
| Total | 254.6 |

All 86 checks passed, 0 failures. The 86 checks took 77.6 seconds of that in
total; the slowest is the control total check at 5.25 seconds. These are local
container timings on 2 cores, not Fabric timings. Concepts and the reasoning:
[docs/concepts/04-data-quality-gates.md](docs/concepts/04-data-quality-gates.md).

## Run it

```bash
py -3.11 -m pip install -r requirements-dev.txt
py -3.11 -m pip install -e .
py -3.11 -m pytest -q
py -3.11 -m portfolio_migration generate
```

Spark tests and a local medallion run (Spark needs winutils on Windows, so use Docker):

```bash
docker build -f Dockerfile.spark -t portfolio-spark:3.5 .
docker run --rm -v "%cd%:/repo" -e PYTHONPATH=/repo/src portfolio-spark:3.5 python -m pytest -q tests/spark
docker run --rm -v "%cd%:/repo" -e PYTHONPATH=/repo/src portfolio-spark:3.5 python -m portfolio_migration lakehouse --landing /repo/data/landing --lake /tmp/lake
```

## Repo layout

```
src/portfolio_migration/
  config.py               countries, products, calendar, DPD buckets
  generate/core.py        simulates the core systems (the truth)
  generate/landing.py     writes raw files as the source systems deliver them
  legacy/extracts.py      the old DWH job's three extracts
  legacy/workbook.py      builds the formula driven Excel pack
  legacy/evaluate.py      recalculates the workbook formulas with pycel
  lakehouse/io.py         Lake abstraction: Fabric default lakehouse or local Delta folders
  lakehouse/bronze.py     raw as landed, incremental by file
  lakehouse/silver.py     parse, validate, quarantine, dedup, audit
  lakehouse/gold.py       star schema, written to staging then published if the audit passes
  lakehouse/quality.py    check engine, results table, publish gate
  lakehouse/dq_checks.py  the check list: what is guaranteed, and at what severity
fabric/notebooks/         thin Fabric notebooks (generated by scripts/build_fabric_notebooks.py)
fabric/pipelines/         pipeline definition
fabric/sql/               checks to run in the SQL analytics endpoint
Dockerfile.spark          local Spark matching Fabric Runtime 1.3
tests/                    pytest suite, run in GitHub Actions
docs/concepts/            one concept guide per phase, with interview lines
docs/fabric-steps/        exact portal steps per phase
docs/ISSUES_AND_FIXES.md  real problems hit during the build
```

## Design decisions so far

| Decision | Why |
|---|---|
| Generate a single truth, then derive both the landed files and the legacy extracts from it | Every legacy vs gold difference must then have a cause. Nothing can hide as noise |
| Published legacy numbers come from recalculating the workbook formulas, not from Python | The reconciliation tests the workbook the business actually uses |
| Landed files carry realistic defects (locale formats, resends, retries, trailers, truncated JSON) | Silver has real work to do, and every defect is counted so tests can prove silver handled it |
| Pinned dependency versions and a test that regenerates the committed legacy figures | Any number in the docs reproduces from the seed |
| 18 month burn in before the 24 month window | The first reported month already has a mature book, as a real lender would |
| Gold is written to staging, audited, then published | A failed check leaves yesterday's good data in place instead of publishing today's wrong data |
| Checks are declared as data, with a severity each | A reviewer reads the list, and adding a rule cannot break the engine |
| Amounts reconciled against each file's trailer, not just row counts | A parse that stays numeric keeps the row count and moves the money |

## Documentation

* [Issues and fixes](docs/ISSUES_AND_FIXES.md)
* [01 The legacy world](docs/concepts/01-legacy-world.md)
* [02 Fabric lakehouse primer](docs/concepts/02-fabric-lakehouse-primer.md)
* [03 Medallion design](docs/concepts/03-medallion-design.md)
* [04 Data quality gates](docs/concepts/04-data-quality-gates.md)
* [Phase 1 portal steps](docs/fabric-steps/phase-1.md)
* [Phase 2 portal steps](docs/fabric-steps/phase-2.md)
* [Phase 3 portal steps](docs/fabric-steps/phase-3.md)
