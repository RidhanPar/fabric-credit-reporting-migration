# Legacy Excel to Microsoft Fabric: consumer credit portfolio reporting migration

A consumer lender (personal loans and credit cards in Poland, the Czech Republic
and Romania) runs its monthly board portfolio pack from a formula driven Excel
workbook. This project migrates it to a Microsoft Fabric medallion lakehouse
and a Direct Lake Power BI model, and proves with a reconciliation which of the
old numbers were wrong.

All data is synthetic and generated from a fixed seed. The legacy workbook is a
real formula workbook, and its published figures come from recalculating its own
formulas.

> **Status:** Phases 1 to 6 of 7 built. This README grows each phase. Every number
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
| 4 | Reconciliation, legacy vs gold | Done |
| 5 | Semantic model (TMDL, Direct Lake, RLS) and report spec | Authored, not yet loaded in Fabric |
| 6 | Production operations (Git, deployment pipeline, monitoring) | Built, portal wiring pending a capacity |
| 7 | Documentation, runbook, teardown | Next |

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

## Phase 4: the reconciliation, and what it found

672 published figures were compared, month by month and country by country
(24 month ends, 4 scopes, 7 KPIs). 498 of them differed. **Unexplained
differences: 0.** Full write up with the proof for each finding:
[docs/RECONCILIATION_FINDINGS.md](docs/RECONCILIATION_FINDINGS.md).

### The old report was wrong, and here is the proof

Three faults in the legacy workbook's own formulas, affecting 336 of the 672
published figures. Each one was proved by correcting that formula in a copy of
the workbook and recalculating it, so the effect is measured, not argued.

| | Fault | What it did | Largest single month effect |
|---|---|---|---|
| F1 | A personal loan product counted twice in the portfolio balance | Cards were summed by the wildcard product code `CC*`, and the card consolidation loan is a personal loan whose code starts with CC, so it was added twice from its launch in March 2025 | Group balance overstated by EUR 3,336,161.01 |
| F2 | Romania converted to EUR at a hardcoded rate | The Romania tab multiplied by the literal `0.2012` while the column showing the correct Treasury rate sat unused next to it | Group balance overstated by EUR 614,059.94 |
| F3 | The 30+ DPD rate used the previous month's arrears | The numerator filtered the balance cube on the previous month end while the denominator used the current one, so the first month of the series reported 0.00% | 30+ rate wrong by 3.53 percentage points |

Effect on the headline numbers the board saw, averaged over the 24 months:

| KPI (group) | Legacy pack | New model | Effect |
|---|---|---|---|
| Portfolio balance | overstated | correct | EUR 1,984,052.50 too high on average, 7.32 percent |
| 30+ DPD rate | 3.04% | 3.25% | arrears understated by 0.21 percentage points |
| Approval rate | 51.12% | 56.77% | definition change, not an error |
| Avg balance per customer | EUR 2,929.67 | EUR 2,862.13 | definition change, not an error |

### How the differences were classified

Every one of the 498 differing figures is attributed to a named cause:

* **3 legacy errors**, each proved by recalculating the corrected workbook.
* **5 definition changes** with a measurable effect, each quantified by having
  gold compute both the old and the new definition. Three further conversion
  steps have no effect above tolerance and are listed anyway.
* **1 new model error**, found and fixed during the build. Spark caps the scale
  when one decimal column is divided by another, which had truncated every rate
  in the gold KPI table to 6 decimal places. The reconciliation caught it because
  the rate residual broke tolerance. A data quality check now fails if rates ever
  look truncated again. See issue 10 in
  [docs/ISSUES_AND_FIXES.md](docs/ISSUES_AND_FIXES.md).
* **0 unexplained.** After every cause is removed, the residual is at most
  0.000000004 EUR on any amount and 1e-16 on any rate, against tolerances of
  0.10 EUR and 1e-07.

### Measured locally

The full pipeline, including the KPI table and its checks, takes 282.2 seconds in
the container (bronze 54.2, silver 73.2, silver checks 36.4, gold with its checks
86.7, KPI table 27.4, KPI checks 4.3), from
[docs/results/phase4_local_run.json](docs/results/phase4_local_run.json). The
reconciliation on top takes 50 seconds, most of it recalculating the legacy
workbook four times: as found, and after each of the three fixes.

