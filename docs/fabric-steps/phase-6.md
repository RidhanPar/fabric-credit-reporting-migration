# Phase 6 manual steps

Four things: connect dev to Git, promote to prod, wire the alerts to a
destination, and record the timings. Section A runs now. The rest needs a capacity.

## A. Check the monitoring locally (no capacity needed)

```bash
docker run --rm -v "%cd%:/repo" -e PYTHONPATH=/repo/src portfolio-spark:3.5 python -m portfolio_migration lakehouse --landing /repo/data/landing --lake /repo/build/lake --summary /repo/docs/results/phase6_local_run.json
```

**You should see** the run summary ending with the monitor step:
`"alerts_evaluated": 6, "fired": [], "stale_sources": []`.

Then the tests that break things on purpose:

```bash
docker run --rm -v "%cd%:/repo" -e PYTHONPATH=/repo/src portfolio-spark:3.5 python -m pytest -q tests/spark/test_ops_monitoring.py
```

| Test | What it breaks | What it proves |
|---|---|---|
| `test_a_failing_step_is_logged_and_the_error_is_not_swallowed` | A step raises | The failure is in `ops_pipeline_run` with the error text, and the exception still stops the run |
| `test_a_failed_step_fires_a_critical_alert` | A FAILED row in the run log | `PIPELINE_FAILED` fires and monitoring raises |
| `test_stale_data_fires_a_critical_alert_even_though_every_run_succeeded` | The clock moves 120 days | `DATA_STALE` fires even though every run succeeded |
| `test_gold_falling_behind_silver_fires_an_alert` | Silver completes after gold | `GOLD_BEHIND_SILVER` fires, so a report behind the data is caught |
| `test_a_failed_error_check_fires_an_alert` | A failed ERROR check in `dq_results` | `QUALITY_ERROR` fires |

## B. Connect the dev workspace to Git

1. Open `portfolio-reporting-dev`, **Workspace settings**, **Git integration**.
2. Git provider **GitHub**. Sign in and authorise Fabric.
3. Repository `RidhanPar/fabric-credit-reporting-migration`, branch `main`,
   **Git folder**: `fabric/workspace`.
4. **Connect and sync**.

**You should see** Fabric list the items in that folder and offer to bring them
in: 5 notebooks, 1 data pipeline, 1 semantic model. Accept.

**Then check three things:**

* Each notebook has `lh_portfolio` as its default lakehouse and `env_portfolio`
  as its Environment. A Git created notebook may arrive without them.
* The pipeline's four activities point at the right notebooks. The committed
  `pipeline-content.json` carries placeholder item ids, so Fabric will ask you to
  re-pick them on first sync. Once you commit back from Fabric, the real ids land
  in the repo and later syncs are clean.
* The semantic model loaded. If it did not, see `docs/fabric-steps/phase-5.md`
  section B.

**Screenshots:** `docs/screenshots/p6-git-connected.png` (the source control
panel showing no uncommitted changes) and `docs/screenshots/p6-git-history.png`.

**Prove it works both ways:** rename a measure's display folder in the Fabric
model, then open the source control panel. The change appears as an uncommitted
diff. Commit it, and the TMDL changes in this repo. That is the whole point.

## C. Create the prod workspace and the deployment pipeline

1. **Workspaces**, **New workspace**: `portfolio-reporting-prod`, same capacity.
   Do **not** connect it to Git. Promotion is the only path in.
2. In that workspace create the lakehouse `lh_portfolio` (same name as dev) and
   the Environment `env_portfolio` with the same wheel version.
3. **Deployment pipelines**, **New pipeline**: `portfolio-reporting`, two stages,
   Development and Production. Assign the workspaces.
4. Set the deployment rules from
   [fabric/deployment/deployment-rules.json](../../fabric/deployment/deployment-rules.json).
   For each notebook: **Deployment rules**, default lakehouse, point it at
   `lh_portfolio` in the prod workspace. Do the same for the semantic model's
   data source.
5. Work through the `before_promoting` checklist in that file, then **Deploy**.

**You should see** the items appear in prod, and the rules listed against each
item. Then run the `after_promoting` checklist.

**Screenshots:** `docs/screenshots/p6-deployment-pipeline.png` and
`docs/screenshots/p6-deployment-rules.png` (a rule showing dev and prod lakehouses).

The rule that repoints the default lakehouse is the single most important line of
configuration in the project. Without it, prod runs read dev data and every run
looks fine.

## D. Wire the alerts to somewhere a person looks

The conditions are already evaluated in code and written to `ops_alerts`. What is
left is delivery, and there are three places to set it.

1. **Pipeline failure path.** Open `pl_portfolio_medallion`, add an **Office 365
   Outlook** or **Teams** activity, and drag the red **On failure** arrow from
   Monitoring to it. Subject: `Portfolio pipeline FAILED: @{pipeline().RunId}`.
   Because the monitoring notebook raises on a critical alert, this fires for a
   bad run and for stale data, not only for a crash.
2. **Scheduled check independent of the pipeline.** Schedule
   `nb_05_monitoring` on its own at 07:00. If the pipeline never ran at all, the
   pipeline's own failure path cannot fire, and this is what catches it.
3. **Activator rule on the table.** Create an Activator rule on `ops_alerts`
   where `fired` is true and `severity` is `CRITICAL`, sending a Teams message.
   This is the one to use if other teams want to subscribe without touching the
   pipeline.

**You should see** one email or Teams message when a run fails, and none when it
succeeds.

**Screenshot:** `docs/screenshots/p6-alert-wiring.png`

Keep it to those four critical conditions. A pipeline that emails on every run
trains everyone to filter the emails.

## E. Record the timings

1. Pipeline **Output** tab after a successful prod run: note the duration of each
   activity and of the whole run.
2. Open the report, **Optimize**, **Performance analyzer**, **Start recording**,
   refresh the visuals, then use each slicer once. Export the recording.
3. Run it twice: once on a cold model, once warm. Direct Lake pays for loading
   column segments on the first query.

Send me both, and I will put them in [docs/PERFORMANCE.md](../PERFORMANCE.md)
next to the local baseline. Until then that file says "not measured yet", which
is the honest state.

## What to send back

1. Screenshots listed above, into `docs/screenshots/`.
2. Pipeline activity durations, dev and prod.
3. The Performance Analyzer export, or just the per visual DAX times.
4. Anything Fabric complained about on the first Git sync, word for word.
