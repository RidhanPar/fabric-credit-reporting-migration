"""Write the raw files that land in OneLake, exactly as the source systems deliver them.

Each country runs its own core banking instance, so the three countries export
in different local formats. Real extracts also carry transport defects. The ones
injected here are the kinds a lakehouse has to handle every day:

* CZ files use ';' delimiters and ',' decimals; RO files use dd/mm/yyyy dates.
* Balance files end with a trailer record (TRL, record count, balance control total).
* One balance file was delivered twice (a *_resend* file).
* Some repayment rows are repeated (the extract batch was retried).
* Some balance rows have no account id (suspense placeholder rows from the export).
* Some PL product codes arrive padded with spaces and in lower case.
* The loan origination system sends nested JSON events. Some applications have an
  earlier PENDING event before the final one, and some lines are truncated copies
  of a record that was resent in full.

None of the defects changes the truth: every defective row is either a repeat of
a real row, recoverable by cleaning, or a junk row that does not exist in the core
databases. Silver must remove or repair all of them without losing real data.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from portfolio_migration import config as cfg
from portfolio_migration.generate.core import CoreData

RESENT_FILE = ("RO", date(2026, 3, 31))
REPAYMENT_DUPLICATE_SHARE = 0.002
BLANK_ACCOUNT_ROWS_PER_COUNTRY = 3
PADDED_PRODUCT_SHARE = 0.01
PENDING_EVENT_SHARE = 0.03
TRUNCATED_LINE_SHARE = 0.001


@dataclass
class LandingManifest:
    """What was injected, so tests can prove silver found all of it."""
    files: int = 0
    rows: dict[str, int] = field(default_factory=dict)
    defects: dict[str, int] = field(default_factory=dict)

    def add(self, key: str, n: int = 1) -> None:
        self.defects[key] = self.defects.get(key, 0) + n


def _fmt_dates(df: pd.DataFrame, cols: list[str], fmt: str) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        out[c] = pd.to_datetime(out[c]).dt.strftime(fmt).where(out[c].notna(), "")
    return out


def _fmt_number(x: float, decimal: str) -> str:
    s = f"{x:.2f}"
    return s.replace(".", ",") if decimal == "," else s


def _write_csv(df: pd.DataFrame, path: Path, country: cfg.Country, trailer: tuple[int, float] | None = None) -> None:
    sep = ";" if country.csv_decimal == "," else ","
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, sep=sep, decimal=country.csv_decimal, float_format="%.2f", lineterminator="\n")
    if trailer is not None:
        count, total = trailer
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"TRL{sep}{count}{sep}{_fmt_number(total, country.csv_decimal)}\n")


def _yyyymm(d: date) -> str:
    return f"{d.year}{d.month:02d}"


def _core_banking(core: CoreData, root: Path, rng: np.random.Generator, m: LandingManifest) -> None:
    extract_date = cfg.reporting_months()[-1]
    for c in cfg.COUNTRIES:
        base = root / "core_banking" / c.code
        fmt = c.csv_date_format

        cust = core.customers[core.customers.country_code == c.code]
        cust = _fmt_dates(cust, ["date_of_birth", "customer_since"], fmt)
        _write_csv(cust, base / "customers" / f"customers_{c.code}_{extract_date:%Y%m%d}.csv", c)
        m.files += 1
        m.rows["customers"] = m.rows.get("customers", 0) + len(cust)

        acc = core.accounts[core.accounts.country_code == c.code].copy()
        acc["term_months"] = acc.term_months.astype("Int64")
        # Rates need 4 decimals; the file wide float format is 2 decimals for money.
        acc["annual_interest_rate"] = acc.annual_interest_rate.map(lambda x: f"{x:.4f}".replace(".", c.csv_decimal))
        acc = _fmt_dates(acc, ["open_date", "close_date", "write_off_date"], fmt)
        _write_csv(acc, base / "accounts" / f"accounts_{c.code}_{extract_date:%Y%m%d}.csv", c)
        m.files += 1
        m.rows["accounts"] = m.rows.get("accounts", 0) + len(acc)

        bal_c = core.balances[core.balances.country_code == c.code]
        blank_months = set(rng.choice(len(cfg.reporting_months()), size=BLANK_ACCOUNT_ROWS_PER_COUNTRY, replace=False))
        for i, me in enumerate(cfg.reporting_months()):
            b = bal_c[bal_c.snapshot_date == me].copy()
            if c.code == "PL":
                pad = rng.random(len(b)) < PADDED_PRODUCT_SHARE
                b.loc[pad, "product_code"] = "  " + b.loc[pad, "product_code"].str.lower() + " "
                m.add("padded_lowercase_product_code", int(pad.sum()))
            if i in blank_months:
                junk = b.sample(1, random_state=int(rng.integers(1_000_000))).copy()
                junk["account_id"] = ""
                junk["balance_local"] = round(float(rng.uniform(10, 500)), 2)
                junk["days_past_due"] = 0
                b = pd.concat([b, junk], ignore_index=True)
                m.add("blank_account_id", 1)
            b = _fmt_dates(b, ["snapshot_date"], fmt)
            trailer = (len(b), float(b.balance_local.sum()))
            path = base / "balances" / f"balances_{c.code}_{_yyyymm(me)}.csv"
            _write_csv(b, path, c, trailer)
            m.files += 1
            m.rows["balances"] = m.rows.get("balances", 0) + len(b)
            if (c.code, me) == RESENT_FILE:
                _write_csv(b, path.with_name(path.stem + "_resend.csv"), c, trailer)
                m.files += 1
                m.rows["balances"] += len(b)
                m.add("resent_balance_file_rows", len(b))

        pay_c = core.repayments[core.repayments.account_id.str.startswith(c.code)]
        months = pd.to_datetime(pay_c.payment_date) + pd.offsets.MonthEnd(0)
        for me in cfg.reporting_months():
            p = pay_c[months.dt.date == me]
            dup = p[rng.random(len(p)) < REPAYMENT_DUPLICATE_SHARE]
            m.add("duplicate_repayment_rows", len(dup))
            p = pd.concat([p, dup]).sort_values("payment_id")
            p = _fmt_dates(p, ["payment_date"], fmt)
            _write_csv(p, base / "repayments" / f"repayments_{c.code}_{_yyyymm(me)}.csv", c)
            m.files += 1
            m.rows["repayments"] = m.rows.get("repayments", 0) + len(p)


def _app_event(rec, status: str, event_ts: datetime) -> dict:
    return {
        "application_id": rec.application_id,
        "submitted_at": f"{rec.application_date:%Y-%m-%d}T{9 + int(rec.application_id[3:]) % 9:02d}:15:00Z",
        "channel": rec.channel,
        "applicant": {"customer_id": rec.customer_id, "country": rec.country_code, "risk_grade": rec.risk_grade},
        "product": {"code": rec.product_code, "requested_amount": rec.requested_amount_local, "currency": rec.currency},
        "decision": {"status": status,
                     "decided_on": None if status in ("PENDING", "WITHDRAWN", "INCOMPLETE") or pd.isna(rec.decision_date)
                     else f"{rec.decision_date:%Y-%m-%d}"},
        "event_ts": event_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _loan_origination_system(core: CoreData, root: Path, rng: np.random.Generator, m: LandingManifest) -> None:
    apps = core.applications.copy()
    apps["month_end"] = (pd.to_datetime(apps.application_date) + pd.offsets.MonthEnd(0)).dt.date
    for me, grp in apps.groupby("month_end", sort=True):
        lines = []
        for rec in grp.itertuples(index=False):
            final_day = rec.decision_date if not pd.isna(rec.decision_date) else rec.application_date + timedelta(days=5)
            final = _app_event(rec, rec.status, datetime.combine(final_day, time(18, 0)))
            if rec.status in ("APPROVED", "DECLINED") and rng.random() < PENDING_EVENT_SHARE:
                lines.append(json.dumps(_app_event(rec, "PENDING", datetime.combine(rec.application_date, time(9, 30)))))
                m.add("pending_event_before_final", 1)
            if rng.random() < TRUNCATED_LINE_SHARE:
                full = json.dumps(final)
                lines.append(full[: len(full) // 2])
                m.add("truncated_json_line", 1)
            lines.append(json.dumps(final))
        path = root / "los" / "applications" / f"applications_{_yyyymm(me)}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        m.files += 1
        m.rows["application_lines"] = m.rows.get("application_lines", 0) + len(lines)


def write_landing(core: CoreData, root: Path, seed: int = cfg.SEED) -> LandingManifest:
    rng = np.random.default_rng(seed + 1)
    m = LandingManifest()

    ref = root / "reference" / "products" / "products.csv"
    ref.parent.mkdir(parents=True, exist_ok=True)
    core.products.to_csv(ref, index=False, lineterminator="\n")
    fx = root / "treasury" / "fx_rates" / f"fx_rates_{cfg.reporting_months()[-1]:%Y%m%d}.csv"
    fx.parent.mkdir(parents=True, exist_ok=True)
    core.fx_rates.to_csv(fx, index=False, lineterminator="\n")
    m.files += 2
    m.rows["products"], m.rows["fx_rates"] = len(core.products), len(core.fx_rates)

    _core_banking(core, root, rng, m)
    _loan_origination_system(core, root, rng, m)
    return m
