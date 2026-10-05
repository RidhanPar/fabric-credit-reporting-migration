# Phase 2 manual steps

Time needed: about 45 minutes, most of it waiting for the Environment to publish.
You need Phase 1 done: workspace `portfolio-reporting-dev`, lakehouse `lh_portfolio`,
files uploaded to `Files/landing`.

## A. Build the wheel locally

```bash
py -3.11 -m pip wheel . --no-deps -w dist
```

**You should see:** `dist/portfolio_migration-0.2.0-py3-none-any.whl`.
(CI also builds it: Actions > latest run > artifact `portfolio-migration-wheel`.)

## B. Create the Environment

1. In the workspace: **New item** > **Environment** > name `env_portfolio`.
2. **Home** tab > Runtime: **1.3 (Spark 3.5, Delta 3.2)**.
3. Left menu **Custom Library** > **Upload** > pick the `.whl` from `dist/`.
4. **Save**, then **Publish** > **Publish all**. Publishing takes about 5 to 15 minutes.

**You should see:** status "Published" and `portfolio_migration-0.2.0-py3-none-any.whl` under Custom Library.

**Screenshot:** `docs/screenshots/p2-environment.png`

## C. Import the notebooks

1. Workspace > **Import** > **Notebook** > **From this computer**.
2. Select all three files in `fabric/notebooks/`: `nb_01_bronze.ipynb`, `nb_02_silver.ipynb`, `nb_03_gold.ipynb`.
3. For **each** notebook:
   * Open it. In the explorer on the left, **Add data items** > **Existing data source** > `lh_portfolio`. Make it the default (pin).
   * Toolbar Environment dropdown (shows "Workspace default") > pick `env_portfolio`.
   * Close it (it autosaves).

## D. Run once by hand (bronze only)

Open `nb_01_bronze` > **Run all**.

**You should see:** the version line `portfolio_migration 0.2.0`, then a table with
7 rows. `bronze_balances` shows `new_files` 73 and `new_rows` 228,308.

Run **Run all** a second time. **You should see:** every `new_rows` is 0. That is idempotency.

**Screenshot:** `docs/screenshots/p2-bronze-rerun.png` (the second run, all zeros)

## E. Build the pipeline

1. Workspace > **New item** > **Data pipeline** > name `pl_portfolio_medallion`.
2. **Activities** > **Notebook**, three times. Name them `Bronze`, `Silver`, `Gold`.
3. For each activity, **Settings** tab:
   * Workspace: `portfolio-reporting-dev`, Notebook: the matching `nb_0x_...`.
   * **Base parameters** > **New**: Name `batch_id`, Type `String`,
     Value > **Add dynamic content** > `@pipeline().RunId`.
4. **General** tab of each: Retry `1`, Retry interval `60` seconds. (Safe because every layer is idempotent.)
5. Drag the green **On success** handle from Bronze to Silver, and from Silver to Gold.
6. **Save**, then **Run**.

Reference definition: `fabric/workspace/pl_portfolio_medallion.DataPipeline/pipeline-content.json`.

**You should see:** all three activities go green. Bronze adds 0 rows this time, because step D already loaded the files.

**Screenshots:**
* `docs/screenshots/p2-pipeline-canvas.png` (three activities chained)
* `docs/screenshots/p2-pipeline-run.png` (Output tab after the run, with durations)

## F. Record the run times

Pipeline **Output** tab: note the **Duration** of each activity and of the whole run.
Send me those four numbers. Only numbers you send go into the README.

## G. Check the results in SQL

1. Open `lh_portfolio` > switch the top right dropdown from Lakehouse to **SQL analytics endpoint**.
2. **New SQL query** > paste `fabric/sql/phase2_checks.sql` > **Run**.

Tables can take a minute to appear in the endpoint after the pipeline finishes.

**You should see:**

* Query 1: `unexplained` is 0 on every line. balances: 228,308 / 73 / 9 / 2,869 / 225,357.
* Query 2: balances `MISSING_ACCOUNT_ID` 9; application_events starting `MALFORMED_JSON` 37.
* Query 3: 24 months. 2024-09 about 18,016,616.68 and 2026-08 about 30,067,420.53.

**Screenshots:**
* `docs/screenshots/p2-audit.png` (query 1)
* `docs/screenshots/p2-quarantine.png` (query 2)

## If something fails

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: portfolio_migration` | Environment not attached or not published | Step B4, then pick `env_portfolio` in the notebook |
| `Table or view not found: bronze_products` in silver | Bronze never ran, or a different default lakehouse | Check the pinned lakehouse is `lh_portfolio` |
| Bronze loads 0 files on the first run | Files not under `Files/landing/...` | The path must be `Files/landing/core_banking/PL/balances/...` |

Send me "Phase 2 done", the four durations, and anything that differed.
