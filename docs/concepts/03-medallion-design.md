# 03. Medallion design: what was built and how to defend it

Read [02-fabric-lakehouse-primer.md](02-fabric-lakehouse-primer.md) first. This
page covers the two new concepts from Phase 2 (Environments and the star schema)
and the decisions an interviewer will probe.

## New concept: Fabric Environment

**What:** an Environment is a Fabric item that pins the Spark runtime version and
installs libraries (from PyPI or an uploaded wheel). Notebooks attach to it.

**Case:** three analysts copy and paste the same cleaning function into their
notebooks. One fixes a bug in theirs; the other two keep the bug. With a wheel in
an Environment there is one copy of the logic, versioned, tested in CI.

**Say:** "Notebooks are thin. The logic ships as a tested wheel through an
Environment, so what CI tested is what Fabric runs."

## New concept: star schema

**What:** facts hold measurable events at a declared grain; dimensions hold the
things you slice by. Power BI's engine is built for this shape.

**Case:** the board wants arrears by product line and country, and Risk wants the
same by risk grade. One `fact_balance_snapshot` joined to `dim_product`,
`dim_country` and `dim_customer` answers both. One wide flat table would have
repeated every product attribute on 225k rows.

| Table | Grain |
|---|---|
| fact_balance_snapshot | one account at one month end |
| fact_origination | one account, at the date it opened |
| fact_application | one application, current state |
| fact_repayment | one payment |
| dim_date, dim_country, dim_product, dim_customer, dim_account | one row per member |

**Say:** "The first thing I write for a fact table is its grain. If I cannot
say it in one sentence, the table is wrong."

## Decisions to defend

| Decision | Why | Alternative rejected |
|---|---|---|
| Bronze keeps every column as a string | A wrong parse rule is fixed in silver and replayed. No resend needed | Typed bronze: a bad parse loses the raw value |
| Bronze loads by file, skipping files already loaded | Reruns and pipeline retries can never duplicate | Overwrite all: loses load history |
| Silver rebuilt in full each run | 230k rows; simple and idempotent | MERGE: right at 10m+ rows, extra complexity now |
| Parse by the source's locale (`_country_feed`) | CZ `14665,02` must become 14665.02, not null | One global parser |
| Unknown means invalid (a rule that returns null fails) | A row we cannot prove valid is not reported | Null rules pass silently |
| Quarantine with every reason and the raw row | Audit can see exactly what left and why | Filter and forget |
| Latest delivery wins on duplicate keys | A resend is a correction by definition | First wins |
| Application events kept as history; latest event is current state | A PENDING event is real history, not bad data | Treat earlier events as duplicates |
| Money as `DECIMAL(18,2)`, rates `DECIMAL(18,8)` | No float rounding in finance totals | Double |
| Balances at month end rate; flows at monthly average rate | Standard IAS 21 practice: stocks at closing rate, flows at average | One rate for everything |
| Natural keys plus integer `date_key` | 16k accounts; readable lineage. Surrogates matter for SCD2 | Surrogate keys everywhere |
| Three notebooks plus a pipeline, not one notebook | Each layer can fail, retry and be timed separately | One monolith |

## The proof silver lost nothing

From the local run and the Spark tests (seed 42):

| Entity | Bronze rows | Trailers | Quarantined | Duplicates | Silver rows |
|---|---|---|---|---|---|
| balances | 228,308 | 73 | 9 | 2,869 | 225,357 |
| repayments | 194,393 | 0 | 0 | 376 | 194,017 |
| application events | 35,720 | 0 | 37 | 0 | 35,683 |

225,357 silver balance rows is exactly the true number, and a test compares all of
them value by value against the truth. 35,683 events resolve to 34,714
applications, also the true number.

**Say:** "Bronze equals trailers plus quarantine plus duplicates plus silver, for
every entity, every run. It's in an audit table, so I don't have to argue it."
