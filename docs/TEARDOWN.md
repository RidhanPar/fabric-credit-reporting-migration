# Teardown

What to remove when you are finished, in the order that stops the money first.

## If you are paying for a capacity, do this first

A pay as you go Fabric capacity bills per hour while it is running, whether
anything uses it or not.

1. **Azure portal**, search for your Fabric capacity, **Pause**. Billing for
   compute stops. The workspaces and their data stay, and a paused capacity means
   the items are unusable until you resume, which is what you want between
   sessions.
2. If you are finished entirely, **Delete** the capacity. Deleting a capacity
   does not delete the workspaces, but it leaves them without compute, so do the
   workspace cleanup below as well.
3. OneLake storage is billed separately and is small for this project (the landed
   files are about 39 MB and the Delta tables a few hundred MB), but it does not
   stop when the capacity pauses. Deleting the workspaces is what stops it.

A Fabric trial capacity costs nothing and expires on its own, so with a trial
there is nothing urgent here.

## Fabric workspaces

Do prod first, so a scheduled pipeline cannot fire against a half removed dev.

For each of `portfolio-reporting-prod` and `portfolio-reporting-dev`:

1. Open the workspace, **Settings**, and turn off any schedule on
   `pl_portfolio_medallion` and on `nb_05_monitoring`. A scheduled run against a
   deleted lakehouse produces alert noise.
2. Delete the **deployment pipeline** `portfolio-reporting` (deployment pipelines
   live outside the workspace, under Deployment pipelines).
3. In dev only: **Workspace settings**, **Git integration**, **Disconnect**.
   Disconnecting does not touch the GitHub repository.
4. Delete the workspace. That removes the lakehouse, its Delta tables, the landed
   files, the notebooks, the pipeline, the semantic model, the report and the
   Environment in one action.

If you want to keep the evidence rather than the running system, take the
screenshots first: the pipeline run, the data quality results, the migration
evidence page, and the row level security test. Those are what an interview asks
for, and they cannot be recovered after deletion.

## Local machine

Nothing here costs money, so this is housekeeping.

```bash
docker image rm portfolio-spark:3.5
```

```bash
docker builder prune -f
```

Generated data and build output (all regenerable from the seed, and already
gitignored):

```bash
rm -rf data/landing data/_truth build
```

Keep `data/legacy/Monthly_Portfolio_Pack.xlsx`,
`data/legacy/legacy_published_kpis.csv`, `data/reconciliation/` and
`docs/results/`. They are committed evidence: the reconciliation tests read them,
and every number in the README traces to them.

To regenerate everything later:

```bash
py -3.11 -m portfolio_migration generate
```

## GitHub

Keep the repository. It is the deliverable.

Two things worth doing if you stop working on it:

1. Reset the semantic model's connection placeholders, if you ever filled them in
   with a real endpoint:

```bash
py -3.11 scripts/set_semantic_model_connection.py --reset
```

2. Close any open pull requests, or merge them, so the default branch is the whole
   project rather than a partial one.

## What is safe to delete and what is not

| Item | Safe to delete | Why |
|---|---|---|
| Fabric workspaces | Yes, after screenshots | Everything in them regenerates from this repo and the seed |
| The Fabric capacity | Yes | Pause first if you might come back |
| `data/landing`, `data/_truth`, `build/` | Yes | Deterministic from seed 42 |
| Docker image | Yes | Rebuilt by one command |
| `data/legacy/` | **No** | Committed evidence, and the reconciliation tests read it |
| `data/reconciliation/`, `docs/results/` | **No** | Every number in the README traces to these files |
| `docs/screenshots/` | **No** | Once a screenshot exists it cannot be regenerated without a capacity |
