"""The check list is the contract, so it gets its own tests. No Spark session needed."""
import pytest

pytest.importorskip("pyspark")

from portfolio_migration.lakehouse import dq_checks, gold, quality  # noqa: E402

SILVER_TABLES = {"silver_products", "silver_fx_rates", "silver_customers", "silver_accounts", "silver_balances",
                 "silver_repayments", "silver_applications", "silver_application_events",
                 "silver_quarantine", "silver_control_totals", "silver_load_audit"}


def test_check_names_are_unique():
    names = [c.name for c in dq_checks.ALL_CHECKS]
    assert len(names) == len(set(names))


def test_every_check_is_well_formed():
    for c in dq_checks.ALL_CHECKS:
        assert c.severity in (quality.ERROR, quality.WARN), c.name
        assert c.layer in ("silver", "gold"), c.name
        assert c.description and c.kind, c.name


def test_checks_only_reference_tables_we_build():
    known = SILVER_TABLES | set(gold.GOLD_TABLES) | {"bronze_balances"}
    for c in dq_checks.ALL_CHECKS:
        assert c.table in known, (c.name, c.table)


def test_gold_checks_cover_every_published_table():
    covered = {c.table for c in dq_checks.GOLD_CHECKS}
    assert set(gold.GOLD_TABLES) <= covered


def test_the_six_core_check_types_plus_the_source_specific_ones_are_all_used():
    kinds = {c.kind for c in dq_checks.ALL_CHECKS}
    assert kinds == {"unique", "not_null", "referential_integrity", "accepted_values", "range",
                     "reconciliation", "control_total", "expression", "metric"}


def test_only_tolerance_style_checks_are_warnings():
    warnings = {c.name for c in dq_checks.ALL_CHECKS if c.severity == quality.WARN}
    assert warnings == {
        "silver_load_audit.quarantine_rate_balances",
        "silver_load_audit.quarantine_rate_repayments",
        "silver_load_audit.quarantine_rate_application_events",
        "silver_applications.no_undecided_applications_left",
    }


def test_every_grain_is_protected_by_a_uniqueness_check():
    unique_tables = {c.table for c in dq_checks.ALL_CHECKS if c.kind == "unique"}
    for fact in ("fact_balance_snapshot", "fact_origination", "fact_application", "fact_repayment"):
        assert fact in unique_tables
