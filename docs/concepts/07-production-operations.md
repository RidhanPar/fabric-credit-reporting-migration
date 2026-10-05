# 07. Production operations (read before Phase 6)

The pipeline works. Now it has to keep working on a Monday morning when you are
on holiday and nobody remembers how it is wired.

## Concept: Git integration

**What:** a Fabric workspace can be connected to a branch of a Git repository.
Items (notebooks, pipelines, semantic models) are stored as folders of text.
"Commit" sends workspace changes to Git, "update" brings Git changes into the
workspace.

**Case:** an analyst renames a measure in the browser at 16:50 on Friday. With
Git integration the change appears as a diff you can see, revert, or question in
a pull request. Without it, the change is invisible until a number moves.

Two things matter in practice:

* Fabric expects one folder per item, named `<name>.<ItemType>`, each with a
  `.platform` file. That is why this repo keeps all Fabric items under
  `fabric/workspace/`.
* Git integration is per workspace and per branch. Dev points at this repo. Prod
  does **not**: prod is fed by the deployment pipeline, so there is exactly one
  path into production.

**Say:** "Dev is connected to Git, prod is fed by the deployment pipeline. That
way nothing reaches production except through a reviewed commit and a promotion."

## Concept: deployment pipelines

**What:** a Fabric deployment pipeline has stages, typically dev, test and prod.
You promote items from one stage to the next. **Deployment rules** rewrite
environment specific values during promotion, so a notebook that reads the dev
lakehouse reads the prod lakehouse after promotion.

**Case:** the gold notebook has `lh_portfolio` in the dev workspace attached as
its default lakehouse. Promote it without a rule and prod would quietly read dev
data, which is the worst kind of bug: everything works and the numbers are wrong.

**Say:** "The rule that repoints the default lakehouse is the single most
important line of configuration in the whole deployment. Without it prod reads
dev and nobody notices."

## Concept: a run log you can report on

**What:** each step of each run writes a row: which batch, which step, when it
started, how long it took, whether it succeeded, and how many rows it wrote.

**Case:** "the report looks wrong" at 09:10. With a run log you answer in one
query: last night's silver step failed at 04:12, gold never published, so the
report is showing Thursday's data. Without one you start opening notebooks.

Fabric has its own monitoring hub, which shows pipeline runs. A run log table is
still worth having, because it is yours: it survives item changes, it joins to
the data quality results, and it can be put on a report page.

**Say:** "Fabric's monitor tells me a pipeline failed. My run log tells me what
the business can and cannot trust this morning, which is the question I am
actually being asked."

## Concept: freshness, and why it is not the same as success

**What:** freshness is the age of the newest data, not the age of the last run.

**Case:** the pipeline succeeded at 04:00 and loaded nothing, because the source
system never dropped the file. Every run is green and the report is a month out
of date. A freshness check compares the newest month end in gold against what the
calendar says should be there, and fails when the gap is too large.

So two separate conditions, and a real monitor needs both:

| Condition | Question |
|---|---|
| Run status | Did the pipeline finish? |
| Freshness | Is the data as new as it should be? |

**Say:** "Success and freshness are different alarms. A green run on stale data is
the failure mode that embarrasses you in a meeting."

## Concept: alerting that someone will actually act on

**What:** an alert is a condition, a severity, a message that says what to do, and
a destination.

**Case:** a pipeline that emails on every run trains everyone to filter the
emails. Alert on: the run failed, an ERROR quality check failed, data is stale,
or the reconciliation has an unexplained difference. Those four are worth waking
up for. Everything else goes on a page you look at when you choose to.

In Fabric the destinations are: the pipeline's own failure path (an activity on
the red arrow, which can send mail or a Teams message), Activator rules on a
table, and the capacity's own notifications. The condition logic belongs in your
code, where it can be tested, and the destination belongs in the portal.

**Say:** "I keep the alert conditions in tested code and the delivery in the
portal, so I can prove the condition is right without a capacity."

## Concept: measuring what you promised

Three numbers a stakeholder will ask for, and where each comes from:

| Number | Where it comes from |
|---|---|
| Pipeline run time | The pipeline's own output, and the run log per step |
| Data refresh time | Direct Lake has no refresh, which is part of the point. The equivalent is the time from source files landing to gold published |
| Query response time | Power BI Performance Analyzer, per visual, per page |

Record them once as a baseline, and again after any change that should have made
things faster. A performance claim without a before and after is an opinion.

**Say:** "Direct Lake removes the refresh window entirely, so the number I quote
is landing to published. Query times I measure per page in Performance Analyzer,
and I keep the baseline."

## What Phase 6 will produce

* All Fabric items in one Git ready folder, including the notebooks and the
  semantic model, so a workspace can be synced from this repo.
* A run log table, a freshness table and an alert table, written by the pipeline.
* Alert conditions as tested code, with the portal wiring documented.
* A monitoring notebook that fails the run when a critical alert fires, so the
  pipeline goes red instead of quietly finishing.
* Deployment rules recorded as configuration, and the dev to prod steps written
  down.
* Measured timings: locally now, in Fabric when a capacity exists.
