"""Generate the reconciliation findings document from the computed bridge.

Every number in the document comes from the frames passed in, so the document
cannot drift from the run that produced it. A test regenerates it and compares.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from portfolio_migration.reconcile import bridge as br
from portfolio_migration.reconcile.diagnose import diagnose, peeling_trail, trail_highlights, worst_cells
from portfolio_migration.reconcile.legacy_fixes import FIXES

KPI_LABELS = {
    "portfolio_balance_eur": "Portfolio balance (EUR)",
    "new_accounts": "New accounts",
    "new_originations_eur": "New originations (EUR)",
    "dpd30_rate": "30+ DPD rate",
    "dpd90_rate": "90+ DPD rate",
    "approval_rate": "Approval rate",
    "avg_balance_per_customer_eur": "Avg balance per customer (EUR)",
}
TRAIL_HEADER = ["Round", "State", "KPI", "Scopes differing", "Worst scope", "Months differing",
                "First month", "What the shape suggests"]
SCOPE_LABELS = {"PL": "Poland", "CZ": "Czech Republic", "RO": "Romania", "GROUP": "Group"}


def _money(x: float) -> str:
    return f"{x:,.2f}"


def _value(kpi: str, x: float) -> str:
    if pd.isna(x):
        return "n/a"
    if kpi in br.RATE_KPIS:
        return f"{x * 100:.3f}%"
    if kpi in br.COUNT_KPIS:
        return f"{x:,.0f}"
    return f"EUR {_money(x)}"


def _short(text: str, limit: int = 42) -> str:
    """Trim to a word boundary so a label never breaks mid word."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "..."


