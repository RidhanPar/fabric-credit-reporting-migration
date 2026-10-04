# Report specification: Monthly Portfolio Pack

The report that replaces the Excel pack. Four pages, each answering one question
for one audience. Built on the `LendCoPortfolio` semantic model (Direct Lake), so
every figure is a documented measure and no page does its own arithmetic.

| Page | Question it answers | Audience | Cadence |
|---|---|---|---|
| 1. Executive summary | Is the book growing, and is credit quality holding? | Board, ExCo | Monthly |
| 2. Delinquency | Where are the arrears, by country and product? | Credit risk | Weekly |
| 3. Account detail | Which accounts sit behind this number? | Collections, audit | Daily, by drillthrough |
| 4. Migration evidence | Can we trust these numbers instead of the old pack? | Finance, internal audit | During the parallel run |

## Global settings

* **Slicers** on pages 1 and 2, synced: month end (`dim_date[year_month]`, single
  select, defaulting to the latest), country (`dim_country[country_name]`),
  product line (`dim_product[product_line]`).
* **Row level security** is on the model, so a country manager opening page 1
  sees their country only and the slicer shows one country. No separate report
  per market.
* **Measures only.** The model sets `discourageImplicitMeasures`, and every
  numeric column is `summarizeBy: none`, so a report author cannot drag a column
  in and invent a number.
* **Currency.** Only EUR columns are visible. Local currency columns are hidden
  in the model, because adding koruna to zloty is meaningless.
* Balances are semi additive: over a quarter or a year, `Portfolio balance (EUR)`
  shows the latest month end in the period, not a sum. Flow measures such as
  `New originations (EUR)` do add up.

## Page 1. Executive summary

| Visual | Type | Fields |
|---|---|---|
| Portfolio balance | Card | `Portfolio balance (EUR)` |
| Growth | Card | `Portfolio balance MoM %` |
| 30+ DPD rate | Card with sparkline | `30+ DPD rate`, trend over the last 12 month ends |
| Arrears move | Card | `30+ DPD rate change` (percentage points, green down) |
| Approval rate | Card | `Approval rate` |
| New lending | Card | `New originations (EUR)` |
| Portfolio balance by month | Line | Axis `dim_date[year_month]`, values `Portfolio balance (EUR)`, legend `dim_country[country_name]` |
| New lending by month | Clustered column | Axis `dim_date[year_month]`, values `New originations (EUR)`, legend `dim_product[product_line]` |
| Country summary | Table | Rows `dim_country[country_name]`; `Portfolio balance (EUR)`, `Active customers`, `Avg balance per customer (EUR)`, `30+ DPD rate`, `Approval rate` |

* The country summary table is the direct replacement for the old Summary tab.
* A button on the page navigates to Delinquency, preserving the slicer state.

## Page 2. Delinquency

| Visual | Type | Fields |
|---|---|---|
| Arrears trend | Line | Axis `dim_date[year_month]`, values `30+ DPD rate` and `90+ DPD rate`, small multiples by `dim_country[country_name]` |
| Balance by DPD bucket | Stacked column | Axis `dim_date[year_month]`, values `Portfolio balance (EUR)`, legend `fact_balance_snapshot[dpd_bucket]` (sorted by `dpd_bucket_order`) |
| Country and product matrix | Matrix | Rows `dim_country[country_name]` then `dim_product[product_name]`; values `Portfolio balance (EUR)`, `Balance 30+ DPD (EUR)`, `30+ DPD rate`, `90+ DPD rate`; conditional formatting on the 30+ rate, amber above 4 percent, red above 6 percent |
| Risk grade split | Clustered bar | Axis `dim_customer[risk_grade]`, values `30+ DPD rate` |
| Worst accounts | Table | Rows `dim_account[account_id]`, `dim_account[account_state]`, `fact_balance_snapshot[days_past_due]`, `Portfolio balance (EUR)`; top 50 by balance where `days_past_due >= 30`; drillthrough enabled |

* Romania's arrears rise through the series, so the trend visual is where that
  shows up first.
* The bucket visual uses the balance, not account counts, matching how the 30+
  rate is defined.

## Page 3. Account detail (drillthrough)

Drillthrough page, hidden from the navigation. Drillthrough fields:
`dim_account[account_id]`, `dim_country[country_name]`, `dim_product[product_name]`,
`fact_balance_snapshot[dpd_bucket]`. "Keep all filters" on, with a back button.

| Visual | Type | Fields |
|---|---|---|
| Account header | Multi row card | `dim_account[account_id]`, `[product_code]` via `dim_product[product_name]`, `[open_date]`, `[term_months]`, `[annual_interest_rate]`, `[account_state]`, `[currency]` |
| Customer | Multi row card | `dim_customer[customer_id]`, `[risk_grade]`, `[age_band]`, `[employment_status]`, `[customer_since]` |
| Balance history | Line and column | Axis `dim_date[year_month]`, line `Portfolio balance (EUR)`, column `fact_balance_snapshot[days_past_due]` |
| Payment history | Table | Rows `fact_repayment[payment_date]`, `[payment_channel]`, `Repayments (EUR)` |
| Arrears history | Table | Rows `dim_date[year_month]`, `fact_balance_snapshot[dpd_bucket]`, `[days_past_due]`, `Portfolio balance (EUR)` |

This page is the answer to the question every migration gets: "can I still get
to the account list, the way I could in the spreadsheet?"

## Page 4. Migration evidence

The page that retires the old pack. It is the reconciliation, in the report.

| Visual | Type | Fields |
|---|---|---|
| Unexplained differences | Card | `Unexplained differences`, with a rule turning it red above 0 |
| Legacy error effect | Card | `Legacy error effect (EUR)` |
| Checks run and failed | Cards | `Quality checks run`, `Quality checks failed` |
| Difference by cause | Clustered bar | Axis `recon_attribution[cause]`, values `Legacy error effect (EUR)` by `recon_attribution[classification]` |
| Attribution detail | Table | Rows `recon_attribution[month_end]`, `[scope]`, `[kpi]`, `[cause_id]`, `[classification]`, `[amount]` |
| Quality results | Table | Rows `dq_results[layer]`, `[table_name]`, `[check_name]`, `[severity]`, `[rows_failed]`, `[passed]`; filtered to the latest `batch_id` |
| Old versus new | Table | `Avg balance per account (EUR)` against `Avg balance per customer (EUR)`, and `Approval rate (legacy definition)` against `Approval rate`, by `dim_date[year_month]` |

The last visual exists because during a parallel run people need to see both
definitions side by side, not be told the old one was wrong.

## Performance and accessibility rules

* Keep each page to about 8 visuals. Every visual is a DAX query, and a page full
  of them is the main cause of a slow report.
* No visual on a column with account level cardinality except on page 3, which is
  always filtered to one account by the drillthrough.
* Direct Lake can fall back to DirectQuery when a query exceeds the capacity
  guardrails, which is slower and harder to explain. Narrow tables, typed columns
  and few visuals per page are how that is avoided.
* Titles say what the figure is and in what currency. Every card has its measure's
  description as its tooltip, which comes from the model, so the definition
  travels with the number.
* Colour is never the only signal: the arrears formatting carries an arrow and a
  value, for readers who cannot distinguish amber from red.

## Definition of done

1. Every figure on every page comes from a measure in the model, not from an
   implicit aggregation or a calculation in the visual.
2. Each page passes the "whose question is this" test, and the page title says so.
3. Row level security verified by viewing as each of the three country roles and
   as group reporting.
4. Page 4 shows `Unexplained differences` as 0.
5. Query response times recorded with Performance Analyzer and written into the
   README (Phase 6).
