# Phase 1 manual steps

Time needed: about 30 minutes.

## A. Run locally

```bash
py -3.11 -m pip install -r requirements-dev.txt
py -3.11 -m pytest -q
py -3.11 -m pip install -e .
py -3.11 -m portfolio_migration generate
```

**You should see:** `20 passed`, then a JSON summary that includes `"legacy_published_kpi_cells": 672`.
`data/landing/` now holds 195 files (about 39 MB).

## B. Look at the legacy workbook

1. Open `data/legacy/Monthly_Portfolio_Pack.xlsx` in Excel. It recalculates on open.
2. Go to the **Summary** tab.

**You should see:** 24 months from 2024-09-30 to 2026-08-31, a group balance of
about EUR 18.3m in Sep 2024 rising to about EUR 34.0m in Aug 2026, and a line chart.

**Screenshot:** `docs/screenshots/p1-legacy-summary.png` (Summary tab with the chart).

## C. Set up Fabric

1. Go to https://app.fabric.microsoft.com and sign in with your work or school account.
2. If you have no capacity: profile icon (top right) > **Start trial**. The Fabric trial gives you 60 days.
3. **Workspaces** > **New workspace**
   * Name: `portfolio-reporting-dev`
   * Advanced > License mode: **Trial** (or your Fabric capacity)
4. In the workspace: **New item** > **Lakehouse**
   * Name: `lh_portfolio`
   * Leave "Lakehouse schemas" **unticked**. Our notebooks use plain table names.

**You should see:** the lakehouse explorer with two empty folders, `Tables` and `Files`.

## D. Upload the landed files

1. In the lakehouse explorer, hover over **Files** > `...` > **New subfolder** > name it `landing`.
2. Hover over `landing` > `...` > **Upload** > **Upload folder**.
3. Upload each of these four local folders, one at a time, from `data/landing/`:
   `core_banking`, `los`, `reference`, `treasury`.

**You should see:** `Files/landing/core_banking/PL/balances/` holding 24 files, and
`Files/landing/core_banking/RO/balances/` holding 25 (one is the `_resend`).

**Screenshots:**
* `docs/screenshots/p1-workspace.png` (the workspace item list)
* `docs/screenshots/p1-landing-files.png` (the expanded `Files/landing` tree showing the RO balances folder)

## E. Tell me when done

Paste: "Phase 1 done" plus anything that looked different from above.
