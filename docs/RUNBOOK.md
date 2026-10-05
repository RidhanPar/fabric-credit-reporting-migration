# Migration runbook

How a team would actually run this: who does what, what happens each month end,
what to do when it breaks, and what has to be true before the Excel pack is
switched off.

Written as instructions, not as description. Anything that needs a Fabric
capacity is marked.

## Roles

| Role | Owns |
|---|---|
| BI analyst (you) | The lakehouse code, the semantic model, the reconciliation, this runbook |
| Finance reporting lead | The KPI definitions, the sign off on each month's reconciliation, the decision to retire the Excel pack |
| Country analysts (3) | Confirming their country's figures during the parallel run |
| Data platform engineer | The capacity, the workspaces, the Git connection, the deployment pipeline |
| Source system owners | The landed files arriving on time and in the agreed shape |

One name per role, written down. "The team" is not an owner.

## Before you start

1. The seven KPI definitions are agreed **in writing**, including the three that
   change ([README KPI table](../README.md)). A definition change discovered
   during the parallel run is a conversation. One discovered after cutover is an
   incident.
2. The source owners agree what lands, where, and by when: the file names, the
   formats, the trailer records, and the latest acceptable arrival time.
3. Materiality is agreed: EUR 0.10 on an amount, 1e-07 on a rate, exact on
   counts. Anything larger must be named.
4. Two workspaces exist, `portfolio-reporting-dev` connected to Git and
   `portfolio-reporting-prod` connected to nothing. (Capacity.)

## The monthly close, step by step

Run in prod. Day 1 to 2 of the month, after the source systems close.

| # | Step | Expected | If not |
|---|---|---|---|
| 1 | Confirm the landed files for the new month are in `Files/landing` | One balance file and one repayment file per country, one applications file | Chase the source owner. Do not run the pipeline against a missing country |
| 2 | Run `pl_portfolio_medallion` | Four activities green | See the failure playbooks below |
| 3 | Check `ops_pipeline_run` for this batch | One SUCCEEDED row per step | The failed step's `detail` column holds the error |
| 4 | Check `ops_freshness` | No row with `is_stale` true | A stale source means the pipeline ran but the data did not arrive |
| 5 | Check `dq_results` for this batch | No failed ERROR check. Warnings are reviewed, not blocking | A failed ERROR check means gold did not publish. The report is still showing last month, correctly dated |
| 6 | Run `nb_04_reconciliation` | `unexplained_cells` is 0 | **Stop. Do not sign off.** See "when the reconciliation does not close" |
| 7 | Open the report, migration evidence page | Unexplained differences 0, and the legacy error effect in line with the previous month | Investigate before circulating |
| 8 | Send the reconciliation summary to the Finance reporting lead | Sign off recorded against the batch id | |
| 9 | Only after sign off, circulate the pack | | |

Steps 3 to 5 are three queries. `fabric/sql/phase3_checks.sql` has the first two
and the quality query ready to paste.

## The parallel run

Run both reports for three consecutive month ends before retiring the Excel pack.

Each month:

1. Finance produces the Excel pack as usual.
2. You run the pipeline and the reconciliation.
3. Every difference is classified: legacy error, new model error or definition
   change. The attribution table does this per figure.
4. Each country analyst confirms their own country's figures, not just the group.
5. The Finance reporting lead signs the month off, or names what is unexplained.

**Exit criteria, all four:**

* Three consecutive months reconciled with zero unexplained differences.
* Every legacy error quantified, written down, and accepted by Finance, including
  whether prior reporting needs restating.
* Every definition change announced to the pack's readers **before** the first
  month they see it.
* The country analysts have used the new report for a month and can find what
  they used to find in the spreadsheet, which is what the account level
  drillthrough is for.

## Retiring the Excel pack

Only after the exit criteria are met.

1. Announce the cutover date and what changes on it, with the definition changes
   restated.
2. Move the workbook to a read only archive location. Keep it: it is the evidence
   for the restatement conversation, and for anyone who asks later why a number
   moved.
3. Keep `nb_04_reconciliation` in the pipeline for one more quarter, pointed at
   the archived workbook. It costs a few minutes a month and it is the thing that
   proves the new numbers have not drifted.
