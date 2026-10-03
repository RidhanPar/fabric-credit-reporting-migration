"""Diagnose a difference from its shape, before reading any formula.

This is how the faults were actually located. For each scope and KPI, the
difference between the legacy figure and the new one is profiled: when it starts,
whether it scales with the figure, whether it is a constant factor, whether only
the first month behaves differently. Each shape points at a kind of mistake.

The signatures here are computed from the data. They are the trail that justifies
which formula was opened first.
"""
from __future__ import annotations

import pandas as pd

from portfolio_migration.reconcile.bridge import KEYS, tolerance

CONSTANT_RATIO_SPREAD = 0.02   # the ratio varies by less than 2 percent across months
DRIFTING_RATIO_SPREAD = 0.10   # proportional every month, but the proportion moves slowly


def difference_profile(bridge: pd.DataFrame, legacy_col: str = "legacy_published",
                       gold_col: str = "gold_published") -> pd.DataFrame:
    """One row per scope and KPI describing the shape of the difference."""
    rows = []
    frame = bridge.assign(_legacy=bridge[legacy_col], _gold=bridge[gold_col])
    frame["_difference"] = frame._gold - frame._legacy
    for (scope, kpi), part in frame.groupby(["scope", "kpi"], sort=True):
        part = part.sort_values("month_end")
        tol = tolerance(kpi)
        differs = part._difference.abs() > tol
        months = part.month_end.dt.date.tolist()
        affected = [m for m, flag in zip(months, differs) if flag]
        affected_rows = part[differs]
        ratio_affected = (affected_rows._gold / affected_rows._legacy.replace(0, pd.NA)).dropna()
        spread = float(ratio_affected.max() - ratio_affected.min()) if len(ratio_affected) else 0.0
        rows.append({
            "scope": scope,
            "kpi": kpi,
            "months_affected": len(affected),
            "months_in_series": len(part),
            "first_affected_month": str(affected[0]) if affected else None,
            "first_affected_index": int(list(differs).index(True)) if affected else -1,
            "largest_difference": float(part._difference.abs().max()),
            "largest_relative_difference": float(ratio_affected.sub(1).abs().max()) if len(ratio_affected) else 0.0,
            "ratio_spread": spread,
            "ratio_always_same_side": bool(len(ratio_affected) > 2
                                           and ((ratio_affected > 1).all() or (ratio_affected < 1).all())),
            "legacy_zero_in_first_month": bool(abs(part._legacy.iloc[0]) <= tol
                                               and abs(part._gold.iloc[0]) > tol),
        })
    return pd.DataFrame(rows)


def signatures(profile: pd.DataFrame) -> pd.DataFrame:
    """Turn each profile into the hypotheses it suggests."""
    hints = []
    for row in profile.itertuples(index=False):
        if row.months_affected == 0:
            hints.append("matches, nothing to explain")
        else:
            notes = []
            if row.legacy_zero_in_first_month:
                notes.append("legacy reports zero in the first month of the series, which suggests the formula "
                             "reaches back one period")
            if row.months_affected == row.months_in_series and row.ratio_spread <= CONSTANT_RATIO_SPREAD:
                notes.append("every month differs by a near constant factor, which suggests a fixed factor or "
                             "rate applied in one place")
            elif (row.months_affected == row.months_in_series
                  and row.ratio_spread <= DRIFTING_RATIO_SPREAD and row.ratio_always_same_side):
                notes.append("every month differs in the same direction and in proportion to the figure, with "
                             "the proportion drifting slowly, which suggests a factor that was correct once "
                             "and has not been updated")
            if row.first_affected_index > 0 and not row.legacy_zero_in_first_month:
                notes.append(f"differences start in {row.first_affected_month} and continue, which suggests "
                             "something that began that month, such as a product launch or a changed extract")
            elif 0 < row.months_affected < row.months_in_series and not row.legacy_zero_in_first_month:
                notes.append("differs in most months but not all, with no clean start point")
            if row.largest_relative_difference < 1e-6:
                notes.append("difference is within rounding of the published figure")
            hints.append("; ".join(notes) or "differs with no obvious pattern, inspect the formula")
    return profile.assign(diagnosis=hints)


def diagnose(bridge: pd.DataFrame, legacy_col: str = "legacy_published",
             gold_col: str = "gold_published") -> pd.DataFrame:
    return signatures(difference_profile(bridge, legacy_col, gold_col))


def peeling_trail(bridge: pd.DataFrame, fixes) -> pd.DataFrame:
    """Diagnose, fix one fault, diagnose what is left, and so on.

    Round 0 is the difference as first reported. Each later round is the
    difference after that fault has been corrected in the workbook. The last
    round is the proof that nothing unexplained is left on the legacy definition.
    """
    rounds = [("0", "as first reported", "legacy_published")]
    for i, fix in enumerate(fixes, start=1):
        rounds.append((str(i), f"after {fix.id} was corrected", f"legacy_after_{fix.id}"))
    frames = []
    for number, label, column in rounds:
        part = diagnose(bridge, column, "gold_legacy_definition")
        frames.append(part.assign(round=number, round_label=label))
    out = pd.concat(frames, ignore_index=True)
    return out[["round", "round_label", "scope", "kpi", "months_affected", "first_affected_month",
                "largest_difference", "largest_relative_difference", "diagnosis"]]


def trail_highlights(trail: pd.DataFrame) -> pd.DataFrame:
    """One line per round and KPI: the worst affected scope, and how many scopes differ.

    The full trail is in peeling_trail.csv. This is the readable version.
    """
    differing = trail[trail.months_affected > 0]
    if differing.empty:
        return differing.assign(scopes_differing=0)
    worst = differing.loc[differing.groupby(["round", "kpi"]).largest_difference.idxmax()]
    counts = differing.groupby(["round", "kpi"]).scope.nunique().rename("scopes_differing")
    return worst.merge(counts, on=["round", "kpi"]).sort_values(["round", "kpi"])


def worst_cells(bridge: pd.DataFrame, limit: int = 10) -> pd.DataFrame:
    """The biggest differences, for the report's headline table."""
    out = bridge.assign(abs_difference=bridge.total_difference.abs())
    return (out.sort_values("abs_difference", ascending=False)
            .head(limit)[[*KEYS, "legacy_published", "gold_published", "total_difference"]])
