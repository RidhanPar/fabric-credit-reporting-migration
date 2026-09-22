# 01. The legacy world

## The situation

LendCo is a consumer lender in Poland, the Czech Republic and Romania. It sells
personal loans and credit cards. Each month Group Finance builds the
**Monthly Portfolio Pack** in Excel and sends it to the board.

How the pack gets made today:

1. The DWH team runs an old SQL job and emails Finance three extracts: a balance
   cube, originations and applications.
2. An analyst pastes them into the workbook's `Data_` tabs.
3. Each country analyst maintains their own country tab of formulas.
4. The Summary tab rolls the countries up to group level in EUR.
5. Summary is pasted as values into the board slides.

Nobody can say for sure the numbers are right. That is the problem we are paid to fix.

## Concept: one truth, two derivations

The generator first simulates what really happened in the core systems. That is
the **truth**. Two things are derived from it:

| Derived from truth | Who uses it | Path |
|---|---|---|
| Raw extract files, with real transport defects | The new Fabric lakehouse | `data/landing/` |
| Aggregated DWH extracts pasted into Excel | The legacy workbook | `data/legacy/` |

**Why this matters in an interview:** because both sides come from the same
truth, every difference between old and new *must* have a cause. If the
reconciliation leaves anything unexplained, one of the two sides is wrong. That
is the same logic as a real parallel run, where old and new read the same source
systems for a few months before the old report is switched off.

**Example scenario:** a card issuer runs its old and new arrears reports in
parallel for three month ends and the new one comes out lower. Sign off only
happens once every difference is attributed to a named cause.

## Concept: the landing zone and control totals

Source systems drop files; they do not write to your tables. Every core banking
balance file ends with a trailer record:

```
TRL;3859;233745819,47
```

That says "this file has 3,859 records and the balances sum to 233,745,819.47".
It is a **control total**. When a file is truncated in transit, the count stops
matching. Banks have used trailers since mainframe days and they are still the
cheapest completeness check there is. Phase 3 checks every file against its trailer.

## What the landed files contain (defects a lakehouse must handle)

Counts come from `data/generation_manifest.json`, seed 42.

| Defect | Count | Where | Real world cause |
|---|---|---|---|
| `;` delimiter and `,` decimals | all CZ files | core_banking/CZ | Czech locale export |
| `dd/mm/yyyy` dates | all RO files | core_banking/RO | Romanian locale export |
| Balance file delivered twice | 2,869 rows | RO March 2026 `_resend` | Ops resent after a timeout |
| Repeated repayment rows | 376 | repayments | Extract batch retried |
| Blank account id rows | 9 | balances | Suspense placeholder rows |
| Padded, lower case product code | 1,009 | PL balances | Free text field in PL core |
| Earlier PENDING event before the final one | 969 | LOS JSON | Event stream, not a table |
| Truncated JSON line | 37 | LOS JSON | Message cut then resent |

None of these changes the truth. Silver must repair or remove every one of them
without losing a single real row. The tests in `tests/test_landing_files.py`
prove the real rows are all still there.

## The workbook

`data/legacy/Monthly_Portfolio_Pack.xlsx`. Every KPI cell is a live formula.
The published figures come from recalculating those formulas with pycel, a
Python Excel formula engine, so the numbers are what Excel itself would show.

KPIs on each country tab and on Summary:

| KPI | Legacy formula in words |
|---|---|
| Portfolio balance (EUR) | Sum of active balances, converted to EUR |
| New accounts | Count of accounts opened in the month |
| New originations (EUR) | Amount disbursed in the month, in EUR |
| 30+ DPD rate | Balance 30 or more days past due / total balance |
| 90+ DPD rate | Balance 90 or more days past due / total balance |
| Approval rate | Approved / applications received |
| Avg balance per customer (EUR) | Portfolio balance / active count |

Three errors are seeded in this workbook. They are not described here on purpose.

**Optional challenge:** open the workbook in Excel and try to find them before
Phase 4. Hint: look at formulas, not values.
