# Phase 3 manual steps

Phase 3 needs no new Fabric items. The checks and the gate are inside the
notebooks you already imported, so the only Fabric work is replacing the wheel
and rerunning.

If you do not have Fabric capacity yet, skip to section D. Everything in Phase 3
is provable locally, and the Fabric part is a 10 minute job once you have a capacity.

## A. Build and upload the new wheel

```bash
py -3.11 -m pip wheel . --no-deps -w dist
```

1. Open `env_portfolio`, **Custom Library**, delete the 0.2.0 wheel, upload `portfolio_migration-0.3.0-py3-none-any.whl`.
2. **Save**, **Publish**, **Publish all**, and wait for "Published".

## B. Replace the notebooks

Workspace, **Import**, **Notebook**, **From this computer**, select all three files in
`fabric/notebooks/` and allow them to overwrite. Re-check on each notebook that
`lh_portfolio` is the default lakehouse and the Environment is `env_portfolio`
(an import can reset these).

## C. Run the pipeline and read the gate

Open `pl_portfolio_medallion` and **Run**.

**You should see:**

* Silver's output now has a second table of 43 check results, all `passed` true.
* Gold prints `43 gold checks passed before publishing`.
* In the lakehouse you now also have `stg_` copies of the 10 gold tables, and a
  new `dq_results` table.

Then run `fabric/sql/phase3_checks.sql` in the SQL analytics endpoint.
Query 1 should return 86 rows, all passed. Query 4 should show `unexplained` as 0.

**Screenshots:**
* `docs/screenshots/p3-dq-results.png` (query 1)
* `docs/screenshots/p3-pipeline-green.png` (the pipeline run)

## D. Prove the gate blocks a bad publish

This is the part worth demonstrating in an interview, and it runs locally.

```bash
docker run --rm -v "%cd%:/repo" -e PYTHONPATH=/repo/src portfolio-spark:3.5 python -m pytest -q tests/spark/test_quality_gates.py
```

The tests break the data on purpose and prove the published tables do not move:

| Test | What it breaks | What it proves |
|---|---|---|
| `test_a_duplicate_in_silver_stops_gold_publishing` | Appends one duplicate balance row | Gold raises, `fact_balance_snapshot` keeps its old row count, and the bad row sits in `stg_fact_balance_snapshot` where it was caught |
| `test_a_missing_fx_rate_stops_gold_publishing` | Deletes August 2026 rates | `balance_eur` would have been null, so the publish is refused |
| `test_warnings_do_not_stop_the_run` | Sets the quarantine tolerance to zero | A WARN failure is recorded and the run continues |
| `test_control_totals_catch_a_decimal_parsing_bug` | Parses a Czech amount as if the comma were a thousands separator | Row count still matches the trailer, the money does not, and the check catches it |

To do the same thing by hand in Fabric once you have capacity: open a new
notebook, append a duplicate row to `silver_balances`, run `nb_03_gold`, and
watch it fail with `DataQualityError` while the published table keeps its old
count. Screenshot that as `docs/screenshots/p3-gate-blocked.png`. Then rerun the
pipeline to clean up.
