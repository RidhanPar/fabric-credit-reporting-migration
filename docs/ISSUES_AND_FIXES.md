# Issues and fixes

Every real problem hit during the build: what was seen, how it was diagnosed,
the root cause, the fix, the evidence, and the line to use in an interview.
Only issues that actually happened are recorded here.

## Best interview stories

| # | Story | Phase |
|---|---|---|
| 1 | The reconciliation found a bug in my own model: Spark truncated every rate to 6 decimals | 4 |
| 2 | A null country code would have passed validation: unknown must mean invalid | 2 |
| 3 | Spark would not start on Windows; ran it in a container matching Fabric Runtime 1.3 | 2 |
| 4 | Interest rates silently rounded to 2 decimals in the landed files | 1 |
| 5 | Spark columns built at import time crash before a session exists | 2 |
| 6 | Python `hash()` would have made the JSON timestamps change on every run | 1 |
| 7 | A bad parse gives null, not a wrong number, which changes which check catches it | 3 |

## Phase 1

### 1. Interest rates rounded to 2 decimals in the landed account files

* **Seen:** a sample line of `accounts_RO_20260831.csv` showed
  `annual_interest_rate` as `0.15`. The product catalogue says 0.149.
* **Diagnosis:** the CSV writer used `float_format="%.2f"` for every float
  column so money would print with 2 decimals. Rates are floats too.
* **Root cause:** one format rule applied to two kinds of number.
* **Fix:** format the rate column to 4 decimals, in the country's decimal
  separator, before writing (`generate/landing.py`).
* **Evidence:** after the fix the CZ file shows `0,1490`.
* **Say:** "I spot check raw files by eye before I trust any test. A blanket
  format rule rounded interest rates to two decimals. No test would have caught it
  because nothing downstream used the rate yet."

### 2. `hash()` is not stable between Python runs

* **Seen:** caught in self review before the first run. The JSON
  `submitted_at` hour was built from `hash(application_id)`.
* **Root cause:** Python salts string hashes per process (`PYTHONHASHSEED`),
  so the value changes every run and the output would not be reproducible.
* **Fix:** derive the hour from the numeric part of the application id.
* **Evidence:** `test_simulation_is_deterministic` and
  `test_committed_legacy_figures_reproduce` pass.
* **Say:** "Reproducibility is a requirement, not a nice to have. If a number is
  in the README, rerunning from the seed has to give the same number."

### 3. Lookups tab header overwritten

* **Seen:** caught in self review before the first run. Writing the FX table
  header with the shared `_header` helper, padded with 4 blank cells, would have
  blanked the product mapping header in A2:C2.
* **Fix:** write the FX header cells directly from column E.
* **Say:** small, but it is the same class of bug as a pasted range landing on
  top of another one in a real workbook.

## Phase 2

### 4. Spark would not start on Windows

* **Seen:** `local_spark()` failed with `java.io.IOException: Unable to establish
  loopback connection` from `UnixDomainSockets.connect`.
* **First fix:** JDK 17 opens a Unix domain socket for its internal pipe and
  could not create it under the default temp path. Setting
  `-Djdk.net.unixdomain.tmpdir=C:/tmp/jdk` got past it.
* **Next error:** `HADOOP_HOME and hadoop.home.dir are unset`. Spark on
  Windows needs Hadoop's `winutils.exe` even to start a local session.
* **Decision:** do not download unofficial winutils binaries. Run Spark in a
  small Linux container (`Dockerfile.spark`) pinned to Fabric Runtime 1.3
  versions: Python 3.11, Spark 3.5.5, Delta 3.2.1. CI runs the same tests
  natively on Linux. On Windows the Spark tests skip themselves with the reason.
* **Evidence:** 14 Spark tests pass in the container in 226.65 s.
* **Say:** "I matched my local Spark to the Fabric runtime version instead of
  fighting Windows. The container is also closer to what Fabric actually runs."

### 5. Spark columns built at import time

* **Seen:** first container run died with `AssertionError` in
  `pyspark/sql/functions.py`: `SparkContext._active_spark_context is not None`.
* **Diagnosis:** the traceback pointed at `silver.py` import. A module level
  constant, the dedup ordering `[F.col("_ingested_at").desc(), ...]`, built Spark
  columns when the module was imported, before any session existed.
* **Fix:** make it a function, `latest_delivery()`, called at run time.
* **Evidence:** the next run completed bronze, silver and gold.
* **Say:** "In classic PySpark a Column needs a live session. Anything Spark
  shaped belongs in a function, not at module level, so the module is safe to
  import anywhere."

### 6. Null rules would have let bad rows through

* **Seen:** caught in self review of `split_valid`. A rule like
  `country_code != _country_feed` evaluates to null, not true, when
  `country_code` is null, and `when(null, reason)` adds no reason. The row would
  have passed as valid.
* **Fix:** wrap every rule in `coalesce(rule, true)`, so a rule that cannot be
  evaluated counts as failed.
