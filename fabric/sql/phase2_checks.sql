-- Phase 2 checks. Run in the lh_portfolio SQL analytics endpoint (read only T-SQL).

-- 1. Every bronze row is accounted for. "unexplained" must be 0 on every line.
SELECT entity, bronze_rows, control_rows, quarantined_rows, duplicate_rows, silver_rows,
       bronze_rows - control_rows - quarantined_rows - duplicate_rows - silver_rows AS unexplained
FROM silver_load_audit
WHERE batch_id = (SELECT TOP 1 batch_id FROM silver_load_audit ORDER BY loaded_at DESC)
ORDER BY entity;

-- 2. What went to quarantine, and why.
SELECT entity, reasons, COUNT(*) AS row_count
FROM silver_quarantine
GROUP BY entity, reasons
ORDER BY entity, reasons;

-- 3. Group portfolio balance in EUR by month end, from gold.
SELECT d.year_month, SUM(f.balance_eur) AS portfolio_balance_eur
FROM fact_balance_snapshot AS f
JOIN dim_date AS d ON d.date_key = f.date_key
WHERE f.is_active = 1
GROUP BY d.year_month
ORDER BY d.year_month;
