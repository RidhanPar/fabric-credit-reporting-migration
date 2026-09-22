# 02. Fabric lakehouse primer (read before Phase 2)

Each concept has one real world case and the line to use in an interview.

## Capacity and workspace

**What:** a *capacity* is the compute you pay for (F2 to F2048, or a free 60 day
trial). A *workspace* is a folder of Fabric items that sits on a capacity and
carries permissions.

**Case:** a lender keeps `portfolio-reporting-dev` and `portfolio-reporting-prod`
as separate workspaces. Analysts can break dev; only the deployment pipeline
writes to prod.

**Say:** "Workspaces are my security and lifecycle boundary. Dev and prod are
separate workspaces so a bad notebook can never touch the board numbers."

## OneLake

**What:** one data lake per tenant, automatically. Every lakehouse stores its
data there as open Delta Parquet files. Think "OneDrive for data".

**Case:** Credit Risk wants the same balance snapshots Finance uses. With
OneLake they add a *shortcut* to the gold table instead of copying it. One copy,
one number.

**Say:** "OneLake removes the copy step, which is where most reconciliation
breaks come from in the first place."

## Lakehouse: Files and Tables

**What:** a lakehouse has two areas. `Files` holds anything (our CSV and JSON
landings). `Tables` holds Delta tables that Spark, SQL and Power BI can all read.
Each lakehouse also gets a read only **SQL analytics endpoint** for T SQL queries.

**Case:** the RO resend lands in `Files/landing/...`; it only becomes a table row
after bronze reads it.

## Delta Lake

**What:** Parquet files plus a transaction log. You get ACID writes, schema
enforcement and **time travel**.

**Case:** an auditor asks what the August balance table looked like before the
September restatement. In a Spark notebook, `SELECT * FROM silver_balance VERSION AS OF 12` answers it.
Excel cannot.

**Say:** "Delta gives me an audit trail for free. Every write is a version."

## Medallion: bronze, silver, gold

| Layer | Rule | Our example |
|---|---|---|
| Bronze | Raw as landed. All columns as strings, plus source file and load time | CZ `14665,02` stays a string |
| Silver | Cleaned, typed, deduplicated. Bad rows go to quarantine with a reason | `14665,02` becomes `14665.02`; blank account id goes to quarantine |
| Gold | Business ready star schema | `fact_balance_snapshot` by account and month |

**Case:** in month 3 you learn the CZ parse rule was wrong. Because bronze kept
the raw strings, you fix silver and rerun. No need to ask the source to resend.

**Say:** "Bronze is my replay buffer. I never transform on the way in."

## Quarantine, not drop

**What:** rows that fail a rule are written to a quarantine table with a reason
code, not silently filtered.

**Case:** the regulator asks why the row count fell by 9 between bronze and
silver. You query `silver_quarantine` and show 9 rows, reason
`MISSING_ACCOUNT_ID`, with file and line number.

**Say:** "Every row that leaves the pipeline leaves a receipt."

## Notebooks vs Dataflow Gen2 vs Warehouse

| Option | Good for | Why not here |
|---|---|---|
| Dataflow Gen2 | Low code Power Query | Hard to unit test, harder to diff in Git |
| Warehouse | T SQL teams | Our JSON flattening and locale parsing is easier in PySpark |
| **Spark notebooks** | Code, tests, Git | Chosen |

**Say:** "I chose notebooks because the logic is testable in CI and diffs cleanly in Git."

## Data pipelines

**What:** Fabric's orchestrator (the Data Factory engine). Activities run in
order with success and failure paths.

**Case:** silver must not run if bronze failed, and gold must not publish if a
quality check fails. On failure the pipeline writes to a run log and alerts.

## Idempotency

**What:** running the same load twice gives the same result.

**Case:** the RO March file arrives twice. Silver keeps one row per account and
month, so the resend changes nothing. Rerunning the whole pipeline also changes
nothing.

**Say:** "Every step is safe to rerun. That is what makes a 2am failure boring."
