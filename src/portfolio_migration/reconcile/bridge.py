"""The reconciliation bridge: walk from each legacy figure to the new one in named steps.

For every month, scope and KPI:

    legacy published
      + the effect of each legacy fix, measured by recalculating the workbook
      = legacy corrected
      + residual against gold on the legacy definition   <- must be inside tolerance
      + the effect of each agreed definition change, measured on gold
      = gold published

Nothing is estimated. Each fix effect comes from the workbook's own recalculation
after that formula is corrected, and each definition effect comes from gold
computing both definitions.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from portfolio_migration import config as cfg
from portfolio_migration.lakehouse.kpis import VARIANT_CHAINS, VARIANT_STEPS
from portfolio_migration.legacy.evaluate import published_kpis
from portfolio_migration.reconcile.legacy_fixes import FIXES, LegacyFix, apply_fixes_cumulatively

LEGACY_ERROR = "LEGACY_ERROR"
DEFINITION_CHANGE = "DEFINITION_CHANGE"
NEW_MODEL_ERROR = "NEW_MODEL_ERROR"
UNEXPLAINED = "UNEXPLAINED"

PUBLISHED = "published"
KEYS = ["month_end", "scope", "kpi"]

# Tolerances. Amounts carry the legacy extract's 2 decimal rounding per product and
# bucket, so a few cents of difference on a multi million euro figure is rounding,
# not a finding. Rates and counts are compared far more tightly.
AMOUNT_TOLERANCE = 0.10
RATE_TOLERANCE = 1e-7
COUNT_TOLERANCE = 0.5
AMOUNT_KPIS = ("portfolio_balance_eur", "new_originations_eur", "avg_balance_per_customer_eur")
RATE_KPIS = ("dpd30_rate", "dpd90_rate", "approval_rate")
COUNT_KPIS = ("new_accounts",)

# Definition steps whose effect is the rounding of per row currency conversion
# rather than a change of meaning. Called out separately so the report can say so.
ROUNDING_STEPS = {
    ("portfolio_balance_eur", "legacy_def", "new"),
    ("new_originations_eur", "avg_rate", "new"),
    ("dpd30_rate", "legacy_def", "new"),
    ("dpd90_rate", "legacy_def", "new"),
    ("avg_balance_per_customer_eur", "per_customer", "new"),
}


@dataclass(frozen=True)
class DefinitionChange:
    id: str
    kpi: str
    from_variant: str
    to_variant: str
    description: str
    kind: str  # "definition" or "rounding"


def definition_changes() -> tuple[DefinitionChange, ...]:
    out = []
    for kpi, chain in VARIANT_CHAINS.items():
        for a, b in zip(chain, chain[1:]):
            key = (kpi, a, b)
            out.append(DefinitionChange(f"D{len(out) + 1}", kpi, a, b, VARIANT_STEPS[key],
                                        "rounding" if key in ROUNDING_STEPS else "definition"))
    return tuple(out)


DEFINITION_CHANGES = definition_changes()


def tolerance(kpi: str) -> float:
    if kpi in RATE_KPIS:
        return RATE_TOLERANCE
    if kpi in COUNT_KPIS:
        return COUNT_TOLERANCE
    return AMOUNT_TOLERANCE


def evaluate_legacy_stages(workbook: Path, workdir: Path,
                           fixes: tuple[LegacyFix, ...] = FIXES) -> dict[str, pd.DataFrame]:
    """Recalculate the workbook as found, then after each fix in turn."""
    stages = {PUBLISHED: published_kpis(workbook)}
    for fix_id, path in apply_fixes_cumulatively(workbook, fixes, workdir).items():
        stages[fix_id] = published_kpis(path)
    return stages


def _series(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    out = frame.copy()
    out["month_end"] = pd.to_datetime(out.month_end)
    return out.set_index(KEYS)["value"].rename(name).to_frame()


def build_bridge(legacy_stages: dict[str, pd.DataFrame], gold_variants: pd.DataFrame,
                 fixes: tuple[LegacyFix, ...] = FIXES) -> pd.DataFrame:
    """One row per month, scope and KPI, with a column per step of the walk."""
    bridge = _series(legacy_stages[PUBLISHED], "legacy_published")
    previous = "legacy_published"
    for fix in fixes:
        stage = _series(legacy_stages[fix.id], f"legacy_after_{fix.id}")
        bridge = bridge.join(stage, how="outer")
        bridge[f"delta_{fix.id}"] = bridge[f"legacy_after_{fix.id}"] - bridge[previous]
        previous = f"legacy_after_{fix.id}"
    bridge["legacy_corrected"] = bridge[previous]

    gold = gold_variants.copy()
    gold["month_end"] = pd.to_datetime(gold.month_end)
    wide = gold.pivot_table(index=KEYS, columns="variant", values="value")
    bridge = bridge.join(wide, how="left")

    bridge["gold_legacy_definition"] = bridge["legacy_def"]
    bridge["residual"] = bridge["gold_legacy_definition"] - bridge["legacy_corrected"]
    for change in DEFINITION_CHANGES:
        column = f"delta_{change.id}"
        rows = bridge.index.get_level_values("kpi") == change.kpi
        bridge[column] = 0.0
        bridge.loc[rows, column] = (bridge.loc[rows, change.to_variant]
                                    - bridge.loc[rows, change.from_variant]).fillna(0.0)

    bridge["gold_published"] = float("nan")
    for kpi, chain in VARIANT_CHAINS.items():
        rows = bridge.index.get_level_values("kpi") == kpi
        bridge.loc[rows, "gold_published"] = bridge.loc[rows, chain[-1]]
    bridge["total_difference"] = bridge["gold_published"] - bridge["legacy_published"]
    bridge["tolerance"] = [tolerance(kpi) for _, _, kpi in bridge.index]
    bridge["unexplained"] = bridge["residual"].where(bridge["residual"].abs() > bridge["tolerance"], 0.0)
    return bridge.reset_index()


def attribution(bridge: pd.DataFrame, fixes: tuple[LegacyFix, ...] = FIXES) -> pd.DataFrame:
    """Long table: every difference, split into named causes. Zero sized causes are dropped."""
    rows = []
    for fix in fixes:
        part = bridge[[*KEYS, f"delta_{fix.id}", "tolerance"]].rename(columns={f"delta_{fix.id}": "amount"})
        part = part.assign(cause_id=fix.id, cause=fix.title, classification=LEGACY_ERROR, kind="legacy_formula")
        rows.append(part)
    for change in DEFINITION_CHANGES:
        part = bridge[[*KEYS, f"delta_{change.id}", "tolerance"]].rename(columns={f"delta_{change.id}": "amount"})
        part = part.assign(cause_id=change.id, cause=change.description,
                           classification=DEFINITION_CHANGE, kind=change.kind)
        rows.append(part)
    residual = bridge[[*KEYS, "residual", "tolerance"]].rename(columns={"residual": "amount"})
    residual = residual.assign(cause_id="U", cause="not explained by any known cause",
                               classification=UNEXPLAINED, kind="residual")
    rows.append(residual)
    out = pd.concat(rows, ignore_index=True)
    out = out[out.amount.abs() > out.tolerance]
    return out.sort_values([*KEYS, "cause_id"]).reset_index(drop=True)


def check_bridge_closes(bridge: pd.DataFrame) -> pd.DataFrame:
    """Rows where the steps do not add up to the total difference. Should be empty."""
    steps = [c for c in bridge.columns if c.startswith("delta_")] + ["residual"]
    walked = bridge[steps].sum(axis=1)
    gap = (bridge["total_difference"] - walked).abs()
    return bridge.assign(step_sum=walked, closing_gap=gap)[gap > bridge["tolerance"]]


def unexplained(bridge: pd.DataFrame) -> pd.DataFrame:
    return bridge[bridge["unexplained"] != 0.0]


def findings_summary(bridge: pd.DataFrame, attributions: pd.DataFrame,
                     fixes: tuple[LegacyFix, ...] = FIXES) -> pd.DataFrame:
    """One row per cause: how big, where, over which months."""
    rows = []
    known = [(f.id, f.title, LEGACY_ERROR, "legacy_formula") for f in fixes]
    known += [(d.id, d.description, DEFINITION_CHANGE, d.kind) for d in DEFINITION_CHANGES]
    for cause_id, cause, classification, kind in known:
        part = attributions[attributions.cause_id == cause_id]
        if part.empty:
            rows.append({"cause_id": cause_id, "cause": cause, "classification": classification, "kind": kind,
                         "kpis": "", "scopes": "", "cells_affected": 0, "first_month": None,
                         "largest_single_difference": 0.0, "largest_relative_difference": 0.0})
            continue
        merged = part.merge(bridge[[*KEYS, "legacy_published"]], on=KEYS, how="left")
        relative = (merged.amount.abs() / merged.legacy_published.abs().replace(0, pd.NA)).dropna()
        rows.append({
            "cause_id": cause_id,
            "cause": cause,
            "classification": classification,
            "kind": kind,
            "kpis": ", ".join(sorted(part.kpi.unique())),
            "scopes": ", ".join(sorted(part.scope.unique())),
            "cells_affected": len(part),
            "first_month": str(pd.to_datetime(part.month_end).min().date()),
            "largest_single_difference": float(part.amount.abs().max()),
            "largest_relative_difference": float(relative.max()) if len(relative) else 0.0,
        })
    return pd.DataFrame(rows)


def reporting_months() -> list[pd.Timestamp]:
    return [pd.Timestamp(m) for m in cfg.reporting_months()]


def restrict_to_window(frame: pd.DataFrame) -> pd.DataFrame:
    months = set(reporting_months())
    out = frame.copy()
    out["month_end"] = pd.to_datetime(out.month_end)
    return out[out.month_end.isin(months)]