4. Record in the change log which figures were restated and from which month.

## Failure playbooks

### A pipeline step failed

1. `ops_pipeline_run`, filter to the batch id, find the FAILED row. The `detail`
   column has the exception and the end of the traceback.
2. Fix the cause, then rerun the whole pipeline. **Rerunning is safe**: bronze
   skips files it has already loaded, silver and gold are rebuilt in full, and the
   reconciliation is read only.
3. If bronze failed part way, no partial state is left: a file is either in a
   bronze table or it is not.

### An ERROR quality check failed and gold did not publish

This is the gate working. The report is still serving the previous publish.

1. `dq_results`, filter to the batch and `passed = false`. The `detail` column
   holds a sample of the failing rows.
2. Decide which of the three it is:
   * **The source sent something new and wrong.** Go back to the source owner.
     Do not weaken the check to get the pack out.
   * **The source sent something new and legitimate**, for example a new product
     code. Add it to the reference data or the accepted values, with Finance
     agreeing, and rerun.
   * **The check is wrong.** Fix the check, in a pull request, with a test.
3. The staging tables `stg_*` hold exactly what was rejected, which is usually the
   fastest way to see the problem.

### The reconciliation does not close

An unexplained difference means one of the two sides is wrong and nobody knows
which.

1. `recon_attribution`, filter to `classification = 'UNEXPLAINED'`, to see which
   month, scope and KPI.
2. Read `peeling_trail.csv` for the shape of the difference: when it starts,
   whether it scales, whether only one country moves. The shape tells you where
   to look before you open anything.
3. Work the three candidates in this order: a changed source extract, a new
   definition nobody told you about, a bug in the new model.
4. Do not sign the month off. A pack with an unexplained difference is how a
   migration loses its credibility, and credibility is the only reason anyone
   believes the next number.

### Data is stale but every run is green

The `DATA_STALE` alert, which exists precisely for this.

1. `ops_freshness` names the source and its age.
2. Check whether the file landed at all. A pipeline that processes nothing
   succeeds, which is why freshness is a separate alarm from run status.
3. Chase the source owner with the file name and the expected arrival time from
   the agreement.

### The report is slower than it was

1. Capacity metrics app: check whether Direct Lake fell back to DirectQuery. That
   is the usual cause of a sudden change.
2. Performance Analyzer on the slow page, per visual, and compare against the
   baseline in [PERFORMANCE.md](PERFORMANCE.md).
3. The usual fixes, in order: fewer visuals per page, no account level cardinality
   outside the drillthrough page, and check nothing has started reading a column
   that was meant to stay hidden.

## Changing something

| Change | Path |
|---|---|
| A quality check | Pull request to `dq_checks.py`, with the severity decided with Finance. CI runs it |
| A KPI definition | Finance agrees first. Then `kpis.py`, the measure in `build_semantic_model.py`, and the reconciliation's variant chain, in one pull request. The reconciliation will quantify the change for you |
| A new source file or column | `bronze.py` for the feed, `silver.py` for the typing and rules, a check in `dq_checks.py`, then the model if it should be visible |
| A measure's wording | `build_semantic_model.py`, which regenerates the TMDL and the measure dictionary. CI fails if they are stale |
| Anything in Fabric | Dev workspace, commit through Git, pull request, then promote. Never edit prod directly (capacity) |

Promotion follows [deployment-rules.json](../fabric/deployment/deployment-rules.json),
including the before and after checklists. The rule that repoints the default
lakehouse is the one that matters: without it, prod reads dev data and every run
still looks green.

## Monthly and quarterly housekeeping

* **Monthly:** review the warnings in `dq_results`. They are not blocking, which
  is exactly why they need a scheduled look.
* **Monthly:** check the quarantine rate per entity. A step change means the feed
  changed shape.
* **Quarterly:** re-read the alert conditions in `ops.py` and ask whether anyone
  acted on each one. An alert nobody acts on should be deleted or fixed.
* **Quarterly:** rerun the query benchmark and compare against the baseline.
* **Quarterly:** confirm the wheel version in each Environment matches the repo.
