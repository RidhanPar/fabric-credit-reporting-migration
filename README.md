# Legacy Excel to Microsoft Fabric: consumer credit portfolio reporting migration

A consumer lender (personal loans and credit cards in Poland, the Czech Republic
and Romania) runs its monthly board portfolio pack from a formula driven Excel
workbook. This project migrates it to a Microsoft Fabric medallion lakehouse
and a Direct Lake Power BI model, and proves with a reconciliation which of the
old numbers were wrong.

All data is synthetic and generated from a fixed seed. The legacy workbook is a
real formula workbook, and its published figures come from recalculating its own
formulas.

> **Status:** Phase 1 of 7 complete. This README grows each phase. Every number
> in it traces to a committed run output.

## Phases

| # | Phase | Status |
|---|---|---|
| 1 | Legacy world: source data, landed files, legacy workbook | Done |
| 2 | Medallion lakehouse: bronze, silver, gold notebooks and pipeline | Next |
| 3 | Data quality gates | |
| 4 | Reconciliation, legacy vs gold | |
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

## Run it

```bash
py -3.11 -m pip install -r requirements-dev.txt
py -3.11 -m pip install -e .
py -3.11 -m pytest -q
py -3.11 -m portfolio_migration generate
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

## Documentation

* [Issues and fixes](docs/ISSUES_AND_FIXES.md)
* [01 The legacy world](docs/concepts/01-legacy-world.md)
* [02 Fabric lakehouse primer](docs/concepts/02-fabric-lakehouse-primer.md)
* [Phase 1 portal steps](docs/fabric-steps/phase-1.md)
