# Issues and fixes

Every real problem hit during the build: what was seen, how it was diagnosed,
the root cause, the fix, the evidence, and the line to use in an interview.
Only issues that actually happened are recorded here.

## Best interview stories

| # | Story | Phase |
|---|---|---|
| 1 | Interest rates silently rounded to 2 decimals in the landed files | 1 |
| 2 | Python `hash()` would have made the JSON timestamps change on every run | 1 |

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
