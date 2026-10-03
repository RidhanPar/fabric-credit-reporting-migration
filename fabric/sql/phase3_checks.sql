-- Phase 3: reporting on data quality. Run in the lh_portfolio SQL analytics endpoint.

-- 1. Latest run, one line per check. This is the sign off view.
SELECT layer, table_name, check_name, kind, severity, rows_checked, rows_failed, passed, detail
FROM dq_results
WHERE batch_id = (SELECT TOP 1 batch_id FROM dq_results ORDER BY checked_at DESC)
ORDER BY passed, severity DESC, layer, table_name, check_name;

-- 2. Did anything fail, and did it stop the publish?
SELECT batch_id, layer, severity, COUNT(*) AS checks, SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END) AS failed
FROM dq_results
GROUP BY batch_id, layer, severity
ORDER BY batch_id, layer, severity;

-- 3. The slowest checks, for tuning.
SELECT TOP 10 check_name, layer, rows_checked, seconds
FROM dq_results
WHERE batch_id = (SELECT TOP 1 batch_id FROM dq_results ORDER BY checked_at DESC)
ORDER BY seconds DESC;

-- 4. Row reconciliation across layers, per entity and run.
SELECT batch_id, entity, bronze_rows, control_rows, quarantined_rows, duplicate_rows, silver_rows,
       bronze_rows - control_rows - quarantined_rows - duplicate_rows - silver_rows AS unexplained
FROM silver_load_audit
ORDER BY loaded_at DESC, entity;

-- 5. What is in quarantine right now, with an example record.
SELECT entity, reasons, COUNT(*) AS rows_quarantined, MIN(raw_record) AS example
FROM silver_quarantine
GROUP BY entity, reasons
ORDER BY rows_quarantined DESC;
