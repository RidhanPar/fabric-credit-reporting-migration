"""Every number in the README has to come from a committed run output.

The rule for this project is that no figure appears in the documentation unless a
real run produced it. That rule is only worth anything if something enforces it,
so this test recomputes the headline numbers from the committed outputs and fails
if the README has drifted from them.

It also checks that every relative link resolves and that no screenshot is
referenced that does not exist, because a README that implies evidence it does
not have is worse than one that admits the gap.
"""
import ast
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
DOCS = sorted((ROOT / "docs").rglob("*.md"))


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _count_test_functions(folder: Path, recurse: bool) -> int:
    paths = folder.rglob("test_*.py") if recurse else folder.glob("test_*.py")
    total = 0
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        total += sum(1 for node in ast.walk(tree)
                     if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"))
    return total


def test_the_generated_data_figures_match_the_manifest():
    manifest = json.loads((ROOT / "data" / "generation_manifest.json").read_text(encoding="utf-8"))
    rows = manifest["truth_rows"]
    for table in ("customers", "applications", "accounts", "balances", "repayments"):
        assert f"{rows[table]:,}" in README, f"{table} row count is stale in the README"
    assert f"{manifest['landing']['files']} landed files" in README
    defects = manifest["landing"]["defects"]
    for key in ("resent_balance_file_rows", "duplicate_repayment_rows", "blank_account_id",
                "padded_lowercase_product_code", "pending_event_before_final", "truncated_json_line"):
        assert f"{defects[key]:,}" in README, f"{key} count is stale in the README"


def test_the_reconciliation_figures_match_the_committed_summary():
    summary = json.loads((ROOT / "data" / "reconciliation" / "reconciliation_summary.json")
                         .read_text(encoding="utf-8"))
    assert f"{summary['cells_compared']} published figures" in README
    assert f"{summary['cells_that_differed']} differed" in README
    assert f"{summary['cells_affected_by_a_legacy_error']} of those {summary['cells_compared']}" in README
    assert summary["legacy_faults_found"] == 3, "the README says three faults"
    assert summary["unexplained_cells"] == 0
    assert "Nothing was left unexplained" in README
    assert _money(abs(summary["largest_overstatement_eur"])) in README


def test_the_fault_effects_match_the_attribution_table():
    attribution = pd.read_csv(ROOT / "data" / "reconciliation" / "attribution.csv")
    for cause_id in ("F1", "F2"):
        largest = attribution[attribution.cause_id == cause_id].amount.abs().max()
        assert _money(largest) in README, f"{cause_id}'s largest effect is stale in the README"
    dpd = attribution[attribution.cause_id == "F3"].amount.abs().max()
    assert f"{dpd * 100:.2f} percentage points" in README


def test_the_portfolio_overstatement_matches_the_bridge():
    bridge = pd.read_csv(ROOT / "data" / "reconciliation" / "bridge.csv")
    balance = bridge[(bridge.kpi == "portfolio_balance_eur") & (bridge.scope == "GROUP")]
    assert (balance.total_difference < 0).all(), "the README claims every month was overstated"
    assert _money(-balance.total_difference.mean()) in README
    assert _money(-balance.total_difference.min()) in README
    assert _money(-balance.total_difference.max()) in README
    share = (-balance.total_difference / balance.gold_published).mean() * 100
    assert f"{share:.2f}%" in README

    dpd = bridge[(bridge.kpi == "dpd30_rate") & (bridge.scope == "GROUP")]
    assert f"{dpd.legacy_published.mean() * 100:.3f}%" in README
    assert f"{dpd.gold_published.mean() * 100:.3f}%" in README


def test_the_timings_match_the_committed_run():
    run = json.loads((ROOT / "docs" / "results" / "phase6_local_run.json").read_text(encoding="utf-8"))
    steps = {name: payload["seconds"] for name, payload in run["steps"].items()}
    for seconds in steps.values():
        assert f"| {seconds} |" in README, f"a step timing of {seconds} is not in the README table"
    assert f"| **{round(sum(steps.values()), 1)}** |" in README

    audit = {entity["entity"]: entity for entity in run["steps"]["silver"]["entities"]}
    balances = audit["balances"]
    for field in ("bronze_rows", "control_rows", "duplicate_rows", "silver_rows"):
        assert f"{balances[field]:,}" in README, f"balances {field} is stale in the README"

    checks = sum(run["steps"][step]["checks"] for step in ("silver_quality", "gold", "kpi_quality"))
    assert f"{checks} checks" in README
    check_seconds = sum(result["seconds"]
                        for step in ("silver_quality", "gold", "kpi_quality")
                        for result in run["steps"][step]["check_results"])
    assert f"{round(check_seconds, 1)} seconds" in README


def test_the_query_benchmark_figures_match():
    bench = json.loads((ROOT / "docs" / "results" / "phase6_query_benchmark.json")
                       .read_text(encoding="utf-8"))
    medians = [q["median_ms"] for q in bench["queries"].values()]
    assert f"{min(medians):,.0f} ms to {max(medians):,.0f} ms" in README
    exec_query = bench["queries"]["exec_balance_by_month"]
    assert f"{exec_query['first_run_ms']:,.0f} ms cold to {exec_query['best_ms']:,.0f} ms warm" in README


def test_the_check_and_model_counts_match_the_code():
    import pytest

    pytest.importorskip("pyspark")
    from portfolio_migration import semantic_model as sm
    from portfolio_migration.lakehouse import dq_checks

    errors = [c for c in dq_checks.ALL_CHECKS if c.severity == "ERROR"]
    warnings = [c for c in dq_checks.ALL_CHECKS if c.severity == "WARN"]
    assert f"{len(dq_checks.ALL_CHECKS)} checks: {len(dq_checks.SILVER_CHECKS)} on silver" in README
    assert f"{len(errors)} stop the publish" in README
    assert f"{len(warnings)} only warn" in README
    assert f"{len(sm.measures())} measures" in README


def test_the_test_counts_match_the_suite():
    local = _count_test_functions(ROOT / "tests", recurse=False)
    spark = _count_test_functions(ROOT / "tests" / "spark", recurse=False)
    assert f"{local + spark} tests" in README
    assert f"{local} tests with no Spark" in README
    assert f"{spark} tests on a real Spark" in README


def test_every_relative_link_resolves():
    pattern = re.compile(r"\[[^\]]+\]\((?!https?://)([^)#]+)")
    for path in [ROOT / "README.md", *DOCS]:
        base = path.parent
        for target in pattern.findall(path.read_text(encoding="utf-8")):
            assert (base / target).exists(), f"{path.name} links to missing {target}"


def test_no_screenshot_is_referenced_that_does_not_exist():
    """A README that implies evidence it does not have is worse than one that admits the gap."""
    embedded = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
    for path in [ROOT / "README.md", *DOCS]:
        for target in embedded.findall(path.read_text(encoding="utf-8")):
            if not target.startswith("http"):
                assert (path.parent / target).exists(), f"{path.name} embeds missing image {target}"
    existing = list((ROOT / "docs" / "screenshots").glob("*.png"))
    if not existing:
        assert "There are no screenshots" in README, (
            "while docs/screenshots is empty the README must say so")


def test_figures_are_labelled_as_figures_not_screenshots():
    """The charts come from committed run outputs. Nothing may imply a Fabric screenshot."""
    figures = sorted((ROOT / "docs" / "figures").glob("*.png"))
    assert figures, "no figures were generated"
    for figure in figures:
        assert f"docs/figures/{figure.name}" in README, f"{figure.name} is not used in the README"
    assert "figures, not screenshots" in README
    assert "scripts/build_figures.py" in README


def test_the_runbook_covers_what_a_team_would_need():
    runbook = (ROOT / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")
    for heading in ("## Roles", "## The monthly close", "## The parallel run",
                    "## Retiring the Excel pack", "## Failure playbooks", "## Changing something"):
        assert heading in runbook, f"the runbook has no {heading} section"
    for playbook in ("A pipeline step failed", "The reconciliation does not close",
                     "Data is stale but every run is green"):
        assert playbook in runbook, f"the runbook has no playbook for: {playbook}"


def test_the_teardown_stops_the_money_first_and_protects_the_evidence():
    teardown = (ROOT / "docs" / "TEARDOWN.md").read_text(encoding="utf-8")
    assert teardown.index("Pause") < teardown.index("Delete the workspace")
    for keep in ("data/legacy/", "docs/results/", "docs/screenshots/"):
        assert keep in teardown, f"the teardown does not say what happens to {keep}"


def test_the_readme_states_where_it_has_and_has_not_run():
    assert "Nothing has run in a Fabric tenant yet" in README
    assert "The data is synthetic" in README
    assert "What a real enterprise migration would add" in README
