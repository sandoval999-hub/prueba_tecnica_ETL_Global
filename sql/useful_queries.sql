-- ===========================================================================
-- useful_queries.sql — Consultas de referencia para actuarios y analistas
-- Atlas RA Seismic ETL Pipeline
-- ===========================================================================

USE seismic_db;

/*
 * Q1: Terremotos con magnitud >= 6.0 en la región 'japan' en los últimos 90 días.
 * Propósito (Actuariado): Identificar eventos altamente destructivos recientes 
 * en una zona de alto riesgo para estimar posible exposición a reclamos.
 * -- Ajustar 90 para cambiar el período (ej. INTERVAL 30 DAY).
 */
SELECT
    event_id,
    magnitude,
    magnitude_class,
    place,
    event_time,
    depth_km,
    risk_score
FROM terremotos
WHERE region_id = 'japan'
  AND magnitude >= 6.0
  AND event_time >= DATE_SUB(NOW(), INTERVAL 90 DAY)
ORDER BY event_time DESC;


/*
 * Q2: Región con mayor risk_score promedio en el mes actual.
 * Propósito (Actuariado): Detectar la región que representa el mayor riesgo 
 * promedio en el mes en curso para ajustar modelos de primas a corto plazo.
 */
SELECT
    t.region_id,
    ROUND(AVG(t.risk_score), 2) AS avg_risk_score,
    COUNT(t.event_id)           AS total_events,
    MAX(t.magnitude)            AS max_magnitude
FROM terremotos t
WHERE t.event_time >= DATE_FORMAT(NOW(), '%Y-%m-01')
GROUP BY t.region_id
ORDER BY avg_risk_score DESC
LIMIT 1;


/*
 * Q3: Total de alertas de tsunami por región en el último año.
 * Propósito (Actuariado): Evaluar la frecuencia histórica reciente de tsunamis 
 * por región para calcular reservas de capital catastrófico marino.
 * -- Ajustar 1 YEAR a 5 YEAR para análisis a más largo plazo.
 */
SELECT
    a.region_id,
    r.display_name,
    COUNT(a.event_id)  AS total_tsunami_alerts,
    ROUND(AVG(a.magnitude), 2) AS avg_magnitude,
    MAX(a.magnitude)   AS max_magnitude
FROM alertas_tsunami a
JOIN regiones r ON a.region_id = r.region_id
WHERE a.detected_at >= DATE_SUB(NOW(), INTERVAL 1 YEAR)
GROUP BY a.region_id, r.display_name
ORDER BY total_tsunami_alerts DESC;


/*
 * Q4: Top 10 eventos más significativos de los últimos 30 días globalmente.
 * Propósito (Actuariado): Analizar los 10 eventos recientes con mayor índice 
 * 'significance' (USGS) para revisión manual de impacto en pólizas globales.
 */
SELECT
    event_id,
    magnitude,
    place,
    event_time,
    significance,
    risk_score,
    region_id,
    alert_level
FROM terremotos
WHERE event_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
ORDER BY significance DESC
LIMIT 10;


/*
 * Q5: Promedio de eventos diarios por región.
 * Propósito (Actuariado): Frecuencia base para modelos estocásticos de simulación 
 * de eventos, útil para el cálculo de primas actuariales base por territorio.
 */
SELECT
    region_id,
    ROUND(AVG(total_events), 2) AS avg_daily_events,
    COUNT(stat_date)            AS total_days_with_data,
    ROUND(AVG(max_magnitude), 2) AS avg_daily_max_magnitude
FROM estadisticas_diarias
GROUP BY region_id
ORDER BY avg_daily_events DESC;


/*
 * Q6: Eventos en cuarentena pendientes de revisión manual con más de 24 hrs.
 * Propósito (Actuariado): Monitoreo de Data Quality; eventos "poison pill" que 
 * llevan estancados más de un día, afectando la precisión del modelo en tiempo real.
 */
SELECT
    event_id,
    motivo_rechazo,
    intentos,
    pipeline_mode,
    created_at,
    TIMESTAMPDIFF(HOUR, created_at, NOW()) AS horas_sin_revisar
FROM eventos_cuarentena
WHERE reviewed = 0
  AND TIMESTAMPDIFF(HOUR, created_at, NOW()) > 24
ORDER BY intentos DESC, created_at ASC;
