-- ===========================================================================
-- V1__init_schema.sql
-- Atlas RA Seismic ETL Pipeline — Initial Schema
-- MySQL 8.0+
-- ===========================================================================

-- Ensure we are in the right database
-- (init_db.sql handles USE before calling this migration)

-- ---------------------------------------------------------------------------
-- 1. regiones — monitoring region catalogue
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS regiones (
    region_id      VARCHAR(50)    NOT NULL,
    display_name   VARCHAR(120)   NOT NULL,
    min_lat        DECIMAL(8,4)   NOT NULL,
    max_lat        DECIMAL(8,4)   NOT NULL,
    min_lon        DECIMAL(9,4)   NOT NULL,
    max_lon        DECIMAL(9,4)   NOT NULL,
    created_at     DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (region_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Catalogue of the 8 seismic monitoring regions + global_other';

-- Seed the 8 monitoring regions
INSERT INTO regiones (region_id, display_name, min_lat, max_lat, min_lon, max_lon) VALUES
    ('pacific_northwest', 'Pacífico Noroeste (EE.UU.)',             41.0, 49.0, -130.0, -116.0),
    ('california',        'California',                              32.0, 42.0, -125.0, -114.0),
    ('japan',             'Japón',                                   30.0, 46.0,  128.0,  146.0),
    ('indonesia',         'Indonesia',                              -11.0,  6.0,   95.0,  141.0),
    ('south_america_west','Chile – Perú',                           -56.0, -5.0,  -82.0,  -66.0),
    ('mediterranean',     'Mediterráneo (Turquía – Grecia – Italia)', 34.0, 45.0,   10.0,   45.0),
    ('himalaya',          'Himalaya (Nepal – India)',                24.0, 36.0,   72.0,   96.0),
    ('new_zealand',       'Nueva Zelanda',                          -50.0,-34.0,  165.0,  180.0),
    ('global_other',      'Global – Other (Alert Mode)',            -90.0, 90.0, -180.0,  180.0)
ON DUPLICATE KEY UPDATE
    display_name = VALUES(display_name),
    min_lat = VALUES(min_lat), max_lat = VALUES(max_lat),
    min_lon = VALUES(min_lon), max_lon = VALUES(max_lon);


-- ---------------------------------------------------------------------------
-- 2. terremotos — main seismic event table (1 row per USGS event)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS terremotos (
    event_id        VARCHAR(20)       NOT NULL  COMMENT 'USGS unique event ID (e.g. us7000m4vf)',
    magnitude       DECIMAL(4,2)      NOT NULL,
    magnitude_class ENUM('micro','minor','light','moderate','strong','major','great') NOT NULL,
    place           VARCHAR(255)      NOT NULL  DEFAULT '',
    event_time      DATETIME          NOT NULL  COMMENT 'UTC event time',
    updated_time    DATETIME                    COMMENT 'UTC last updated by USGS',
    latitude        DECIMAL(9,6)      NOT NULL,
    longitude       DECIMAL(10,6)     NOT NULL,
    depth_km        DECIMAL(8,3)      NOT NULL,
    depth_class     ENUM('shallow','intermediate','deep') NOT NULL,
    energy_joules   DOUBLE            NOT NULL  COMMENT 'Gutenberg-Richter E = 10^(1.5M + 4.8)',
    risk_score      DECIMAL(6,4)      NOT NULL  COMMENT 'Atlas RA proprietary risk score 0–100',
    region_id       VARCHAR(50)       NOT NULL,
    felt            INT UNSIGNED               COMMENT 'Community "felt it" reports (nullable)',
    cdi             DECIMAL(4,2)               COMMENT 'Community Decimal Intensity (nullable)',
    mmi             DECIMAL(4,2)               COMMENT 'Modified Mercalli Intensity (nullable)',
    alert_level     ENUM('green','yellow','orange','red') COMMENT 'PAGER alert level (nullable)',
    tsunami         TINYINT(1)        NOT NULL  DEFAULT 0,
    significance    SMALLINT UNSIGNED NOT NULL  DEFAULT 0 COMMENT 'USGS sig field (0–1000+)',
    net             VARCHAR(10)                COMMENT 'Contributing network code',
    mag_type        VARCHAR(10)                COMMENT 'Magnitude type (ml, mb, mww…)',
    status          VARCHAR(20)                COMMENT 'automatic | reviewed | deleted',
    created_at      DATETIME          NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME          NOT NULL  DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (event_id),
    CONSTRAINT fk_terremotos_region FOREIGN KEY (region_id)
        REFERENCES regiones(region_id) ON UPDATE CASCADE,

    -- Frequently queried columns get their own indexes
    INDEX idx_event_time     (event_time),
    INDEX idx_magnitude      (magnitude),
    INDEX idx_region         (region_id),
    INDEX idx_risk_score     (risk_score DESC),
    INDEX idx_tsunami        (tsunami),
    INDEX idx_alert_level    (alert_level),
    INDEX idx_region_date    (region_id, event_time)  COMMENT 'Composite for region+date queries'

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Main seismic event table. PK = USGS event_id. UPSERT-idempotent.';


-- ---------------------------------------------------------------------------
-- 3. alertas_tsunami — separate table for tsunami-flagged events
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alertas_tsunami (
    alert_id        BIGINT UNSIGNED   NOT NULL AUTO_INCREMENT,
    event_id        VARCHAR(20)       NOT NULL,
    run_id          INT UNSIGNED,
    region_id       VARCHAR(50)       NOT NULL,
    magnitude       DECIMAL(4,2)      NOT NULL,
    event_time      DATETIME          NOT NULL,
    detected_at     DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (alert_id),
    UNIQUE KEY uq_tsunami_event (event_id),
    INDEX idx_tsunami_region  (region_id),
    INDEX idx_tsunami_detected(detected_at)

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Tsunami-flagged events detected by the pipeline';


-- ---------------------------------------------------------------------------
-- 4. estadisticas_diarias — daily aggregates per region (recalculated each run)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS estadisticas_diarias (
    region_id       VARCHAR(50)       NOT NULL,
    stat_date       DATE              NOT NULL,
    total_events    INT UNSIGNED      NOT NULL DEFAULT 0,
    max_magnitude   DECIMAL(4,2),
    avg_magnitude   DECIMAL(5,3),
    avg_depth_km    DECIMAL(8,3),
    count_micro     INT UNSIGNED      NOT NULL DEFAULT 0,
    count_minor     INT UNSIGNED      NOT NULL DEFAULT 0,
    count_light     INT UNSIGNED      NOT NULL DEFAULT 0,
    count_moderate  INT UNSIGNED      NOT NULL DEFAULT 0,
    count_strong    INT UNSIGNED      NOT NULL DEFAULT 0,
    count_major     INT UNSIGNED      NOT NULL DEFAULT 0,
    count_great     INT UNSIGNED      NOT NULL DEFAULT 0,
    max_risk_score  DECIMAL(6,4),
    updated_at      DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (region_id, stat_date),
    INDEX idx_stat_date (stat_date)

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Daily aggregates per region. Recalculated after every pipeline run.';


-- ---------------------------------------------------------------------------
-- 5. log_ejecuciones — pipeline run audit log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS log_ejecuciones (
    run_id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    mode                  ENUM('daily','alert','historical') NOT NULL,
    start_time            DATETIME        NOT NULL,
    end_time              DATETIME,
    status                ENUM('running','success','error') NOT NULL DEFAULT 'running',
    regions_processed     SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    events_extracted      INT UNSIGNED    NOT NULL DEFAULT 0,
    events_loaded         INT UNSIGNED    NOT NULL DEFAULT 0,
    events_discarded      INT UNSIGNED    NOT NULL DEFAULT 0,
    events_quarantined    INT UNSIGNED    NOT NULL DEFAULT 0,
    circuit_breaker_state ENUM('closed','open','half-open') NOT NULL DEFAULT 'closed',
    error_message         TEXT,

    PRIMARY KEY (run_id),
    INDEX idx_run_start (start_time),
    INDEX idx_run_status(status)

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Audit log for every pipeline execution';


-- ---------------------------------------------------------------------------
-- 6. log_calidad_datos — discarded record quality log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS log_calidad_datos (
    log_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id          INT UNSIGNED,
    event_id        VARCHAR(50)     NOT NULL,
    motivo_rechazo  VARCHAR(512)    NOT NULL,
    pipeline_mode   VARCHAR(20),
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (log_id),
    INDEX idx_quality_run  (run_id),
    INDEX idx_quality_event(event_id)

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Records discarded during validation with rejection reason';
