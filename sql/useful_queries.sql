-- ===========================================================================
-- useful_queries.sql — Reference queries for actuaries and analysts
-- Atlas RA Seismic ETL Pipeline
-- ===========================================================================

USE seismic_db;

-- ---------------------------------------------------------------------------
-- Q1: Strong earthquakes (mag ≥ 6.0) in Japan in the last 90 days
-- ---------------------------------------------------------------------------
SELECT
    event_id,
    magnitude,
    magnitude_class,
    place,
    event_time,
    depth_km,
    depth_class,
    risk_score,
    alert_level,
    tsunami
FROM terremotos
WHERE region_id = 'japan'
  AND magnitude >= 6.0
  AND event_time >= DATE_SUB(NOW(), INTERVAL 90 DAY)
ORDER BY magnitude DESC;


-- ---------------------------------------------------------------------------
-- Q2: Region with highest average risk_score this month
-- ---------------------------------------------------------------------------
SELECT
    t.region_id,
    r.display_name,
    ROUND(AVG(t.risk_score), 2)  AS avg_risk_score,
    COUNT(*)                      AS total_events,
    MAX(t.magnitude)              AS max_magnitude
FROM terremotos t
JOIN regiones r USING (region_id)
WHERE t.event_time >= DATE_FORMAT(NOW(), '%Y-%m-01')
GROUP BY t.region_id, r.display_name
ORDER BY avg_risk_score DESC
LIMIT 5;


-- ---------------------------------------------------------------------------
-- Q3: Tsunami alerts per region in the last year
-- ---------------------------------------------------------------------------
SELECT
    at.region_id,
    r.display_name,
    COUNT(*)            AS tsunami_alerts,
    MAX(at.magnitude)   AS max_magnitude,
    MIN(at.detected_at) AS first_detected,
    MAX(at.detected_at) AS last_detected
FROM alertas_tsunami at
JOIN regiones r USING (region_id)
WHERE at.detected_at >= DATE_SUB(NOW(), INTERVAL 1 YEAR)
GROUP BY at.region_id, r.display_name
ORDER BY tsunami_alerts DESC;


-- ---------------------------------------------------------------------------
-- Q4: Top 10 most significant earthquakes in the last 30 days (global)
-- ---------------------------------------------------------------------------
SELECT
    event_id,
    magnitude,
    magnitude_class,
    place,
    region_id,
    event_time,
    significance,
    risk_score,
    depth_km,
    alert_level,
    tsunami
FROM terremotos
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
ORDER BY significance DESC
LIMIT 10;


-- ---------------------------------------------------------------------------
-- Q5: Average daily events per region (base frequency for premium calculation)
-- ---------------------------------------------------------------------------
SELECT
    t.region_id,
    r.display_name,
    COUNT(DISTINCT DATE(t.event_time))  AS active_days,
    COUNT(*)                            AS total_events,
    ROUND(COUNT(*) / COUNT(DISTINCT DATE(t.event_time)), 2) AS avg_events_per_day,
    ROUND(AVG(t.magnitude), 3)          AS avg_magnitude
FROM terremotos t
JOIN regiones r USING (region_id)
GROUP BY t.region_id, r.display_name
ORDER BY avg_events_per_day DESC;


-- ---------------------------------------------------------------------------
-- Q6: Events currently in quarantine pending manual review
-- ---------------------------------------------------------------------------
SELECT
    quarantine_id,
    event_id,
    pipeline_mode,
    intentos,
    motivo_rechazo,
    created_at,
    reviewed
FROM eventos_cuarentena
WHERE reviewed = 0
ORDER BY created_at ASC;


-- ---------------------------------------------------------------------------
-- Q7: Pipeline run history — last 20 executions
-- ---------------------------------------------------------------------------
SELECT
    run_id,
    mode,
    start_time,
    end_time,
    TIMESTAMPDIFF(SECOND, start_time, end_time) AS duration_seconds,
    status,
    events_extracted,
    events_loaded,
    events_discarded,
    events_quarantined,
    circuit_breaker_state,
    error_message
FROM log_ejecuciones
ORDER BY run_id DESC
LIMIT 20;


-- ---------------------------------------------------------------------------
-- Q8: Data quality issues — most common rejection reasons
-- ---------------------------------------------------------------------------
SELECT
    motivo_rechazo,
    COUNT(*)        AS occurrences,
    MIN(created_at) AS first_seen,
    MAX(created_at) AS last_seen
FROM log_calidad_datos
GROUP BY motivo_rechazo
ORDER BY occurrences DESC
LIMIT 20;


-- ---------------------------------------------------------------------------
-- Q9: Daily statistics — regions comparison for current month
-- ---------------------------------------------------------------------------
SELECT
    ed.stat_date,
    ed.region_id,
    r.display_name,
    ed.total_events,
    ed.max_magnitude,
    ROUND(ed.avg_magnitude, 3)  AS avg_magnitude,
    ROUND(ed.avg_depth_km, 1)   AS avg_depth_km,
    ed.count_strong + ed.count_major + ed.count_great AS destructive_events,
    ROUND(ed.max_risk_score, 2) AS max_risk_score
FROM estadisticas_diarias ed
JOIN regiones r USING (region_id)
WHERE ed.stat_date >= DATE_FORMAT(NOW(), '%Y-%m-01')
ORDER BY ed.stat_date DESC, ed.max_risk_score DESC;


-- ---------------------------------------------------------------------------
-- Q10: High-risk events (risk_score > 70) by region — last 180 days
-- ---------------------------------------------------------------------------
SELECT
    region_id,
    COUNT(*)                        AS high_risk_events,
    MAX(risk_score)                 AS max_risk_score,
    MAX(magnitude)                  AS max_magnitude,
    SUM(CASE WHEN tsunami=1 THEN 1 ELSE 0 END) AS tsunami_events
FROM terremotos
WHERE risk_score > 70
  AND event_time >= DATE_SUB(NOW(), INTERVAL 180 DAY)
GROUP BY region_id
ORDER BY high_risk_events DESC;