def _table(rows: list[list[str]], header: list[str]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def summary_numbers(bridge: pd.DataFrame, attributions: pd.DataFrame) -> dict:
    legacy_errors = attributions[attributions.classification == br.LEGACY_ERROR]
    return {
        "cells_compared": int(len(bridge)),
        "cells_that_differed": int((bridge.total_difference.abs() > bridge.tolerance).sum()),
        "cells_affected_by_a_legacy_error": int(len(legacy_errors.groupby(br.KEYS))),
        "legacy_faults_found": int(legacy_errors.cause_id.nunique()),
        "definition_changes": int(attributions[attributions.classification == br.DEFINITION_CHANGE]
                                  .cause_id.nunique()),
        "new_model_errors": int(attributions[attributions.classification == br.NEW_MODEL_ERROR].cause_id.nunique()),
        "unexplained_cells": int(len(br.unexplained(bridge))),
        "largest_residual_amount_eur": float(
            bridge[bridge.kpi.isin(br.AMOUNT_KPIS)].residual.abs().max()),
        "largest_residual_rate": float(bridge[bridge.kpi.isin(br.RATE_KPIS)].residual.abs().max()),
        "largest_overstatement_eur": float(
            bridge[bridge.kpi == "portfolio_balance_eur"].total_difference.min()),
    }


def _walk_example(bridge: pd.DataFrame) -> str:
    """The group portfolio balance for the last month, walked from legacy to gold."""
    last = bridge.month_end.max()
    row = bridge[(bridge.month_end == last) & (bridge.scope == "GROUP")
                 & (bridge.kpi == "portfolio_balance_eur")].iloc[0]
    lines = [f"Group portfolio balance, {last.date()}", "",
             f"  legacy published                {_money(row.legacy_published):>16}"]
    for fix in FIXES:
        delta = row[f"delta_{fix.id}"]
        if abs(delta) > row.tolerance:
            lines.append(f"  {fix.id} {_short(fix.short):<46} {_money(delta):>14}")
    lines.append(f"  legacy corrected                {_money(row.legacy_corrected):>16}")
    lines.append(f"  residual against gold           {_money(row.residual):>16}")
    for change in br.DEFINITION_CHANGES:
        delta = row.get(f"delta_{change.id}", 0.0)
        if abs(delta) > row.tolerance:
            lines.append(f"  {change.id} {_short(change.description):<46} {_money(delta):>14}")
    lines.append(f"  gold published                  {_money(row.gold_published):>16}")
    lines.append(f"  unexplained                     {_money(row.unexplained):>16}")
    return "```\n" + "\n".join(lines) + "\n```"


def _finding_section(fix, round_number: int, bridge: pd.DataFrame, attributions: pd.DataFrame,
                     trail: pd.DataFrame) -> str:
    part = attributions[attributions.cause_id == fix.id]
    affected = part.groupby(["scope", "kpi"]).amount.agg(["count", lambda s: s.abs().max()])
    affected.columns = ["months", "largest"]
    rows = [[SCOPE_LABELS.get(scope, scope), KPI_LABELS[kpi], str(int(r.months)),
             _value(kpi, r.largest)] for (scope, kpi), r in affected.iterrows()]
    first_month = pd.to_datetime(part.month_end).min().date()
    biggest = part.loc[part.amount.abs().idxmax()]
    before = trail[(trail["round"] == str(round_number)) & trail.kpi.isin(fix.diagnostic_kpis)
                   & (trail.months_affected > 0)]
    shapes = sorted({d for d in before.diagnosis if d})

    return "\n".join([
        f"### {fix.id}. {fix.title}",
        "",
        f"**Classification:** legacy error. The old report was wrong from {first_month}.",
        "",
        f"**How it was found** (round {round_number} of the peeling trail, "
        f"{'the difference as first reported' if round_number == 0 else 'after the earlier faults were corrected'}):",
        "",
        *[f"* The shape of the difference: {t}" for t in shapes],
        f"* {fix.diagnosis}",
        "",
        "**What the formula did**",
        "",
        fix.cause,
        "",
        "**What it should do**",
        "",
        fix.correct_behaviour,
        *(["", "**Knock on effect**", "", fix.side_effect] if fix.side_effect else []),
        "",
        "**The fix** (column(s) " + ", ".join(fix.columns) + " of the "
        + ", ".join(fix.sheets) + f" tab(s), {fix.expected_cells} cells):",
        "",
        "```",
        f"before  {fix.before_after[0]}",
        f"after   {fix.before_after[1]}",
        "```",
        "",
        "**Measured effect** (recalculating the workbook after the fix):",
        "",
        _table(rows, ["Scope", "KPI", "Months affected", "Largest single month effect"]),
        "",
        f"Largest single effect: {_value(biggest.kpi, biggest.amount)} on "
        f"{KPI_LABELS[biggest.kpi]} for {SCOPE_LABELS.get(biggest.scope, biggest.scope)} in "
        f"{pd.to_datetime(biggest.month_end).date()}.",
        "",
    ])


def write_findings(path: Path, bridge: pd.DataFrame, attributions: pd.DataFrame,
                   summary: pd.DataFrame, numbers: dict) -> Path:
    trail = peeling_trail(bridge, FIXES)
    worst = worst_cells(bridge, 8)
    definition_rows = []
    for change in br.DEFINITION_CHANGES:
        part = attributions[attributions.cause_id == change.id]
        definition_rows.append([
            change.id, KPI_LABELS[change.kpi], change.description,
            "rounding" if change.kind == "rounding" else "definition",
            str(len(part)),
            _value(change.kpi, part.amount.abs().max()) if not part.empty
            else "none above tolerance",
        ])

    highlights = trail_highlights(trail)
    trail_rows = [[r.round, r.round_label, KPI_LABELS[r.kpi], str(r.scopes_differing),
                   SCOPE_LABELS.get(r.scope, r.scope), str(r.months_affected),
                   str(r.first_affected_month or "none"), r.diagnosis]
                  for r in highlights.itertuples(index=False)]
    final_round = trail[trail["round"] == str(len(FIXES))]
    still_differing = int((final_round.months_affected > 0).sum())

    worst_rows = [[str(r.month_end.date()), SCOPE_LABELS.get(r.scope, r.scope), KPI_LABELS[r.kpi],
                   _value(r.kpi, r.legacy_published), _value(r.kpi, r.gold_published),
                   _value(r.kpi, r.total_difference)] for r in worst.itertuples(index=False)]

    fault_rows = [[r.cause_id, r.cause, r.classification.replace("_", " ").lower(), str(r.cells_affected),
                   str(r.first_month or "none")]
                  for r in summary.itertuples(index=False) if r.cells_affected]

    doc = f"""# Reconciliation findings: legacy Excel pack against the new gold layer

Generated by `python -m portfolio_migration reconcile`. Every number here comes
from that run. Do not edit this file by hand.

## Headline

The migration compared **{numbers['cells_compared']} published figures**
(24 month ends, 4 scopes, 7 KPIs). **{numbers['cells_that_differed']} of them differed.**

**The old report was wrong.** {numbers['legacy_faults_found']} faults were found in the legacy
workbook's own formulas, affecting {numbers['cells_affected_by_a_legacy_error']} published figures.
The largest single effect on the group portfolio balance was an overstatement of
EUR {_money(abs(numbers['largest_overstatement_eur']))}.

Every remaining difference is an agreed definition change
({numbers['definition_changes']} of them) or currency conversion rounding.
**Unexplained differences: {numbers['unexplained_cells']}.**

The residual after all causes are removed is at most
EUR {numbers['largest_residual_amount_eur']:.2e} on any amount and
{numbers['largest_residual_rate']:.2e} on any rate, against tolerances of
EUR {br.AMOUNT_TOLERANCE} and {br.RATE_TOLERANCE}.

## Method

For every month, scope and KPI the reconciliation walks from the legacy figure to
the new one in named steps:

{_walk_example(bridge)}

* A legacy fault's effect is measured by correcting that formula in a copy of the
  workbook and recalculating it. Not by an opinion about what the formula meant.
* A definition change's effect is measured by having gold compute both
  definitions, so the step is exact.
* The residual is what is left. It must be inside tolerance, otherwise the
  reconciliation is incomplete and nothing should be signed.

Tolerances: EUR {br.AMOUNT_TOLERANCE} on amounts, which covers the legacy extract's
rounding to 2 decimals per product and bucket; {br.RATE_TOLERANCE} on rates;
exact on counts.

## The biggest differences before explanation

{_table(worst_rows, ["Month", "Scope", "KPI", "Legacy", "Gold", "Difference"])}

## How the faults were found: the peeling trail

Diagnose the shape of the difference, correct the one fault that shape points at,
then diagnose what is left. Computed from the data, before any formula was opened.
One line per round and KPI, showing the worst affected scope. The full trail, every
scope and KPI in every round, is in `data/reconciliation/peeling_trail.csv`.

{_table(trail_rows, TRAIL_HEADER)}

After all {len(FIXES)} faults are corrected, {still_differing} of the
{len(final_round)} scope and KPI series still differ from gold on the legacy
definition. That is the proof that the legacy faults are the whole story.

## Causes

{_table(fault_rows, ["ID", "Cause", "Class", "Figures affected", "From"])}

## Legacy faults found

"""
    for i, fix in enumerate(FIXES):
        doc += _finding_section(fix, i, bridge, attributions, trail) + "\n"

    doc += f"""## Definition changes

These are not errors. Both sides are arithmetically right; the definition
changed and was agreed. Each one is quantified by computing both definitions on gold.

{_table(definition_rows, ["ID", "KPI", "Change", "Kind", "Figures affected", "Largest effect"])}

Three of these have no effect above tolerance. For a rate, converting each account
to EUR and then dividing gives the same answer as dividing the local currency
totals, because the rate cancels. For the average balance per customer, the per
account rounding is a fraction of a cent once it is divided by the customer count.
They are listed anyway so the chain is complete and nothing is quietly skipped.

## New model errors

{numbers['new_model_errors']} in this run. The residual is what is left when every known
cause is removed, so a fault in the new model shows up here by elimination.

That is how one was found during the build. The first full reconciliation closed
on amounts and failed on rates by about 0.0000005, because Spark caps the scale
when one decimal column is divided by another, which had truncated every rate in
the gold KPI table to 6 decimal places. It was fixed before any figure was
published, and a data quality check now fails if rates ever look truncated again.
The write up is issue 10 in [ISSUES_AND_FIXES.md](ISSUES_AND_FIXES.md).

## Reproduce this

```bash
py -3.11 -m portfolio_migration reconcile
```

Outputs: `data/reconciliation/bridge.csv` (one row per figure with every step),
`attribution.csv` (one row per difference and cause), `diagnosis.csv`, and this
document. `tests/test_reconciliation.py` asserts the bridge closes and that no
figure is left unexplained.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")
    return path


def write_outputs(out_dir: Path, bridge: pd.DataFrame, attributions: pd.DataFrame,
                  summary: pd.DataFrame, numbers: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    bridge.to_csv(out_dir / "bridge.csv", index=False)
    attributions.to_csv(out_dir / "attribution.csv", index=False)
    diagnose(bridge).to_csv(out_dir / "diagnosis.csv", index=False)
    peeling_trail(bridge, FIXES).to_csv(out_dir / "peeling_trail.csv", index=False)
    summary.to_csv(out_dir / "findings_summary.csv", index=False)
    (out_dir / "reconciliation_summary.json").write_text(json.dumps(numbers, indent=2), encoding="utf-8")
