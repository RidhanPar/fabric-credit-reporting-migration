"""The reconciliation must explain every difference, and keep explaining it.

These tests run the real reconciliation: the committed legacy workbook is
recalculated as found and after each fix, and compared against the committed
gold KPI table. No Spark needed.
"""
from pathlib import Path

import pandas as pd
import pytest

from portfolio_migration.reconcile import bridge as br
from portfolio_migration.reconcile import report
from portfolio_migration.reconcile.diagnose import peeling_trail
from portfolio_migration.reconcile.legacy_fixes import FIX_BY_ID, FIXES, FixDidNotApply, apply_fix

ROOT = Path(__file__).parents[1]
WORKBOOK = ROOT / "data" / "legacy" / "Monthly_Portfolio_Pack.xlsx"
GOLD_KPIS = ROOT / "docs" / "results" / "gold_kpi_monthly.csv"
FINDINGS = ROOT / "docs" / "RECONCILIATION_FINDINGS.md"


@pytest.fixture(scope="session")
def reconciliation(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("recon")
    stages = br.evaluate_legacy_stages(WORKBOOK, workdir)
    gold = br.restrict_to_window(pd.read_csv(GOLD_KPIS, parse_dates=["month_end"]))
    bridge = br.build_bridge(stages, gold)
    attributions = br.attribution(bridge)
    summary = br.findings_summary(bridge, attributions)
    numbers = report.summary_numbers(bridge, attributions)
    return bridge, attributions, summary, numbers


def test_every_published_figure_is_compared(reconciliation):
    bridge, _, _, numbers = reconciliation
    assert numbers["cells_compared"] == 24 * 4 * 7 == 672
    assert bridge.legacy_published.notna().all()
    assert bridge.gold_published.notna().all()


def test_the_bridge_closes_for_every_figure(reconciliation):
    bridge, _, _, _ = reconciliation
    not_closing = br.check_bridge_closes(bridge)
    assert not_closing.empty, not_closing[["month_end", "scope", "kpi", "closing_gap"]].head().to_string()


def test_nothing_is_left_unexplained(reconciliation):
    bridge, _, _, numbers = reconciliation
    left = br.unexplained(bridge)
    assert left.empty, left[["month_end", "scope", "kpi", "residual"]].head().to_string()
    assert numbers["unexplained_cells"] == 0
    assert numbers["largest_residual_amount_eur"] < br.AMOUNT_TOLERANCE
    assert numbers["largest_residual_rate"] < br.RATE_TOLERANCE


def test_three_legacy_faults_were_found_and_all_three_bite(reconciliation):
    _, attributions, summary, numbers = reconciliation
    assert numbers["legacy_faults_found"] == len(FIXES) == 3
    for fix in FIXES:
        rows = attributions[attributions.cause_id == fix.id]
        assert len(rows) > 0, fix.id
        assert set(rows.kpi) <= set(fix.kpis), fix.id
    found = summary[summary.classification == br.LEGACY_ERROR]
    assert (found.cells_affected > 0).all()


def test_the_legacy_report_overstated_the_portfolio(reconciliation):
    bridge, _, _, _ = reconciliation
    balance = bridge[(bridge.kpi == "portfolio_balance_eur") & (bridge.scope == "GROUP")]
    # Gold is lower than legacy in every month: the old pack was too high throughout.
    assert (balance.total_difference < 0).all()


def test_each_fix_changes_exactly_the_cells_it_claims(tmp_path):
    for fix in FIXES:
        changed = apply_fix(WORKBOOK, fix, tmp_path / f"{fix.id}.xlsx")
        assert changed == fix.expected_cells, fix.id


def test_a_fix_that_no_longer_matches_fails_loudly(tmp_path):
    once = tmp_path / "once.xlsx"
    apply_fix(WORKBOOK, FIX_BY_ID["F3"], once)
    with pytest.raises(FixDidNotApply):
        apply_fix(once, FIX_BY_ID["F3"], tmp_path / "twice.xlsx")


def test_every_difference_has_at_least_one_named_cause(reconciliation):
    bridge, attributions, _, _ = reconciliation
    differing = bridge[bridge.total_difference.abs() > bridge.tolerance]
    named = attributions.groupby(br.KEYS).size()
    for row in differing.itertuples(index=False):
        assert (row.month_end, row.scope, row.kpi) in named.index, (row.scope, row.kpi, row.month_end)


def test_causes_are_only_the_three_allowed_classes(reconciliation):
    _, attributions, _, _ = reconciliation
    assert set(attributions.classification) <= {br.LEGACY_ERROR, br.DEFINITION_CHANGE, br.NEW_MODEL_ERROR}


def test_the_peeling_trail_ends_clean(reconciliation):
    bridge, _, _, _ = reconciliation
    trail = peeling_trail(bridge, FIXES)
    first_round = trail[trail["round"] == "0"]
    last_round = trail[trail["round"] == str(len(FIXES))]
    assert (first_round.months_affected > 0).sum() > 0
    assert (last_round.months_affected == 0).all(), last_round[last_round.months_affected > 0].to_string()


def test_the_shapes_point_at_the_right_faults(reconciliation):
    """The signatures are what justified opening each formula."""
    bridge, _, _, _ = reconciliation
    trail = peeling_trail(bridge, FIXES)

    def hint(round_number, scope, kpi):
        row = trail[(trail["round"] == round_number) & (trail.scope == scope) & (trail.kpi == kpi)]
        return row.diagnosis.iloc[0]

    assert "began that month" in hint("0", "PL", "portfolio_balance_eur")
    assert "reaches back one period" in hint("0", "PL", "dpd30_rate")
    # Once the double count is gone, Romania's remaining gap is a drifting proportion.
    assert "has not been updated" in hint("1", "RO", "portfolio_balance_eur")


def test_the_findings_document_is_up_to_date(reconciliation, tmp_path):
    bridge, attributions, summary, numbers = reconciliation
    numbers = dict(numbers)
    regenerated = report.write_findings(tmp_path / "findings.md", bridge, attributions, summary, numbers)
    assert regenerated.read_text(encoding="utf-8") == FINDINGS.read_text(encoding="utf-8"), (
        "docs/RECONCILIATION_FINDINGS.md is stale. Rerun: py -3.11 -m portfolio_migration reconcile")
