# Phase 4 manual steps

The reconciliation runs locally and needs no Fabric capacity. Section A is the
part to do now. Section B is for when you have a capacity and want the
reconciliation tables inside the lakehouse for the Power BI report.

## A. Run the reconciliation locally

The gold KPI table is produced by Spark, so it comes from the container. The
reconciliation itself is plain Python and runs on Windows.

```bash
docker run --rm -v "%cd%:/repo" -e PYTHONPATH=/repo/src portfolio-spark:3.5 python -m portfolio_migration lakehouse --landing /repo/data/landing --lake /tmp/lake --kpi-csv /repo/docs/results/gold_kpi_monthly.csv
```

```bash
py -3.11 -m portfolio_migration reconcile
```

**You should see** a JSON summary ending with `"unexplained_cells": 0`, and these
files written:

* `docs/RECONCILIATION_FINDINGS.md`, the findings document
* `data/reconciliation/bridge.csv`, one row per published figure with every step of the walk
* `data/reconciliation/attribution.csv`, one row per difference and named cause
* `data/reconciliation/peeling_trail.csv`, the diagnosis after each fault was corrected

Then read `docs/RECONCILIATION_FINDINGS.md`. That document is the deliverable of
this phase, and the three faults are named in it.

To see the tests that keep it honest:

```bash
py -3.11 -m pytest -q tests/test_reconciliation.py
```

## B. In Fabric, later

1. Add `pycel` to `env_portfolio`: open the Environment, **Public Library**,
   **Add from PyPI**, type `pycel`, save and publish.
2. In the lakehouse, create `Files/legacy` and upload
   `data/legacy/Monthly_Portfolio_Pack.xlsx` into it.
3. Import `fabric/notebooks/nb_04_reconciliation.ipynb`, attach `lh_portfolio`
   and `env_portfolio`, and run it.
4. Add it to `pl_portfolio_medallion` as a fourth activity after Gold, with the
   same `batch_id` parameter.

**You should see** three new tables: `recon_bridge`, `recon_attribution` and
`recon_findings_summary`, and the notebook printing the same figures the local
run produced. The notebook asserts that nothing is unexplained, so if a future
data change breaks the reconciliation, the pipeline goes red.

**Screenshots** for later: `docs/screenshots/p4-recon-tables.png` (the recon
tables in the lakehouse) and `docs/screenshots/p4-recon-notebook.png` (the
notebook output).

## What to say about this phase in an interview

"The migration did not just reproduce the old report. It proved the old report
was wrong. Three faults in the legacy workbook's formulas, each one corrected in
a copy of the workbook and recalculated so the effect is measured rather than
asserted. Every remaining difference is an agreed definition change, quantified
by computing both definitions. Nothing is left unexplained, and a test fails if
that ever stops being true."
