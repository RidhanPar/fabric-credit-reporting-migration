# 06. The semantic model (read before Phase 5)

The lakehouse is not the deliverable. A model the business can ask questions of is.

## Concept: the semantic model

**What:** the layer that turns tables into a business vocabulary. Relationships,
measures with agreed definitions, formatting, hierarchies, security. Power BI
reports, Excel pivot tables and Copilot all read the same model, so they all
agree.

**Case:** three analysts each build their own "30+ DPD rate" in their own file.
Three numbers reach the board. One semantic model means one definition of that
measure, maintained in one place, and it is the thing that stops the Excel
problem coming back through the back door.

**Say:** "The migration is only finished when there is one definition of each KPI
that everyone reads, not one spreadsheet per analyst."

## Concept: Direct Lake

Power BI has three storage modes. The choice matters and interviewers ask.

| Mode | How it reads data | Cost |
|---|---|---|
| Import | Copies data into the model, compressed in memory | Fast queries, but a refresh to schedule and a second copy of the data |
| DirectQuery | Sends SQL to the source per visual | No copy, no refresh, but every click waits on the source |
| **Direct Lake** | Reads the Delta Parquet files in OneLake directly into the engine's memory | Import style speed with no refresh and no copy |

**Case:** the pipeline finishes at 05:40. With import, a refresh then has to run
and can fail on its own. With Direct Lake the report is current the moment gold
is published, because the model reads the same files.

Direct Lake has rules worth knowing:

* It needs a Fabric capacity. Pro alone cannot do it.
* It reads Delta tables in OneLake, so the gold tables must be the published
  ones and not views over staging.
* If a query exceeds the capacity's guardrails, it can **fall back** to
  DirectQuery over the SQL endpoint, which is slower. Fallback behaviour is a
  model setting, and keeping tables narrow and typed is how you avoid it.
* Calculated columns in DAX are not supported in Direct Lake tables. Do that
  work upstream in gold, which is where it belongs anyway.

**Say:** "Direct Lake gives import speed without a refresh step, because the
model reads the same Delta files the pipeline just wrote. The price is that the
modelling discipline moves upstream into gold."

## Concept: PBIP and TMDL, a report as text

**What:** a Power BI Project saves the model as **TMDL** files, plain text, one
file per table, instead of one binary `.pbix`. The report pages are JSON.

**Case:** two people change the model in the same week. With a `.pbix` you get
"their version or mine". With TMDL you get a diff, a review and a merge, like
any other code. You can also see that someone changed the 30+ DPD measure, in a
pull request, with the old and the new formula side by side.

**Say:** "A .pbix is a binary blob. A PBIP with TMDL is reviewable, so a measure
change goes through a pull request like a code change."

## Concept: measures, and writing a definition down

A measure is not just DAX. It is a sentence the business signed off, with the DAX
underneath it. Our 30+ DPD rate, for example:

* **Business definition:** the balance of active accounts 30 or more days past
  due, divided by the total balance of active accounts, at a month end.
* **Why it is a balance rate and not an account count rate:** it is the money at
  risk that matters for impairment, not how many accounts.
* **DAX:** a division of two sums over the balance fact, filtered to month ends.

The documentation of a measure belongs next to the measure (TMDL supports
descriptions), so it travels with the model rather than living in someone's head.

## Concept: row level security

**What:** rules on the model that filter data by who is asking. In Fabric it can
be defined in the semantic model (roles plus a DAX filter) or in the lakehouse.

**Case:** the Poland country manager opens the group report and sees Poland only,
with no separate file and no separate report. Group risk sees all three countries.
One report, three audiences.

Practical points for the interview:

* RLS filters a dimension (`dim_country`), and the facts follow the relationship.
* It is tested by viewing the report **as** a role, which is a feature of the
  service, and that test is part of release, not an afterthought.
* RLS is not a security boundary for the lakehouse itself: someone with workspace
  access can read the tables underneath. Sensitive data needs the permission set
  further down as well.

**Say:** "RLS on the country dimension, tested by viewing as the role. I would
not call it a control on its own, because lakehouse access sits underneath it."

## Concept: report design that answers a question

Three pages, each with a job:

| Page | Question it answers | Who reads it |
|---|---|---|
| Executive summary | Is the book growing, and is credit quality holding? | The board, once a month |
| Delinquency | Where are the arrears, by country, product and vintage? | Credit risk, weekly |
| Account detail (drillthrough) | Which accounts are behind this number? | Collections and audit, daily |

**Drillthrough** is the feature that makes the third page work: right click a
figure, land on an account level page already filtered to that context. It is
also the answer to the question every migration gets asked: "can I still get to
the account list, like I could in the old spreadsheet?"

**Say:** "Every page answers one question for one audience. If I cannot say whose
question a page answers, it should not exist."

## What Phase 5 will produce

* A semantic model as a PBIP with TMDL text files, in this repo.
* DAX measures for the seven board KPIs, plus the new definitions agreed in
  Phase 4, each with its business definition written next to it.
* Row level security by country, with a documented test.
* A report specification for the three pages, with the visuals and the fields
  each one uses.
* Tests over the TMDL: every measure documented, every KPI present, relationships
  single direction, and no calculated columns that Direct Lake cannot support.
