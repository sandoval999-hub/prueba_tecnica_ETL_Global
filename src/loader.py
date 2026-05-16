"""
src/loader.py — Load phase of the seismic ETL pipeline.

Responsibilities:
  - Build and manage the SQLAlchemy connection pool
  - Execute UPSERT (INSERT … ON DUPLICATE KEY UPDATE) in batches of 200
  - Wrap each batch in an explicit transaction (BEGIN / COMMIT / ROLLBACK)
  - Load tsunami alerts, quality log, quarantine records, and run metadata
  - Recalculate daily statistics after each run
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from src.logger_config import get_logger
from src.models import PipelineRun, QuarantineRecord, SeismicEvent

logger = get_logger("loader")


class SeismicLoader:
    """
    Loads transformed seismic data into MySQL using SQLAlchemy connection pooling.

    Parameters
    ----------
    db_url : str
        SQLAlchemy connection URL (built by utils.get_db_url).
    cfg : dict
        Full pipeline configuration dict (from config.yaml).
    """

    def __init__(self, db_url: str, cfg: Dict[str, Any]) -> None:
        db_cfg = cfg.get("database", {})
        self.batch_size: int = int(cfg.get("pipeline", {}).get("batch_size", 200))

        self.engine: Engine = create_engine(
            db_url,
            pool_size=int(db_cfg.get("pool_size", 5)),
            max_overflow=int(db_cfg.get("max_overflow", 10)),
            pool_timeout=int(db_cfg.get("pool_timeout", 30)),
            pool_recycle=int(db_cfg.get("pool_recycle", 3600)),
            echo=False,
        )
        logger.info(
            "Connection pool created | pool_size=%d max_overflow=%d pool_timeout=%ds pool_recycle=%ds",
            db_cfg.get("pool_size", 5),
            db_cfg.get("max_overflow", 10),
            db_cfg.get("pool_timeout", 30),
            db_cfg.get("pool_recycle", 3600),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Public interface
    # ─────────────────────────────────────────────────────────────────────────

    def load_events(
        self,
        events: List[SeismicEvent],
        run_id: Optional[int] = None,
    ) -> int:
        """
        UPSERT SeismicEvent records into `terremotos` in batches of batch_size.
        Each batch runs inside an explicit transaction.

        Returns the number of successfully inserted/updated records.
        """
        if not events:
            logger.info("No events to load.")
            return 0

        loaded = 0
        failed_batches = 0
        batches = [
            events[i : i + self.batch_size]
            for i in range(0, len(events), self.batch_size)
        ]

        logger.info(
            "Loading %d events in %d batches of %d",
            len(events), len(batches), self.batch_size,
        )

        for batch_idx, batch in enumerate(batches, start=1):
            first_id = batch[0].event_id
            last_id = batch[-1].event_id
            try:
                self._upsert_events_batch(batch)
                loaded += len(batch)
                logger.debug(
                    "Batch %d/%d OK | events %s … %s",
                    batch_idx, len(batches), first_id, last_id,
                )
            except Exception as exc:
                failed_batches += 1
                logger.error(
                    "Batch %d/%d FAILED | events %s … %s | error: %s",
                    batch_idx, len(batches), first_id, last_id, exc,
                )
                if run_id:
                    self._log_batch_failure(
                        run_id=run_id,
                        first_event_id=first_id,
                        last_event_id=last_id,
                        error=str(exc),
                    )

        if failed_batches:
            logger.warning(
                "%d/%d batches failed. %d events loaded successfully.",
                failed_batches, len(batches), loaded,
            )
        else:
            logger.info("All %d batches committed. %d events loaded.", len(batches), loaded)

        return loaded

    def load_quality_entries(
        self,
        entries: List[Dict[str, Any]],
        run_id: Optional[int],
        pipeline_mode: str,
    ) -> None:
        """Insert discarded-event entries into log_calidad_datos."""
        if not entries:
            return
        logger.info("Logging %d quality discard entries.", len(entries))
        with self.engine.begin() as conn:
            for entry in entries:
                conn.execute(
                    text(
                        """
                        INSERT INTO log_calidad_datos
                            (run_id, event_id, motivo_rechazo, pipeline_mode, created_at)
                        VALUES
                            (:run_id, :event_id, :motivo, :mode, NOW())
                        """
                    ),
                    {
                        "run_id": run_id,
                        "event_id": entry.get("event_id"),
                        "motivo": entry.get("motivo_rechazo"),
                        "mode": pipeline_mode,
                    },
                )

    def load_quarantine(self, records: List[QuarantineRecord]) -> None:
        """Insert failed-transform records into eventos_cuarentena."""
        if not records:
            return
        logger.info("Writing %d records to quarantine.", len(records))
        with self.engine.begin() as conn:
            for rec in records:
                conn.execute(
                    text(
                        """
                        INSERT INTO eventos_cuarentena
                            (event_id, raw_json, motivo_rechazo, intentos, pipeline_mode, created_at)
                        VALUES
                            (:event_id, :raw_json, :motivo, :intentos, :mode, :created_at)
                        ON DUPLICATE KEY UPDATE
                            intentos = intentos + 1,
                            motivo_rechazo = VALUES(motivo_rechazo)
                        """
                    ),
                    {
                        "event_id": rec.event_id,
                        "raw_json": rec.raw_json,
                        "motivo": rec.rejection_reason,
                        "intentos": rec.attempts,
                        "mode": rec.pipeline_mode,
                        "created_at": rec.created_at,
                    },
                )

    def load_tsunami_alerts(self, events: List[SeismicEvent], run_id: Optional[int]) -> int:
        """Insert or update tsunami alert records for events with tsunami=1."""
        tsunami_events = [e for e in events if e.has_tsunami]
        if not tsunami_events:
            return 0

        logger.info("Loading %d tsunami alert(s).", len(tsunami_events))
        with self.engine.begin() as conn:
            for event in tsunami_events:
                conn.execute(
                    text(
                        """
                        INSERT INTO alertas_tsunami
                            (event_id, run_id, region_id, magnitude, event_time, detected_at)
                        VALUES
                            (:event_id, :run_id, :region_id, :mag, :event_time, NOW())
                        ON DUPLICATE KEY UPDATE
                            region_id    = VALUES(region_id),
                            magnitude    = VALUES(magnitude),
                            detected_at  = VALUES(detected_at)
                        """
                    ),
                    {
                        "event_id": event.event_id,
                        "run_id": run_id,
                        "region_id": event.region_id,
                        "mag": event.magnitude,
                        "event_time": event.event_time.replace(tzinfo=None),
                    },
                )
        return len(tsunami_events)

    def log_run_start(self, run: PipelineRun) -> int:
        """Insert a new pipeline run record and return the auto-generated run_id."""
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO log_ejecuciones
                        (mode, start_time, status, circuit_breaker_state)
                    VALUES
                        (:mode, :start_time, :status, :cb_state)
                    """
                ),
                {
                    "mode": run.mode,
                    "start_time": run.start_time.replace(tzinfo=None),
                    "status": run.status,
                    "cb_state": run.circuit_breaker_state,
                },
            )
            run_id = result.lastrowid
        logger.info("Run started | run_id=%d mode=%s", run_id, run.mode)
        return run_id

    def log_run_end(self, run_id: int, run: PipelineRun) -> None:
        """Update the pipeline run record with final metrics."""
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE log_ejecuciones
                    SET
                        end_time             = :end_time,
                        status               = :status,
                        regions_processed    = :regions,
                        events_extracted     = :extracted,
                        events_loaded        = :loaded,
                        events_discarded     = :discarded,
                        events_quarantined   = :quarantined,
                        circuit_breaker_state= :cb_state,
                        error_message        = :error
                    WHERE run_id = :run_id
                    """
                ),
                {
                    "end_time": run.end_time.replace(tzinfo=None) if run.end_time else None,
                    "status": run.status,
                    "regions": run.regions_processed,
                    "extracted": run.events_extracted,
                    "loaded": run.events_loaded,
                    "discarded": run.events_discarded,
                    "quarantined": run.events_quarantined,
                    "cb_state": run.circuit_breaker_state,
                    "error": run.error_message,
                    "run_id": run_id,
                },
            )
        logger.info(
            "Run %d finished | status=%s | loaded=%d | discarded=%d | quarantined=%d",
            run_id, run.status, run.events_loaded, run.events_discarded, run.events_quarantined,
        )

    def dispose(self) -> None:
        """Close all connections in the pool (call at pipeline end)."""
        self.engine.dispose()
        logger.debug("Connection pool disposed.")

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _upsert_events_batch(self, batch: List[SeismicEvent]) -> None:
        """
        Execute UPSERT for a single batch within an explicit transaction.
        On failure the transaction is rolled back automatically (engine.begin context).
        """
        upsert_sql = text(
            """
            INSERT INTO terremotos (
                event_id, magnitude, magnitude_class, place,
                event_time, updated_time, latitude, longitude,
                depth_km, depth_class, energy_joules, risk_score,
                region_id, felt, cdi, mmi, alert_level,
                tsunami, significance, net, mag_type, status
            ) VALUES (
                :event_id, :magnitude, :magnitude_class, :place,
                :event_time, :updated_time, :latitude, :longitude,
                :depth_km, :depth_class, :energy_joules, :risk_score,
                :region_id, :felt, :cdi, :mmi, :alert_level,
                :tsunami, :significance, :net, :mag_type, :status
            )
            ON DUPLICATE KEY UPDATE
                magnitude       = VALUES(magnitude),
                magnitude_class = VALUES(magnitude_class),
                place           = VALUES(place),
                updated_time    = VALUES(updated_time),
                depth_km        = VALUES(depth_km),
                depth_class     = VALUES(depth_class),
                energy_joules   = VALUES(energy_joules),
                risk_score      = VALUES(risk_score),
                region_id       = VALUES(region_id),
                felt            = VALUES(felt),
                cdi             = VALUES(cdi),
                mmi             = VALUES(mmi),
                alert_level     = VALUES(alert_level),
                tsunami         = VALUES(tsunami),
                significance    = VALUES(significance),
                status          = VALUES(status)
            """
        )

        rows = [
            {
                "event_id": e.event_id,
                "magnitude": e.magnitude,
                "magnitude_class": e.magnitude_class,
                "place": e.place,
                "event_time": e.event_time.replace(tzinfo=None),
                "updated_time": e.updated_time.replace(tzinfo=None) if e.updated_time else None,
                "latitude": e.latitude,
                "longitude": e.longitude,
                "depth_km": e.depth_km,
                "depth_class": e.depth_class,
                "energy_joules": e.energy_joules,
                "risk_score": e.risk_score,
                "region_id": e.region_id,
                "felt": e.felt,
                "cdi": e.cdi,
                "mmi": e.mmi,
                "alert_level": e.alert_level,
                "tsunami": e.tsunami,
                "significance": e.significance,
                "net": e.net,
                "mag_type": e.mag_type,
                "status": e.status,
            }
            for e in batch
        ]

        with self.engine.begin() as conn:
            conn.execute(upsert_sql, rows)

    def _log_batch_failure(
        self,
        run_id: int,
        first_event_id: str,
        last_event_id: str,
        error: str,
    ) -> None:
        """Record a failed batch in log_calidad_datos."""
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO log_calidad_datos
                            (run_id, event_id, motivo_rechazo, pipeline_mode, created_at)
                        VALUES
                            (:run_id, :event_id, :motivo, 'batch_failure', NOW())
                        """
                    ),
                    {
                        "run_id": run_id,
                        "event_id": f"{first_event_id}..{last_event_id}",
                        "motivo": f"Batch transaction failed: {error}",
                    },
                )
        except Exception as inner:
            logger.error("Could not log batch failure to DB: %s", inner)
