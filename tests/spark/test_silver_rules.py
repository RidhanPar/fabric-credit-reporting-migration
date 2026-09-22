"""Silver parsing and validation rules on small hand made rows."""
from datetime import datetime

from pyspark.sql import functions as F

from portfolio_migration.lakehouse import silver

BAL_COLS = ["account_id", "snapshot_date", "country_code", "product_code", "currency", "balance_local",
            "days_past_due", "account_status", "_country_feed", "_source_file"]
SCHEMA = ",".join(f"{c} string" for c in BAL_COLS) + ",_source_modified_at timestamp,_ingested_at timestamp,_batch_id string"
PRODUCTS = ["PL_STD", "CC_GOLD"]


def _row(acc="CZ00000001", date="2026-08-31", ctry="CZ", prod="PL_STD", bal="100,50", dpd="0",
         status="ACTIVE", feed="CZ", file="f1.csv", modified="2026-09-01 10:00:00"):
    return (acc, date, ctry, prod, "CZK", bal, dpd, status, feed, file,
            datetime.fromisoformat(modified), datetime(2026, 9, 2), "b1")


def _bronze(spark, rows):
    return spark.createDataFrame(rows, SCHEMA)


def test_cz_decimal_comma_and_ro_dates_parse(spark):
    rows = [_row(), _row(acc="RO00000001", date="31/08/2026", ctry="RO", bal="200.25", feed="RO")]
    good, bad = silver.silver_balances(_bronze(spark, rows), PRODUCTS)
    got = {r.account_id: (str(r.snapshot_date), str(r.balance_local)) for r in good.collect()}
    assert got == {"CZ00000001": ("2026-08-31", "100.50"), "RO00000001": ("2026-08-31", "200.25")}
    assert bad.count() == 0


def test_padded_lower_case_product_is_repaired_not_rejected(spark):
    good, bad = silver.silver_balances(_bronze(spark, [_row(prod="  pl_std ")]), PRODUCTS)
    assert [r.product_code for r in good.collect()] == ["PL_STD"]
    assert bad.count() == 0


def test_bad_rows_are_quarantined_with_every_reason(spark):
    rows = [_row(acc="", bal="abc"), _row(acc="CZ00000009", ctry=None, prod="XX", dpd="-1")]
    good, bad = silver.silver_balances(_bronze(spark, rows), PRODUCTS)
    assert good.count() == 0
    q = silver.to_quarantine(bad, "balances").collect()
    assert sorted(r.reasons for r in q) == ["COUNTRY_FEED_MISMATCH,UNKNOWN_PRODUCT,INVALID_DPD",
                                            "MISSING_ACCOUNT_ID,INVALID_BALANCE"]
    assert all('"account_id"' in r.raw_record and r.source_file == "f1.csv" for r in q)


def test_resent_file_keeps_one_row_the_latest_delivery(spark):
    rows = [_row(bal="1,00", file="balances_CZ_202608.csv", modified="2026-09-01 10:00:00"),
            _row(bal="2,00", file="balances_CZ_202608_resend.csv", modified="2026-09-01 11:00:00")]
    good, _ = silver.silver_balances(_bronze(spark, rows), PRODUCTS)
    assert [str(r.balance_local) for r in good.collect()] == ["2.00"]


def test_trailers_are_split_out(spark):
    trl = ("TRL", "1", "100,50", None, None, None, None, None, "CZ", "f1.csv", None, None, "b1")
    body, trailers = silver.split_trailers(_bronze(spark, [_row(), trl]))
    assert body.count() == 1
    t = trailers.collect()[0]
    assert (t.expected_rows, str(t.expected_amount)) == (1, "100.50")


EVENT = ('{"application_id": "APP1", "submitted_at": "2026-08-01T09:15:00Z", "channel": "ONLINE", '
         '"applicant": {"customer_id": "C1", "country": "PL", "risk_grade": "B"}, '
         '"product": {"code": "PL_STD", "requested_amount": 1000.0, "currency": "PLN"}, '
         '"decision": {"status": "STATUS", "decided_on": DECIDED}, "event_ts": "TS"}')


def test_application_events_latest_wins_and_truncated_lines_quarantined(spark):
    pending = EVENT.replace("STATUS", "PENDING").replace("DECIDED", "null").replace("TS", "2026-08-01T09:30:00Z")
    final = EVENT.replace("STATUS", "APPROVED").replace("DECIDED", '"2026-08-02"').replace("TS", "2026-08-02T18:00:00Z")
    b = (spark.createDataFrame([(pending,), (final,), (final[:40],)], "raw_json string")
         .withColumn("_country_feed", F.lit("GROUP")).withColumn("_source_file", F.lit("a.jsonl"))
         .withColumn("_source_modified_at", F.current_timestamp())
         .withColumn("_ingested_at", F.current_timestamp()).withColumn("_batch_id", F.lit("b1")))
    events, bad = silver.silver_application_events(b, PRODUCTS)
    assert events.count() == 2
    assert silver.to_quarantine(bad, "application_events").collect()[0].reasons.startswith("MALFORMED_JSON")
    current = silver.current_applications(events).collect()
    assert [(r.application_id, r.status, str(r.decision_date)) for r in current] == [("APP1", "APPROVED", "2026-08-02")]
