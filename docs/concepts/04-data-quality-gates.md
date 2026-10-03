# 04. Data quality gates (read before Phase 3)

## The problem a gate solves

Monday morning. The pipeline ran at 04:00. The board pack is at 09:00. One
country's balance file arrived truncated, so the portfolio is 8 percent light.
Nobody notices until the meeting.

Two ways to live:

* **Monitor:** publish, then check, then send an apology.
* **Gate:** check, then publish only if the checks pass. Yesterday's numbers stay
  on screen with yesterday's date, which is far less damaging than today's wrong ones.

**Say:** "A stale number with a date on it is recoverable. A wrong number with
today's date on it is not."

## Concept: write, audit, publish

The pattern has a name. Write the new data to **staging** tables, **audit** it
there, and only **publish** to the tables the report reads if the audit passes.

**Case:** the gold build runs, writes `stg_fact_balance_snapshot`, and the
referential check finds 40 accounts missing from `dim_account`. Publishing is
abandoned. `fact_balance_snapshot` still holds last night's good data, the
report still works, and the pipeline fails loudly.

Here the swap is a plain overwrite from staging, which at 225 thousand rows costs
seconds. At 50 million rows you would swap with a metadata operation instead,
such as a table rename or a view pointing at the newest good version.

**Say:** "Gold is never written directly. It is written to staging, audited, then
published. The report cannot see data that failed a check."

## Concept: severity

Not every failure should stop the pack.

| Severity | Meaning | Example |
|---|---|---|
| ERROR | Stops the publish | Duplicate account and month in the fact table |
| WARN | Recorded, publish continues | 0.4 percent of repayment rows quarantined, within the agreed tolerance |

**Case:** a credit card account closes mid month and its last snapshot carries a
null product code. One row. If that stopped the board pack every month, the team
would start switching checks off. Severity is what keeps a gate credible.

**Say:** "I set severity per check with the business, so the gate stops the
things that make the report wrong and only warns on the things that make it imperfect."

## The six check types every warehouse needs

| Check | Question | Our example |
|---|---|---|
| Uniqueness | Is the grain what I claimed? | One row per account per month end |
| Not null | Are the keys and measures present? | `balance_eur` is never null |
| Referential integrity | Does every key resolve? | Every `account_id` in a fact exists in `dim_account` |
| Accepted values | Only the codes we agreed? | `account_status` in ACTIVE, CLOSED, WRITTEN_OFF |
| Range | Are the numbers possible? | `days_past_due` between 0 and 179 |
| Reconciliation | Did we lose or invent anything between layers? | Gold row count and balance total equal silver's |

Plus two that come from this project's sources:

* **Control totals.** Each balance file's trailer says how many rows and what
  they sum to. Compare the parsed rows against it. This is the one check that
  catches a *parsing* bug: if the Czech decimal comma were parsed wrong, the row
  count would still match and the sum would not.
* **Quarantine rate.** Quarantining 9 rows is housekeeping. Quarantining 9,000
  means the feed changed shape. Same check, a threshold decides.

**Say:** "Row counts alone are not a reconciliation. A locale bug keeps the count
and moves the money, so I reconcile the amounts too."

## Why checks are declared, not coded

Every check in this project is one line in a list: type, table, columns,
severity. The engine knows how to run each type. That matters because the
business can read the list, and because adding a rule cannot introduce a bug in
the engine.

**Say:** "Checks are declarative. A reviewer reads the list, not the code."

## The results table

Every run appends to `dq_results`: batch id, layer, table, check name, type,
severity, rows checked, rows failed, pass or fail, a sample of what failed, and
how long the check took. That table is what you report on, and it is also the
evidence trail when someone asks why the pack was late.
