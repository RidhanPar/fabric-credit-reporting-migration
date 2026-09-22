"""The simulated core systems must behave like a real loan book."""
import pandas as pd

from portfolio_migration import config as cfg
from portfolio_migration.generate.core import simulate


def test_calendar():
    months = cfg.reporting_months()
    assert len(months) == 24
    assert str(months[0]) == "2024-09-30" and str(months[-1]) == "2026-08-31"
    assert len(cfg.simulation_calendar()) == cfg.BURN_IN_MONTHS + 24


def test_dpd_buckets_cover_every_reportable_day():
    assert [cfg.dpd_bucket(d) for d in (0, 1, 29, 30, 59, 60, 89, 90, 179)] == [
        "Current", "1-29", "1-29", "30-59", "30-59", "60-89", "60-89", "90-179", "90-179"]


def test_snapshots_are_unique_and_inside_the_window(core):
    b = core.balances
    assert not b.duplicated(["account_id", "snapshot_date"]).any()
    assert set(b.snapshot_date) == set(cfg.reporting_months())


def test_balances_are_valid(core):
    b = core.balances
    assert (b.balance_local >= 0).all()
    active = b[b.account_status == "ACTIVE"]
    assert active.days_past_due.between(0, cfg.WRITE_OFF_DPD - 1).all()
    ended = b[b.account_status != "ACTIVE"]
    assert (ended.balance_local == 0).all()


def test_accounts_trace_back_to_approved_applications(core):
    apps = core.applications.set_index("application_id")
    acc = core.accounts
    assert acc.account_id.is_unique
    assert (apps.loc[acc.application_id, "status"] == "APPROVED").all()
    decided = pd.to_datetime(apps.loc[acc.application_id, "decision_date"].values)
    assert (pd.to_datetime(acc.open_date).values >= decided.values).all()
    assert set(core.balances.account_id) <= set(acc.account_id)
    assert set(acc.customer_id) <= set(core.customers.customer_id)


def test_written_off_accounts_carry_a_write_off(core):
    wo = core.accounts[core.accounts.write_off_date.notna()]
    assert len(wo) > 0
    assert (wo.write_off_amount_local >= 0).all()


def test_undecided_applications_have_no_decision_date(core):
    a = core.applications
    undecided = a[a.status.isin(["WITHDRAWN", "INCOMPLETE"])]
    assert undecided.decision_date.isna().all()
    assert a[a.status.isin(["APPROVED", "DECLINED"])].decision_date.notna().all()


def test_consolidation_loan_only_sold_after_launch(core):
    launch = cfg.simulation_calendar()[cfg.PRODUCT_BY_CODE["CC_CONSOL"].launch_month_index].replace(day=1)
    consol = core.applications[core.applications.product_code == "CC_CONSOL"]
    assert len(consol) > 0
    assert (consol.application_date >= launch).all()


def test_repayments_are_positive_and_unique(core):
    r = core.repayments
    assert r.payment_id.is_unique
    assert (r.amount_local > 0).all()


def test_simulation_is_deterministic(core):
    again = simulate()
    pd.testing.assert_frame_equal(core.balances, again.balances)
    pd.testing.assert_frame_equal(core.applications, again.applications)
