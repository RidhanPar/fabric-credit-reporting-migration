"""Render the README figures from the committed run outputs.

Every figure here is drawn from a file in `data/` or `docs/results/` that a real
run produced. None of them is a screenshot of Fabric or Power BI: nothing has run
in a Fabric tenant, and an invented screenshot would contradict the README.

    py -3.11 scripts/build_figures.py

Output: docs/figures/*.png
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

ROOT = Path(__file__).parents[1]
OUT = ROOT / "docs" / "figures"

# Validated default palette, light surface (see the dataviz reference palette).
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]  # blue, orange, aqua, yellow
CRITICAL = "#d03b3b"
COUNTRY_COLOUR = {"PL": SERIES[0], "CZ": SERIES[1], "RO": SERIES[2]}
COUNTRY_NAME = {"PL": "Poland", "CZ": "Czech Republic", "RO": "Romania"}

plt.rcParams.update({
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "text.color": INK,
    "axes.labelcolor": INK_SECONDARY,
    "axes.edgecolor": AXIS,
    "axes.linewidth": 1.0,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "xtick.labelcolor": INK_SECONDARY,
    "ytick.labelcolor": INK_SECONDARY,
    "grid.color": GRID,
    "grid.linewidth": 1.0,
    "legend.frameon": False,
    "figure.dpi": 150,
})


def _style(ax, title: str, subtitle: str = "", ylabel: str = "") -> None:
    """Title above, subtitle under it, both left aligned and clear of the plot."""
    lines = textwrap.wrap(subtitle, width=96) if subtitle else []
    ax.set_title(title, color=INK, fontsize=14, fontweight="semibold", loc="left",
                 pad=12 + 17 * len(lines))
    if lines:
        ax.text(0, 1.012 + 0.052 * (len(lines) - 1), "\n".join(lines), transform=ax.transAxes,
                color=INK_SECONDARY, fontsize=10.5, va="bottom", linespacing=1.4)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=10.5)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(AXIS)
    ax.spines["bottom"].set_color(AXIS)
    ax.set_axisbelow(True)


def _millions(value, _pos) -> str:
    return f"{value / 1e6:.0f}m"


def _save(fig, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT / f"{name}.png", bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    print(f"docs/figures/{name}.png")


# Data ---------------------------------------------------------------------------------

def bridge() -> pd.DataFrame:
    return pd.read_csv(ROOT / "data" / "reconciliation" / "bridge.csv", parse_dates=["month_end"])


def attribution() -> pd.DataFrame:
    return pd.read_csv(ROOT / "data" / "reconciliation" / "attribution.csv", parse_dates=["month_end"])


def kpis() -> pd.DataFrame:
    return pd.read_csv(ROOT / "docs" / "results" / "gold_kpi_monthly.csv", parse_dates=["month_end"])


# Figures ------------------------------------------------------------------------------

def figure_reconciliation_bridge(bridge_df: pd.DataFrame) -> None:
    """The headline: how the legacy figure walks down to the new one."""
    last = bridge_df.month_end.max()
    row = bridge_df[(bridge_df.month_end == last) & (bridge_df.scope == "GROUP")
                    & (bridge_df.kpi == "portfolio_balance_eur")].iloc[0]

    steps = [("Legacy\npublished", row.legacy_published, "total")]
    for cause, label in (("F1", "F1 double\ncounted product"), ("F2", "F2 hardcoded\nRomania FX rate")):
        steps.append((label, row[f"delta_{cause}"], "down"))
    steps.append(("Legacy\ncorrected", row.legacy_corrected, "total"))
    # The definition changes move this KPI by 0.14 EUR, which cannot be drawn at this
    # scale, so the subtitle states it rather than showing a bar of no height.
    definition = row.gold_published - row.legacy_corrected
    steps.append(("Gold\npublished", row.gold_published, "total"))

    fig, ax = plt.subplots(figsize=(10, 5.6))
    running = 0.0
    for index, (label, value, kind) in enumerate(steps):
        if kind == "total":
            ax.bar(index, value, width=0.62, color=SERIES[0], zorder=3)
            ax.annotate(f"{value / 1e6:.2f}m", (index, value), textcoords="offset points", xytext=(0, 7),
                        ha="center", color=INK, fontsize=10.5, fontweight="semibold")
            running = value
        else:
            bottom = running + value
            ax.bar(index, -value, bottom=bottom, width=0.62, color=CRITICAL, zorder=3)
            shown = f"{value / 1e6:.2f}m" if abs(value) >= 5_000 else f"{value:,.0f}"
            ax.annotate(shown, (index, running), textcoords="offset points", xytext=(0, 7),
                        ha="center", color=CRITICAL, fontsize=10.5, fontweight="semibold")
            running = bottom

    ax.set_xticks(range(len(steps)))
    ax.set_xticklabels([label for label, _, _ in steps], fontsize=10)
    ax.yaxis.set_major_formatter(FuncFormatter(_millions))
    ax.grid(axis="y")
    ax.set_ylim(0, row.legacy_published * 1.12)
    _style(ax,
           "The legacy group portfolio balance, walked to the new one",
           f"{last:%B %Y}, EUR. Blue is a published figure, red a correction. Definition changes move "
           f"this KPI by only EUR {abs(definition):.2f}, too small to draw. Residual after every "
           "cause: 0.000000004 EUR.",
           "EUR")
    _save(fig, "reconciliation-bridge")


def figure_legacy_vs_gold(bridge_df: pd.DataFrame) -> None:
    """What the board saw, against what the data says, for all 24 months."""
    series = (bridge_df[(bridge_df.scope == "GROUP") & (bridge_df.kpi == "portfolio_balance_eur")]
              .sort_values("month_end"))
    fig, ax = plt.subplots(figsize=(10, 5.2))
    ax.plot(series.month_end, series.legacy_published, color=SERIES[1], linewidth=2,
            marker="o", markersize=4, markevery=[0, len(series) - 1], label="Legacy Excel pack")
    ax.plot(series.month_end, series.gold_published, color=SERIES[0], linewidth=2,
            marker="o", markersize=4, markevery=[0, len(series) - 1], label="New model (gold)")
    ax.fill_between(series.month_end, series.gold_published, series.legacy_published,
                    color=CRITICAL, alpha=0.10, zorder=1)

    final = series.iloc[-1]
    ax.annotate(f"{final.legacy_published / 1e6:.1f}m", (final.month_end, final.legacy_published),
                textcoords="offset points", xytext=(8, 2), color=SERIES[1], fontsize=10.5,
                fontweight="semibold")
    ax.annotate(f"{final.gold_published / 1e6:.1f}m", (final.month_end, final.gold_published),
                textcoords="offset points", xytext=(8, -4), color=SERIES[0], fontsize=10.5,
                fontweight="semibold")
    overstated = -series.total_difference.mean()
    share = (-series.total_difference / series.gold_published).mean() * 100

    ax.yaxis.set_major_formatter(FuncFormatter(_millions))
    ax.grid(axis="y")
    ax.margins(x=0.08)
    ax.legend(loc="upper left", fontsize=10.5, labelcolor=INK_SECONDARY)
    _style(ax,
           "The legacy pack was too high in every month",
           f"Group portfolio balance, EUR. Overstated by EUR {overstated:,.0f} on average "
           f"({share:.2f}%), in all 24 months.",
           "EUR")
    _save(fig, "legacy-vs-gold-balance")


def figure_arrears_by_country(kpi_df: pd.DataFrame) -> None:
    """Where the credit risk actually is, from the new model."""
    data = kpi_df[(kpi_df.kpi == "dpd30_rate") & (kpi_df.variant == "new")
                  & kpi_df.scope.isin(COUNTRY_COLOUR)]
    fig, ax = plt.subplots(figsize=(10, 5.2))
    for scope, part in data.groupby("scope"):
        part = part.sort_values("month_end")
        ax.plot(part.month_end, part.value * 100, color=COUNTRY_COLOUR[scope], linewidth=2,
                label=COUNTRY_NAME[scope])
        last = part.iloc[-1]
        ax.annotate(f"{last.value * 100:.1f}%", (last.month_end, last.value * 100),
                    textcoords="offset points", xytext=(8, -3), color=COUNTRY_COLOUR[scope],
                    fontsize=10.5, fontweight="semibold")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.grid(axis="y")
    ax.margins(x=0.1)
    ax.set_ylim(0, None)
    ax.legend(loc="upper left", fontsize=10.5, labelcolor=INK_SECONDARY)
    _style(ax,
           "30+ days past due rate, by country",
           "Balance 30 or more days past due over total active balance, at each month end. "
           "Romania and Poland trend up across the window, the Czech Republic down.",
           "Share of balance")
    _save(fig, "arrears-by-country")


def figure_difference_by_cause(attribution_df: pd.DataFrame, bridge_df: pd.DataFrame) -> None:
    """What explains the difference on the headline KPI."""
    balance = attribution_df[(attribution_df.kpi == "portfolio_balance_eur")
                             & (attribution_df.scope == "GROUP")]
    totals = balance.groupby(["cause_id", "classification"]).amount.sum().abs().reset_index()
    totals = totals.sort_values("amount")
    labels = {"F1": "F1  double counted product", "F2": "F2  hardcoded Romania FX rate",
              "D1": "D1  per account EUR rounding"}
    colours = [CRITICAL if row.classification == "LEGACY_ERROR" else SERIES[0]
               for row in totals.itertuples()]

    fig, ax = plt.subplots(figsize=(10, 3.6))
    positions = range(len(totals))
    ax.barh(list(positions), totals.amount, color=colours, height=0.55, zorder=3)
    for index, row in enumerate(totals.itertuples()):
        ax.annotate(f"EUR {row.amount:,.0f}", (row.amount, index), textcoords="offset points",
                    xytext=(8, 0), va="center", color=INK, fontsize=10.5, fontweight="semibold")
    ax.set_yticks(list(positions))
    ax.set_yticklabels([labels.get(row.cause_id, row.cause_id) for row in totals.itertuples()],
                       fontsize=10.5)
    ax.xaxis.set_major_formatter(FuncFormatter(_millions))
    ax.grid(axis="x")
    ax.set_xlim(0, totals.amount.max() * 1.28)
    legacy_total = balance[balance.classification == "LEGACY_ERROR"].amount.abs().sum()
    months = bridge_df[(bridge_df.scope == "GROUP")
                       & (bridge_df.kpi == "portfolio_balance_eur")].month_end.nunique()
    _style(ax,
           "Every difference on the group portfolio balance has a named cause",
           f"Each cause summed over the {months} months, EUR. Red is a legacy formula fault, "
           f"EUR {legacy_total:,.0f} between them. The one definition change on this KPI is EUR 3 of "
           "per account rounding, too small to see.",
           "")
    _save(fig, "difference-by-cause")


def figure_pipeline_step_times() -> None:
    """Where the five minutes goes."""
    run = json.loads((ROOT / "docs" / "results" / "phase6_local_run.json").read_text(encoding="utf-8"))
    labels = {"bronze": "Bronze", "silver": "Silver", "silver_quality": "Silver checks (43)",
              "gold": "Gold, with its 43 checks", "kpis": "KPI table",
              "kpi_quality": "KPI checks (10)", "monitor": "Monitoring"}
    steps = [(labels[name], payload["seconds"]) for name, payload in run["steps"].items()]
    steps.reverse()

    fig, ax = plt.subplots(figsize=(10, 4.0))
    positions = range(len(steps))
    ax.barh(list(positions), [seconds for _, seconds in steps], color=SERIES[0], height=0.55, zorder=3)
    for index, (_, seconds) in enumerate(steps):
        ax.annotate(f"{seconds:.1f} s", (seconds, index), textcoords="offset points", xytext=(8, 0),
                    va="center", color=INK, fontsize=10.5, fontweight="semibold")
    ax.set_yticks(list(positions))
    ax.set_yticklabels([label for label, _ in steps], fontsize=10.5)
    ax.grid(axis="x")
    ax.set_xlim(0, max(seconds for _, seconds in steps) * 1.2)
    total = sum(seconds for _, seconds in steps)
    _style(ax,
           "Where the pipeline spends its time",
           f"Local run, Spark 3.5.5 and Delta 3.2.1 on 2 cores. Total {total:.1f} s, "
           "of which the 96 quality checks are 78.7 s. Not a Fabric timing.",
           "")
    ax.set_xlabel("seconds", fontsize=10.5, color=INK_SECONDARY)
    _save(fig, "pipeline-step-times")


def main() -> None:
    bridge_df, attribution_df, kpi_df = bridge(), attribution(), kpis()
    figure_reconciliation_bridge(bridge_df)
    figure_legacy_vs_gold(bridge_df)
    figure_arrears_by_country(kpi_df)
    figure_difference_by_cause(attribution_df, bridge_df)
    figure_pipeline_step_times()


if __name__ == "__main__":
    main()
