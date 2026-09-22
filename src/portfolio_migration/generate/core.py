"""Simulate the lender's core systems month by month.

The output of this module is the *truth*: what really happened to every
application, account, balance and payment. Two things are derived from it later:

* the raw files that land in OneLake (with the defects real extracts carry), and
* the aggregated extracts the old DWH job feeds into the legacy Excel workbook.

Because both come from the same truth, any difference between the legacy report
and the new gold layer must be explainable. That is the property Phase 4 proves.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from portfolio_migration import config as cfg

GRADES = np.array(["A", "B", "C", "D", "E"])
GRADE_MIX = np.array([0.15, 0.30, 0.30, 0.17, 0.08])
GRADE_APPROVAL_FACTOR = {"A": 1.45, "B": 1.20, "C": 1.00, "D": 0.70, "E": 0.35}
# Monthly probability that a performing account misses its payment.
GRADE_MISS_PROB = {"A": 0.004, "B": 0.009, "C": 0.018, "D": 0.035, "E": 0.070}
SEASONALITY = {1: 0.85, 2: 0.90, 3: 1.00, 4: 1.00, 5: 1.02, 6: 1.00,
               7: 0.95, 8: 0.95, 9: 1.03, 10: 1.05, 11: 1.10, 12: 1.20}
MONTHLY_GROWTH = 1.008
# Share of applications that never reach a credit decision.
WITHDRAWN_SHARE = 0.06
INCOMPLETE_SHARE = 0.04
TAKE_UP_RATE = 0.94
EXISTING_CUSTOMER_SHARE = 0.12
CHANNELS = np.array(["ONLINE", "BRANCH", "BROKER"])
CHANNEL_MIX = np.array([0.62, 0.23, 0.15])
PAYMENT_CHANNELS = np.array(["DIRECT_DEBIT", "BANK_TRANSFER", "CARD"])
PAYMENT_CHANNEL_MIX = np.array([0.60, 0.25, 0.15])


@dataclass
class CoreData:
    products: pd.DataFrame
    fx_rates: pd.DataFrame
    customers: pd.DataFrame
    applications: pd.DataFrame
    accounts: pd.DataFrame
    balances: pd.DataFrame      # month end snapshots, reporting window only
    repayments: pd.DataFrame    # payment transactions, reporting window only


def _macro_factor(country: str, t: int, n: int) -> float:
    """Credit cycle per country. Romania deteriorates through the window."""
    x = t / max(n - 1, 1)
    return {"PL": 1.0, "CZ": 1.05 - 0.15 * x, "RO": 0.9 + 0.8 * x ** 2}[country]


def simulate_fx(rng: np.random.Generator, cal: list[date]) -> pd.DataFrame:
    """Daily log random walk per currency, summarised to month end and monthly average."""
    rows = []
    for c in cfg.COUNTRIES:
        rate = c.fx_start
        daily_vol = c.fx_monthly_vol / np.sqrt(21)
        daily_drift = c.fx_drift / 21
        for me in cal:
            days = pd.bdate_range(me.replace(day=1), me)
            path = []
            for _ in days:
                rate *= float(np.exp(daily_drift + daily_vol * rng.standard_normal()))
                path.append(rate)
            rows.append({
                "month_end": me,
                "currency": c.currency,
                "rate_to_eur_month_end": round(path[-1], 6),
                "rate_to_eur_month_avg": round(float(np.mean(path)), 6),
            })
    return pd.DataFrame(rows)


def _products_frame(cal: list[date]) -> pd.DataFrame:
    return pd.DataFrame([{
        "product_code": p.code,
        "product_name": p.name,
        "product_line": p.product_line,
        "is_revolving": p.is_revolving,
        "launch_date": cal[p.launch_month_index].replace(day=1),
        "annual_interest_rate": p.annual_rate,
    } for p in cfg.PRODUCTS])


def _round_local(amount_local: np.ndarray, currency: str) -> np.ndarray:
    step = 1000 if currency == "CZK" else 100
    return np.maximum(step, np.round(amount_local / step) * step)


def _simulate_applications(rng, cal, fx) -> tuple[pd.DataFrame, pd.DataFrame]:
    fx_me = fx.set_index(["month_end", "currency"])["rate_to_eur_month_end"]
    apps, customers = [], []
    app_seq, cust_seq = 0, {c.code: 0 for c in cfg.COUNTRIES}
    known_customers: dict[str, list[str]] = {c.code: [] for c in cfg.COUNTRIES}

    for t, me in enumerate(cal):
        live = [p for p in cfg.PRODUCTS if p.launch_month_index <= t]
        w = np.array([p.mix_weight for p in live])
        w = w / w.sum()
        for c in cfg.COUNTRIES:
            lam = c.monthly_applications * SEASONALITY[me.month] * MONTHLY_GROWTH ** t
            n = int(rng.poisson(lam))
            prod_idx = rng.choice(len(live), size=n, p=w)
            grades = rng.choice(GRADES, size=n, p=GRADE_MIX)
            day = rng.integers(1, me.day + 1, size=n)
            channel = rng.choice(CHANNELS, size=n, p=CHANNEL_MIX)
            u_status = rng.random(n)
            u_approve = rng.random(n)
            u_takeup = rng.random(n)
            decision_lag = rng.integers(0, 4, size=n)
            open_lag = rng.integers(0, 6, size=n)
            existing = rng.random(n) < EXISTING_CUSTOMER_SHARE
            amount_u = rng.random(n)
            rate = fx_me[(me, c.currency)]

            for i in range(n):
                p = live[prod_idx[i]]
                app_seq += 1
                if existing[i] and known_customers[c.code]:
                    cust_id = known_customers[c.code][int(rng.integers(len(known_customers[c.code])))]
                else:
                    cust_seq[c.code] += 1
                    cust_id = f"C{c.code}{cust_seq[c.code]:07d}"
                    known_customers[c.code].append(cust_id)
                    age_years = int(rng.integers(21, 70))
                    customers.append({
                        "customer_id": cust_id,
                        "country_code": c.code,
                        "date_of_birth": date(me.year - age_years, int(rng.integers(1, 13)), int(rng.integers(1, 29))),
                        "employment_status": rng.choice(["EMPLOYED", "SELF_EMPLOYED", "RETIRED", "OTHER"], p=[0.72, 0.14, 0.09, 0.05]),
                        "monthly_income_local": float(_round_local(np.array([rng.lognormal(7.3, 0.45) / rate]), c.currency)[0]),
                        "risk_grade": grades[i],
                        "customer_since": date(me.year, me.month, int(day[i])),
                    })
                app_date = date(me.year, me.month, int(day[i]))
                lo, hi = p.amount_eur_range
                amount_local = _round_local(np.array([(lo + (hi - lo) * amount_u[i]) / rate]), c.currency)[0]
                if u_status[i] < WITHDRAWN_SHARE:
                    status, decision = "WITHDRAWN", None
                elif u_status[i] < WITHDRAWN_SHARE + INCOMPLETE_SHARE:
                    status, decision = "INCOMPLETE", None
                else:
                    p_ok = min(0.97, p.base_approval * GRADE_APPROVAL_FACTOR[grades[i]])
                    status = "APPROVED" if u_approve[i] < p_ok else "DECLINED"
                    decision = app_date + timedelta(days=int(decision_lag[i]))
                apps.append({
                    "application_id": f"APP{app_seq:08d}",
                    "customer_id": cust_id,
                    "country_code": c.code,
                    "product_code": p.code,
                    "application_date": app_date,
                    "decision_date": decision,
                    "status": status,
                    "requested_amount_local": float(amount_local),
                    "currency": c.currency,
                    "channel": channel[i],
                    "risk_grade": grades[i],
                    "_takes_up": bool(u_takeup[i] < TAKE_UP_RATE),
                    "_open_lag": int(open_lag[i]),
                })
    return pd.DataFrame(apps), pd.DataFrame(customers)


def _month_index(cal: list[date], d: date) -> int:
    return (d.year - cal[0].year) * 12 + d.month - cal[0].month


def _simulate_accounts(rng, cal, apps: pd.DataFrame):
    """Open accounts from approved applications and run each one month by month."""
    end = cal[-1]
    window_start_idx = cfg.BURN_IN_MONTHS
    approved = apps[(apps.status == "APPROVED") & apps._takes_up].copy()
    approved["open_date"] = [d + timedelta(days=lag) for d, lag in zip(approved.decision_date, approved._open_lag)]
    approved = approved[approved.open_date <= end]

    accounts, snaps, pays = [], [], []
    seq = {c.code: 0 for c in cfg.COUNTRIES}
    pay_seq = 0
    for row in approved.itertuples(index=False):
        prod = cfg.PRODUCT_BY_CODE[row.product_code]
        seq[row.country_code] += 1
        acc_id = f"{row.country_code}{seq[row.country_code]:08d}"
        r = prod.annual_rate / 12
        amount = row.requested_amount_local
        term = int(rng.choice(prod.term_months_choices)) if not prod.is_revolving else None
        installment = amount * r / (1 - (1 + r) ** -term) if term else None
        due_day = int(rng.integers(1, 29))
        full_payer = rng.random() < 0.35
        miss_base = GRADE_MISS_PROB[row.risk_grade] * (1.1 if prod.is_revolving else 1.0)
        min_floor = 20 / cfg.COUNTRY_BY_CODE[row.country_code].fx_start  # about 20 EUR minimum payment

        t0 = _month_index(cal, row.open_date)
        status, missed = "ACTIVE", 0
        if prod.is_revolving:
            balance = round(amount * float(rng.uniform(0.0, 0.35)), 2)
        else:
            balance = float(amount)
        close_date = write_off_date = None
        write_off_amount = None

        def emit(t: int, bal: float, st: str, dpd: int) -> None:
            if t >= window_start_idx:
                snaps.append((acc_id, cal[t], row.country_code, row.product_code, row.currency,
                              round(bal, 2), dpd, st))

        def dpd_for(t: int, m: int) -> int:
            if m == 0:
                return 0
            offset = min(29, max(1, cal[t].day - due_day))
            return 30 * (m - 1) + offset

        emit(t0, balance, status, 0)
        for t in range(t0 + 1, len(cal)):
            me = cal[t]
            macro = _macro_factor(row.country_code, t, len(cal))
            paid = 0.0
            if prod.is_revolving:
                min_due = 0.0 if balance <= 0 else min(balance, max(0.05 * balance, min_floor))
                if missed == 0:
                    miss = min_due > 0 and rng.random() < miss_base * macro
                else:
                    u = rng.random()
                    p_cure = (0.35, 0.22, 0.12)[missed - 1] if missed <= 3 else 0.05
                    miss = u >= p_cure
                if miss:
                    missed += 1
                else:
                    missed = 0
                    paid = balance if full_payer else min(balance, min_due + float(rng.uniform(0, 0.3)) * balance)
                interest = 0.0 if (full_payer and missed == 0) else balance * r
                spend = 0.0 if missed >= 1 or rng.random() > 0.7 else amount * float(rng.uniform(0.02, 0.30))
                balance = min(amount * 1.05, max(0.0, balance + interest + spend - paid))
                if missed == 0 and balance == 0 and rng.random() < 0.006:
                    status, close_date = "CLOSED", me
            else:
                if missed == 0:
                    if rng.random() < 0.012:
                        paid = balance * (1 + r)
                    elif rng.random() < miss_base * macro:
                        missed = 1
                    else:
                        paid = min(installment, balance * (1 + r))
                else:
                    u = rng.random()
                    p_cure = (0.35, 0.22, 0.12)[missed - 1] if missed <= 3 else 0.05
                    p_stay = 0.25 if missed <= 3 else 0.15
                    if u < p_cure:
                        paid = min(installment * (missed + 1), balance * (1 + r))
                        missed = 0
                    elif u < p_cure + p_stay:
                        paid = min(installment, balance * (1 + r))
                    else:
                        missed += 1
                balance = max(0.0, balance * (1 + r) - paid)
                if balance < 0.5 and missed == 0:
                    balance, status, close_date = 0.0, "CLOSED", me

            if paid > 0.005:
                pay_day = min(me.day, max(1, due_day + int(rng.integers(-2, 3))))
                pay_seq += 1
                if t >= window_start_idx:
                    pays.append((f"PAY{pay_seq:09d}", acc_id, date(me.year, me.month, pay_day),
                                 round(paid, 2), row.currency, rng.choice(PAYMENT_CHANNELS, p=PAYMENT_CHANNEL_MIX)))

            if missed >= 7:
                status, write_off_date, write_off_amount = "WRITTEN_OFF", me, round(balance, 2)
                balance = 0.0
            emit(t, balance, status, 0 if status != "ACTIVE" else dpd_for(t, missed))
            if status != "ACTIVE":
                break

        accounts.append({
            "account_id": acc_id,
            "customer_id": row.customer_id,
            "application_id": row.application_id,
            "country_code": row.country_code,
            "product_code": row.product_code,
            "currency": row.currency,
            "open_date": row.open_date,
            "original_amount_local": float(amount),
            "term_months": term,
            "annual_interest_rate": prod.annual_rate,
            "close_date": close_date,
            "write_off_date": write_off_date,
            "write_off_amount_local": write_off_amount,
        })

    balances = pd.DataFrame(snaps, columns=["account_id", "snapshot_date", "country_code", "product_code",
                                            "currency", "balance_local", "days_past_due", "account_status"])
    repayments = pd.DataFrame(pays, columns=["payment_id", "account_id", "payment_date", "amount_local",
                                             "currency", "payment_channel"])
    return pd.DataFrame(accounts), balances, repayments


def simulate(seed: int = cfg.SEED) -> CoreData:
    rng = np.random.default_rng(seed)
    cal = cfg.simulation_calendar()
    fx = simulate_fx(rng, cal)
    apps, customers = _simulate_applications(rng, cal, fx)
    accounts, balances, repayments = _simulate_accounts(rng, cal, apps)
    apps = apps.drop(columns=["_takes_up", "_open_lag"])
    return CoreData(
        products=_products_frame(cal),
        fx_rates=fx,
        customers=customers,
        applications=apps,
        accounts=accounts,
        balances=balances,
        repayments=repayments,
    )


def last_day(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
