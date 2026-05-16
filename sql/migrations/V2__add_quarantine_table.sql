-- ===========================================================================
-- V2__add_quarantine_table.sql
-- Atlas RA Seismic ETL Pipeline — Add Quarantine Table
-- MySQL 8.0+
-- ===========================================================================
-- This migration adds the eventos_cuarentena table for records that fail
-- transformation after multiple attempts. These records are held for
-- manual review by the Data Quality team.

CREATE TABLE IF NOT EXISTS eventos_cuarentena (
    quarantine_id   BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
    event_id        VARCHAR(50)      NOT NULL  COMMENT 'USGS event ID (may be malformed)',
    raw_json        MEDIUMTEXT       NOT NULL  COMMENT 'Original raw JSON from USGS API',
    motivo_rechazo  VARCHAR(1024)    NOT NULL  COMMENT 'Transformation error description',
    intentos        TINYINT UNSIGNED NOT NULL  DEFAULT 1  COMMENT 'Number of processing attempts',
    pipeline_mode   VARCHAR(20)      NOT NULL  COMMENT 'daily | alert | historical',
    created_at      DATETIME         NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    reviewed        TINYINT(1)       NOT NULL  DEFAULT 0  COMMENT '1 = reviewed by Data Quality',
    reviewer_notes  TEXT,
    reviewed_at     DATETIME,

    PRIMARY KEY (quarantine_id),
    UNIQUE KEY uq_quarantine_event (event_id),
    INDEX idx_quarantine_created   (created_at),
    INDEX idx_quarantine_reviewed  (reviewed),
    INDEX idx_quarantine_mode      (pipeline_mode)

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Poison pill table: events that failed transformation are held for manual DQ review';
