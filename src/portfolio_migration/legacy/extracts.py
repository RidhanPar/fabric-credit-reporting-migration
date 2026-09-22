"""The three extracts the old DWH job sends to Finance every month.

These mirror the SQL of the legacy job (reproduced in the docstrings). Finance
pastes them into the workbook's data tabs. They are built from the core truth,
not from the landed files, because the old job read the core databases directly.
"""
from __future__ import annotations

import pandas as pd

from portfolio_migration import config as cfg
from portfolio_migration.generate.core import CoreData


def _bucket_frame() -> pd.DataFrame:
    return pd.DataFrame(cfg.DPD_BUCKETS, columns=["dpd_bucket", "dpd_min", "dpd_max"])


def balance_cube(core: CoreData) -> pd.DataFrame:
    """Month end balance cube.

    SELECT snapshot_date, country, product, dpd_bucket, dpd_min,
           COUNT(*) AS accounts, SUM(balance) AS balance_local
    FROM   core.account_snapshot
    WHERE  account_status = 'ACTIVE'
    GROUP  BY 1, 2, 3, 4, 5
    """
    b = core.balances[core.balances.account_status == "ACTIVE"].copy()
    b["dpd_bucket"] = b.days_past_due.map(cfg.dpd_bucket)
    cube = (b.groupby(["snapshot_date", "country_code", "product_code", "dpd_bucket"], as_index=False)
              .agg(accounts=("account_id", "size"), balance_local=("balance_local", "sum")))
    cube = cube.merge(_bucket_frame()[["dpd_bucket", "dpd_min"]], on="dpd_bucket")
    cube["balance_local"] = cube.balance_local.round(2)
    order = {label: i for i, (label, _, _) in enumerate(cfg.DPD_BUCKETS)}
    cube["_o"] = cube.dpd_bucket.map(order)
    cube = cube.sort_values(["snapshot_date", "country_code", "product_code", "_o"]).drop(columns="_o")
    return cube[["snapshot_date", "country_code", "product_code", "dpd_bucket", "dpd_min",
                 "accounts", "balance_local"]].reset_index(drop=True)


def originations(core: CoreData) -> pd.DataFrame:
    """New accounts by disbursement month.

    SELECT EOMONTH(open_date), country, product, COUNT(*), SUM(original_amount)
    FROM   core.account GROUP BY 1, 2, 3
    """
    a = core.accounts.copy()
    a["month_end"] = pd.to_datetime(a.open_date) + pd.offsets.MonthEnd(0)
    a["month_end"] = a.month_end.dt.date
    months = set(cfg.reporting_months())
    a = a[a.month_end.isin(months)]
    out = (a.groupby(["month_end", "country_code", "product_code"], as_index=False)
             .agg(new_accounts=("account_id", "size"), disbursed_local=("original_amount_local", "sum")))
    return out.sort_values(["month_end", "country_code", "product_code"]).reset_index(drop=True)


def applications(core: CoreData) -> pd.DataFrame:
    """Applications by application month and outcome, from the loan origination system."""
    a = core.applications.copy()
    a["month_end"] = (pd.to_datetime(a.application_date) + pd.offsets.MonthEnd(0)).dt.date
    a = a[a.month_end.isin(set(cfg.reporting_months()))]
    out = (a.pivot_table(index=["month_end", "country_code", "product_code"], columns="status",
                         values="application_id", aggfunc="count", fill_value=0)
             .reset_index())
    for s in ["APPROVED", "DECLINED", "WITHDRAWN", "INCOMPLETE"]:
        if s not in out:
            out[s] = 0
    out["received"] = out[["APPROVED", "DECLINED", "WITHDRAWN", "INCOMPLETE"]].sum(axis=1)
    out = out.rename(columns={"APPROVED": "approved", "DECLINED": "declined",
                              "WITHDRAWN": "withdrawn", "INCOMPLETE": "incomplete"})
    out.columns.name = None
    return out[["month_end", "country_code", "product_code", "received", "approved", "declined",
                "withdrawn", "incomplete"]].sort_values(["month_end", "country_code", "product_code"]).reset_index(drop=True)


def fx_table(core: CoreData) -> pd.DataFrame:
    """Treasury month end rates, wide, as Finance keeps them on the Lookups tab."""
    fx = core.fx_rates[core.fx_rates.month_end.isin(set(cfg.reporting_months()))]
    wide = fx.pivot(index="month_end", columns="currency", values="rate_to_eur_month_end").reset_index()
    wide.columns.name = None
    return wide[["month_end"] + [c.currency for c in cfg.COUNTRIES]]
