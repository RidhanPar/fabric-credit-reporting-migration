"""Recalculate the legacy workbook's formulas and read off the published KPIs.

pycel compiles the Excel formulas in the file and evaluates them, the same way
Excel would on open. The figures returned here are therefore the figures the
workbook shows the business, produced by its own formulas and not by a Python
reimplementation of them.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from pycel import ExcelCompiler

from portfolio_migration import config as cfg
from portfolio_migration.legacy.workbook import FIRST_ROW, PUBLISHED_COUNTRY_KPIS, PUBLISHED_SUMMARY_KPIS


def _num(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    raise ValueError(f"cell evaluated to a non numeric value: {v!r}")


def published_kpis(path: Path) -> pd.DataFrame:
    """Long table: month_end, scope (country code or GROUP), kpi, value."""
    xl = ExcelCompiler(filename=str(path))
    rows = []
    for i, me in enumerate(cfg.reporting_months()):
        r = FIRST_ROW + i
        for c in cfg.COUNTRIES:
            for col, kpi in PUBLISHED_COUNTRY_KPIS.items():
                rows.append((me, c.code, kpi, _num(xl.evaluate(f"{c.code}!{col}{r}"))))
        for col, kpi in PUBLISHED_SUMMARY_KPIS.items():
            rows.append((me, "GROUP", kpi, _num(xl.evaluate(f"Summary!{col}{r}"))))
    return pd.DataFrame(rows, columns=["month_end", "scope", "kpi", "value"])