`tests/test_reconciliation.py` asserts the bridge closes for all 672 figures,
that nothing is unexplained, that each fix still changes exactly the cells it
claims, and that the findings document matches the run.

## Phase 5: the semantic model

A Direct Lake semantic model over the gold tables, saved as TMDL text so a
measure change can be reviewed in a pull request like any other code change.
Generated by [scripts/build_semantic_model.py](scripts/build_semantic_model.py)
into [fabric/workspace/](fabric/workspace/LendCoPortfolio.SemanticModel):
12 tables, 26 measures, 20 relationships, 4 security roles.

* **Measures:** the 7 board KPIs under the definitions agreed in Phase 4, plus
  supporting and time intelligence measures, plus the two legacy definitions kept
  for the parallel run, plus 4 measures for the migration evidence page. Every one
  carries its business definition in the model. Read them in
  [docs/MEASURES.md](docs/MEASURES.md).
* **Row level security:** three country manager roles and a group role. Each
  country role filters `dim_country`, `dim_account` and `dim_customer`, because
  the last two are not joined to the country dimension and a visual built only
  from them would otherwise show every market.
* **Report:** four pages specified in [docs/REPORT_SPEC.md](docs/REPORT_SPEC.md):
  executive summary, delinquency, account level drillthrough, and a migration
  evidence page that puts the reconciliation in front of the business.

Modelling decisions worth defending:

| Decision | Why |
|---|---|
| Direct Lake, not import | The report is current the moment gold publishes. No refresh to schedule, no second copy of the data |
| Balances are semi additive (`LASTNONBLANKVALUE`) | A balance is a stock. Summing 12 month ends would report twelve times the portfolio |
| Local currency columns hidden | Adding koruna to zloty is meaningless, so the model only exposes EUR |
| `discourageImplicitMeasures`, every column `summarizeBy: none` | A report author cannot drag a column in and invent a number |
| Dimensions kept flat, relationships single direction | One path from each fact to each dimension, so no ambiguity and no bidirectional filtering |
| No calculated columns | Direct Lake does not support them, and that work belongs in gold anyway |
| The model is generated from one script | The columns, measures and documentation have a single source, and a Spark test compares the TMDL against the real gold schema |

**Status, stated plainly:** the TMDL is authored, generated, documented and
tested as text. It has not been loaded by the Power BI engine, because Direct
Lake needs a Fabric capacity and the author's account cannot start a Fabric
trial. [docs/fabric-steps/phase-5.md](docs/fabric-steps/phase-5.md) has the
steps, including how to recover if the TMDL needs a formatting fix on first load.

## Phase 6: production operations

### One folder Fabric can sync

Every Fabric item now lives in [fabric/workspace/](fabric/workspace): five
notebooks, the data pipeline and the semantic model, each as a folder with a
`.platform` file, which is the layout Fabric's Git integration reads. The dev
workspace points at that folder. Prod is **not** connected to Git: it is fed only
by the deployment pipeline, so there is exactly one path into production.

[fabric/deployment/deployment-rules.json](fabric/deployment/deployment-rules.json)
records the rules that must be set when promoting, item by item, with the reason
for each. A test fails if a notebook has no rule repointing its default lakehouse,
because a notebook promoted without that rule keeps reading dev data and every run
still looks green.

### Three tables you can report on

| Table | What it holds |
|---|---|
| `ops_pipeline_run` | One row per step per run: start, finish, seconds, status, rows written, and the error text if it failed |
| `ops_freshness` | The age of the newest data per source, against an agreed limit |
| `ops_alerts` | Six alert conditions, evaluated, each with the action to take |

Run status and freshness are separate alarms on purpose. A pipeline that
succeeds while the source never delivered leaves every run green and the report a
month stale, which is the failure mode that embarrasses you in a meeting.

