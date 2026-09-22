"""Landed files must look like real extracts and still contain the whole truth."""
import json
from decimal import Decimal

import pandas as pd

from portfolio_migration import config as cfg


def _read_balance_file(path, country):
    sep = ";" if country.csv_decimal == "," else ","
    lines = path.read_text(encoding="utf-8").splitlines()
    trailer = lines[-1].split(sep)
    assert trailer[0] == "TRL"
    body = [ln.split(sep) for ln in lines[1:-1]]
    return lines[0].split(sep), body, int(trailer[1]), Decimal(trailer[2].replace(",", "."))


def test_every_balance_file_matches_its_trailer(landing):
    root, _ = landing
    checked = 0
    for c in cfg.COUNTRIES:
        for path in sorted((root / "core_banking" / c.code / "balances").glob("*.csv")):
            header, body, count, total = _read_balance_file(path, c)
            assert len(body) == count, path.name
            idx = header.index("balance_local")
            assert sum(Decimal(r[idx].replace(",", ".")) for r in body) == total, path.name
            checked += 1
    assert checked == 3 * 24 + 1  # plus the resent file


def test_country_formats_differ(landing):
    root, _ = landing
    cz = (root / "core_banking/CZ/balances/balances_CZ_202608.csv").read_text().splitlines()[1]
    ro = (root / "core_banking/RO/balances/balances_RO_202608.csv").read_text().splitlines()[1]
    assert ";" in cz and "," in cz.split(";")[5]
    assert "31/08/2026" in ro


def test_real_balance_rows_equal_truth_after_cleaning(landing, core):
    root, manifest = landing
    frames = []
    for c in cfg.COUNTRIES:
        for path in (root / "core_banking" / c.code / "balances").glob("balances_??_??????.csv"):
            header, body, _, _ = _read_balance_file(path, c)
            frames.append(pd.DataFrame(body, columns=header))
    raw = pd.concat(frames)
    real = raw[raw.account_id != ""]
    assert len(raw) - len(real) == manifest.defects["blank_account_id"]
    assert len(real) == len(core.balances)
    assert set(real.product_code.str.strip().str.upper()) == set(core.balances.product_code)


def test_application_events_rebuild_to_truth(landing, core):
    root, manifest = landing
    good, bad = [], 0
    for path in sorted((root / "los/applications").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                good.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    assert bad == manifest.defects["truncated_json_line"]
    latest = (pd.json_normalize(good).sort_values("event_ts")
                .drop_duplicates("application_id", keep="last"))
    assert len(latest) == len(core.applications)
    assert (latest["decision.status"] != "PENDING").all()
    truth = core.applications.set_index("application_id").status
    assert (latest.set_index("application_id")["decision.status"] == truth.loc[latest.application_id].values).all()


def test_every_defect_type_is_present(landing):
    _, manifest = landing
    for key in ("padded_lowercase_product_code", "blank_account_id", "duplicate_repayment_rows",
                "resent_balance_file_rows", "pending_event_before_final", "truncated_json_line"):
        assert manifest.defects.get(key, 0) > 0, key