* **Evidence:** `test_bad_rows_are_quarantined_with_every_reason` sends a row
  with a null country and asserts `COUNTRY_FEED_MISMATCH` is among its reasons.
* **Say:** "SQL three valued logic is the classic silent data quality bug. My
  rule is: if a row can't be proven valid, it isn't."

### 7. Typed and raw columns with the same name

* **Seen:** caught in self review before the first run. The first silver draft
  selected the typed column (for example `balance_local` as decimal) next to the
  raw string column of the same name, kept for quarantine. Spark allows two
  columns with the same name but any later reference to it is ambiguous.
* **Fix:** carry raw columns as `_raw_<name>`. The quarantine JSON strips the
  prefix, so the raw record reads exactly like the source file.
* **Say:** "I keep the raw value next to the parsed one until validation is
  done, under a different name, so a quarantined row shows exactly what arrived."

## Phase 3

### 8. One dictionary key, two meanings

* **Seen:** `test_rerunning_is_idempotent` failed with `KeyError: 'table'` after
  the gold step started reporting its quality checks.
* **Diagnosis:** the runner built the gold step payload as
  `{"results": [tables]}` and then merged the quality summary into it, which also
  had a `results` key. The table list was silently replaced by the check list.
* **Fix:** name each thing: `tables`, `entities`, `check_results`.
* **Evidence:** 20 Spark tests pass, and `docs/results/phase3_local_run.json`
  now shows both lists under the gold step.
* **Say:** "A dict merge overwrote one list with another. The test caught it in a
  second, which is the argument for having the run summary under test at all."

### 9. A bad parse gives null, not a wrong number

* **Seen:** I wrote a test asserting that parsing the Czech `100,50` with the
  wrong locale would produce 10050. It produced `None`.
* **Diagnosis:** Spark's cast to decimal returns null for `100,50`, it does not
  strip the comma. So that class of bug surfaces as nulls, and the not null
  check catches it.
* **Why it matters:** the control total check earns its place for the other
  case: a parse that stays numeric but is wrong, such as a comma read as a
  thousands separator. The test now simulates that, and the check catches it
  because the row count still matches the trailer and the money does not.
* **Say:** "I had the failure mode backwards. A failed cast is null and easy to
  catch. The dangerous bug is the one that is still a number, which is why I
  reconcile amounts against the file's own control total, not just row counts."

## Phase 4

### 10. The reconciliation found a bug in my own model

* **Seen:** the first full reconciliation closed on amounts, with a worst residual
  of 0.000000004 EUR, and then failed on rates: 160 of the 672 published figures
  had residuals up to 0.0000005, above the 0.0000001 rate tolerance. The bridge
  itself added up, so nothing was missing; the gold rate simply did not equal the
  corrected legacy rate.
* **Diagnosis:** printed the worst cell. Gold's rate was `0.028232000000` and the
  legacy corrected rate was `0.028232498934`. Exactly six decimals in gold is not
  a coincidence. The KPI builder divided one decimal column by another: both
  operands were `decimal(38,10)`, and Spark caps the scale of a decimal division
  result, landing it at 6 decimal places. Every rate in the table had been
  silently truncated.
* **Root cause:** using decimal types for a ratio. Decimal is right for money,
  and wrong for a quotient whose precision matters more than its exactness.
* **Fix:** cast both sides to double before dividing (`kpis.py::_safe_divide`).
* **Guard:** a new data quality check, `gold_kpi_monthly.rates_not_truncated`,
  fails if most rate values sit exactly on 6 decimals. A genuine rate almost
  never does, so that pattern means precision has been lost again.
* **Classification:** this is the `NEW_MODEL_ERROR` class of the reconciliation,
  found exactly the way that class is meant to be found: by elimination, once the
  legacy faults and the agreed definition changes were removed and something was
  still left over.
* **Say:** "The reconciliation is not there to prove the old report wrong. It is
  there to explain every difference, and the first thing it found was a bug in
  mine. Spark caps the scale when you divide two decimals, so all my rates were
  truncated at six decimal places. The tolerance caught it, I fixed the type, and
  I added a check that fails if rates ever look truncated again."

### 11. The reconciliation quietly needed Spark

* **Seen:** CI green locally, red on the Linux job:
  `ModuleNotFoundError: No module named 'pyspark'` while importing
  `tests/test_reconciliation.py`.
* **Diagnosis:** the reconciliation is meant to be plain pandas, so the fast CI
  job does not install pyspark. `reconcile/bridge.py` imported the KPI variant
  chains from `lakehouse/kpis.py`, which imports pyspark. The import was for
  metadata only, two dictionaries.
* **Fix:** moved the KPI names, variant chains and step descriptions into
  `kpi_definitions.py`, plain Python, read by both the Spark builder and the
  pandas reconciliation.
* **Guard:** a test walks the AST of every module in `reconcile/` and fails if
  any of them imports pyspark or anything from `lakehouse/`.
* **Say:** "The cheap CI job is the one that catches layering mistakes. It does
  not install Spark, so an accidental dependency on it fails immediately. I moved
  the shared metadata into a module that neither layer owns."
