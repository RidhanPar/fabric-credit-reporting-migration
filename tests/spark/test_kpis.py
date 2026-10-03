"""The monthly KPI table feeds both the reconciliation and the semantic model."""
import pandas as pd

from portfolio_migration import config as cfg
from portfolio_migration.lakehouse.kpis import KPI_TABLE, VARIANT_CHAINS


def _kpis(lake) -> pd.DataFrame:
    months = {pd.Timestamp(m) for m in cfg.reporting_months()}
    df = lake.read(KPI_TABLE).toPandas()
    df["month_end"] = pd.to_datetime(df.month_end)
    return df[df.month_end.isin(months)]


def test_every_kpi_and_variant_covers_every_month_and_scope(lake_run):
    lake, _, _ = lake_run
    df = _kpis(lake)
    scopes = {c.code for c in cfg.COUNTRIES} | {"GROUP"}
    for kpi, chain in VARIANT_CHAINS.items():
        for variant in chain:
            part = df[(df.kpi == kpi) & (df.variant == variant)]
            assert set(part.scope) == scopes, (kpi, variant)
            assert len(part) == len(scopes) * cfg.WINDOW_MONTHS, (kpi, variant, len(part))


def test_group_is_the_sum_of_the_three_countries(lake_run):
    lake, _, _ = lake_run
    df = _kpis(lake)
    for kpi in ("portfolio_balance_eur", "new_originations_eur", "new_accounts"):
        part = df[(df.kpi == kpi) & (df.variant == "legacy_def")]
        countries = part[part.scope != "GROUP"].groupby("month_end").value.sum()
        group = part[part.scope == "GROUP"].set_index("month_end").value
        assert ((countries - group).abs() < 0.05).all(), kpi


def test_rates_keep_their_precision(lake_run):
    """Spark caps the scale when dividing decimals, which once truncated every rate to 6 decimals."""
    lake, _, _ = lake_run
    rates = _kpis(lake)
    rates = rates[rates.kpi.str.endswith("_rate") & (rates.value != 0)]
    on_six_decimals = (rates.value.round(6) == rates.value).mean()
    assert on_six_decimals < 0.5, f"{on_six_decimals:.1%} of rates sit exactly on 6 decimals"


def test_rates_are_between_zero_and_one(lake_run):
    lake, _, _ = lake_run
    rates = _kpis(lake)
    rates = rates[rates.kpi.str.endswith("_rate")]
    assert rates.value.between(0, 1).all()


def test_the_definition_changes_actually_change_something(lake_run):
    """If a variant made no difference it would not be worth having in the chain."""
    lake, _, _ = lake_run
    df = _kpis(lake)
    wide = df.pivot_table(index=["month_end", "scope", "kpi"], columns="variant", values="value")
    approval = wide.xs("approval_rate", level="kpi")
    assert (approval["new"] > approval["legacy_def"]).all()
    avg = wide.xs("avg_balance_per_customer_eur", level="kpi")
    assert (avg["per_customer"] > avg["legacy_def"]).all()
    orig = wide.xs("new_originations_eur", level="kpi")
    assert (orig["avg_rate"] != orig["legacy_def"]).all()