The alert conditions are tested by breaking a copy of the lakehouse: a failed
step, a 120 day gap, silver finishing after gold, and a failed ERROR check all
fire the right alert, and none fire on a clean run. The monitoring notebook runs
on success, failure **or** skip of the earlier steps, because a failed run still
has to raise the alarm, and it raises on a critical alert so the pipeline goes red.

### Measured, not estimated

Local run, Spark 3.5.5 and Delta 3.2.1 on 2 cores, from
[docs/results/phase6_local_run.json](docs/results/phase6_local_run.json):
bronze 68.2s, silver 76.7s, silver checks 37.0s, gold 92.4s, KPI table 24.8s,
KPI checks 5.3s, monitoring 16.7s, **total 321.1s**. The 96 quality checks are
78.7s of that, about a quarter of the run, which is the price of the publish gate.

The report's own aggregations were benchmarked against gold
([docs/results/phase6_query_benchmark.json](docs/results/phase6_query_benchmark.json)):
median 1.0s to 2.3s per query on two cores, with the executive summary query
dropping from 10.2s cold to 1.1s warm. Those are Spark timings, not Direct Lake.
Fabric pipeline durations and Power BI query times are marked "not measured yet"
in [docs/PERFORMANCE.md](docs/PERFORMANCE.md) and stay that way until a real run
produces them.

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
  lakehouse/ops.py        run log, freshness checks, alert conditions
  kpi_definitions.py      KPI names and variant chains, shared and Spark free
  lakehouse/kpis.py       the 7 board KPIs from gold, in old and new definitions
  reconcile/legacy_fixes.py  the faults found in the workbook, as formula patches
  reconcile/bridge.py     the walk from each legacy figure to its gold counterpart
  reconcile/diagnose.py   diagnose a difference from its shape, round by round
  reconcile/report.py     generates the findings document
  semantic_model.py       reads the TMDL back, so the model can be tested
fabric/workspace/    the Power BI semantic model as TMDL text
fabric/workspace/         every Fabric item in Git format: notebooks, pipeline, semantic model
fabric/notebooks/         the same notebooks as .ipynb, for importing by hand
fabric/deployment/        the deployment pipeline's rules, as a reviewable specification
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
| Legacy figures come from recalculating the workbook, before and after each fix | The effect of a fault is measured by the workbook itself, not asserted by me |
| Gold computes the old definition as well as the new one | A definition change is then an exact number, not an estimate |
| A declared tolerance, and a test that fails on any unexplained residual | "Nothing unexplained" stays true after a data change, or CI goes red |
| Alert conditions in tested code, delivery in the portal | The condition can be proved without a capacity; only the destination needs one |
| Monitoring runs on success, failure or skip | A failed run still has to raise the alarm |
| Prod is not connected to Git | One path into production: a reviewed commit, then a promotion |

## Documentation

* [Issues and fixes](docs/ISSUES_AND_FIXES.md)
* [01 The legacy world](docs/concepts/01-legacy-world.md)
* [02 Fabric lakehouse primer](docs/concepts/02-fabric-lakehouse-primer.md)
* [03 Medallion design](docs/concepts/03-medallion-design.md)
* [04 Data quality gates](docs/concepts/04-data-quality-gates.md)
* [05 Reconciliation](docs/concepts/05-reconciliation.md)
* [06 The semantic model](docs/concepts/06-semantic-model.md)
* [07 Production operations](docs/concepts/07-production-operations.md)
* [Measured performance](docs/PERFORMANCE.md)
* [Measure dictionary](docs/MEASURES.md)
* [Report specification](docs/REPORT_SPEC.md)
* [Reconciliation findings](docs/RECONCILIATION_FINDINGS.md)
* [Phase 1 portal steps](docs/fabric-steps/phase-1.md)
* [Phase 2 portal steps](docs/fabric-steps/phase-2.md)
* [Phase 3 portal steps](docs/fabric-steps/phase-3.md)
* [Phase 4 portal steps](docs/fabric-steps/phase-4.md)
* [Phase 5 portal steps](docs/fabric-steps/phase-5.md)
* [Phase 6 portal steps](docs/fabric-steps/phase-6.md)
