"""
src/transformer.py — Transform phase of the seismic ETL pipeline.

Responsibilities:
  - Convert UNIX ms timestamps → UTC datetime
  - Classify magnitude (Richter scale)
  - Classify depth (shallow / intermediate / deep)
  - Calculate energy released (Gutenberg-Richter formula)
  - Calculate proprietary Atlas RA risk_score (0–100)
  - Assign monitoring region from bounding boxes
  - Validate and discard invalid records
  - Handle Quarantine logic for records that fail transformation
  - Deduplicate by USGS event_id
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.logger_config import get_logger
from src.models import QuarantineRecord, RegionConfig, SeismicEvent, USGSFeature

logger = get_logger("transformer")


# ─────────────────────────────────────────────────────────────────────────────
# Pure classification functions (also tested individually)
# ─────────────────────────────────────────────────────────────────────────────

def classify_magnitude(mag: float) -> str:
    """
    Return the Richter-scale magnitude class for a given magnitude.

    ┌────────────┬──────────┐
    │ Magnitude  │ Class    │
    ├────────────┼──────────┤
    │ < 2.0      │ micro    │
    │ 2.0 – 3.9  │ minor    │
    │ 4.0 – 4.9  │ light    │
    │ 5.0 – 5.9  │ moderate │
    │ 6.0 – 6.9  │ strong   │
    │ 7.0 – 7.9  │ major    │
    │ ≥ 8.0      │ great    │
    └────────────┴──────────┘
    """
    if mag < 2.0:
        return "micro"
    elif mag < 4.0:
        return "minor"
    elif mag < 5.0:
        return "light"
    elif mag < 6.0:
        return "moderate"
    elif mag < 7.0:
        return "strong"
    elif mag < 8.0:
        return "major"
    else:
        return "great"


def classify_depth(depth_km: float) -> str:
    """
    Return the depth classification for a given depth in km.

    ┌──────────────┬──────────────┐
    │ Depth (km)   │ Class        │
    ├──────────────┼──────────────┤
    │ 0 – 70       │ shallow      │
    │ 70 – 300     │ intermediate │
    │ > 300        │ deep         │
    └──────────────┴──────────────┘
    """
    if depth_km <= 70:
        return "shallow"
    elif depth_km <= 300:
        return "intermediate"
    else:
        return "deep"


def calculate_energy_joules(magnitude: float) -> float:
    """
    Estimate energy released using the Gutenberg-Richter formula:
        log10(E) = 1.5 * M + 4.8
        E = 10^(1.5 * M + 4.8)

    Returns energy in joules.
    """
    exponent = 1.5 * magnitude + 4.8
    return math.pow(10, exponent)


def calculate_risk_score(
    magnitude: float,
    depth_km: float,
    significance: int,
    felt: Optional[int],
    max_depth_km: float = 700.0,
    max_significance: float = 1000.0,
    max_felt: float = 100.0,
) -> float:
    """
    Compute the Atlas RA proprietary risk score (0–100).

    Formula:
        risk_score = (mag_norm * 0.40) + (depth_norm * 0.25)
                   + (sig_norm * 0.20) + (pop_proxy * 0.15)

    Where:
        mag_norm   = min((magnitude / 10) * 100, 100)
        depth_norm = max(0, (1 - depth_km / max_depth_km)) * 100
        sig_norm   = min(significance / max_significance * 100, 100)
        pop_proxy  = 0 if felt is None else min(felt / max_felt * 100, 100)
    """
    mag_norm = min((magnitude / 10.0) * 100.0, 100.0)
    depth_norm = max(0.0, (1.0 - depth_km / max_depth_km)) * 100.0
    sig_norm = min((significance / max_significance) * 100.0, 100.0)
    pop_proxy = 0.0 if felt is None else min((felt / max_felt) * 100.0, 100.0)

    score = (
        mag_norm * 0.40
        + depth_norm * 0.25
        + sig_norm * 0.20
        + pop_proxy * 0.15
    )
    return round(min(max(score, 0.0), 100.0), 4)


def assign_region(
    latitude: float,
    longitude: float,
    regions: List[RegionConfig],
) -> str:
    """
    Return the region_id of the first region whose bounding box contains (lat, lon).
    Returns 'global_other' if no region matches (only possible in alert mode).
    """
    for region in regions:
        if region.contains(latitude, longitude):
            return region.region_id
    return "global_other"


def ms_to_utc(timestamp_ms: int) -> datetime:
    """Convert a UNIX timestamp in milliseconds to an aware UTC datetime."""
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Main transformer class
# ─────────────────────────────────────────────────────────────────────────────

class SeismicTransformer:
    """
    Transforms raw USGSFeature objects into validated SeismicEvent dataclasses.

    Invalid records are either discarded (logged to quality_log) or sent to
    quarantine (if the failure is unexpected / code error).
    """

    def __init__(self, cfg: Dict[str, Any], regions: List[RegionConfig]) -> None:
        self.regions = regions

        rs_cfg = cfg.get("risk_score", {})
        self.max_depth_km: float = float(rs_cfg.get("max_depth_km", 700.0))
        self.max_significance: float = float(rs_cfg.get("max_significance", 1000.0))
        self.max_felt: float = float(rs_cfg.get("max_felt_reports", 100.0))

        self.pipeline_mode: str = "unknown"  # set by caller before transform()

    def transform(
        self,
        features: List[USGSFeature],
        pipeline_mode: str = "daily",
    ) -> Tuple[List[SeismicEvent], List[Dict[str, Any]], List[QuarantineRecord]]:
        """
        Transform a list of USGSFeature into (events, quality_log_entries, quarantine_records).

        Parameters
        ----------
        features       : Raw extracted features (already filtered for type=earthquake)
        pipeline_mode  : 'daily' | 'alert' | 'historical'

        Returns
        -------
        events          : Valid, transformed SeismicEvent list
        quality_entries : List of dicts for log_calidad_datos (discarded records)
        quarantine      : List of QuarantineRecord (transformation errors)
        """
        self.pipeline_mode = pipeline_mode
        seen_ids: set = set()
        events: List[SeismicEvent] = []
        quality_entries: List[Dict[str, Any]] = []
        quarantine_records: List[QuarantineRecord] = []

        for feature in features:
            event_id = feature.id

            # ── Deduplication ────────────────────────────────────────────────
            if event_id in seen_ids:
                logger.debug("Duplicate event %s — skipping", event_id)
                quality_entries.append(self._quality_entry(event_id, "duplicate"))
                continue
            seen_ids.add(event_id)

            # ── Try to transform; quarantine on unexpected errors ─────────────
            try:
                result = self._transform_one(feature, quality_entries)
                if result is not None:
                    events.append(result)
            except Exception as exc:
                logger.error(
                    "Unexpected error transforming event %s: %s. Sending to quarantine.",
                    event_id, exc,
                )
                quarantine_records.append(
                    QuarantineRecord(
                        event_id=event_id,
                        raw_json=json.dumps(feature.model_dump(), default=str),
                        rejection_reason=f"{type(exc).__name__}: {exc}",
                        attempts=1,
                        pipeline_mode=pipeline_mode,
                    )
                )

        logger.info(
            "Transform complete | mode=%s | valid=%d | discarded=%d | quarantined=%d",
            pipeline_mode,
            len(events),
            len(quality_entries),
            len(quarantine_records),
        )
        return events, quality_entries, quarantine_records

    # ─────────────────────────────────────────────────────────────────────────
    # Per-event transformation logic
    # ─────────────────────────────────────────────────────────────────────────

    def _transform_one(
        self,
        feature: USGSFeature,
        quality_entries: List[Dict[str, Any]],
    ) -> Optional[SeismicEvent]:
        """
        Transform a single USGSFeature.
        Returns None (and appends to quality_entries) if the record is discarded.
        Raises on truly unexpected errors so the caller can quarantine it.
        """
        props = feature.properties
        geom = feature.geometry
        event_id = feature.id

        # ── Discard deleted events ────────────────────────────────────────────
        if (props.status or "").lower() == "deleted":
            quality_entries.append(self._quality_entry(event_id, "status=deleted"))
            return None

        # ── Validate magnitude ────────────────────────────────────────────────
        if props.mag is None or props.mag < 0:
            quality_entries.append(
                self._quality_entry(event_id, f"invalid magnitude: {props.mag}")
            )
            return None

        # ── Extract coordinates (lon=0, lat=1, depth=2) ───────────────────────
        lon = geom.coordinates[0]
        lat = geom.coordinates[1]
        depth_km = geom.coordinates[2]

        # ── Validate coordinates ──────────────────────────────────────────────
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            quality_entries.append(
                self._quality_entry(event_id, f"invalid coords: lat={lat} lon={lon}")
            )
            return None

        # ── Validate depth ────────────────────────────────────────────────────
        if depth_km < 0:
            quality_entries.append(
                self._quality_entry(event_id, f"negative depth: {depth_km}")
            )
            return None

        # ── Validate timestamp ────────────────────────────────────────────────
        if props.time is None:
            quality_entries.append(self._quality_entry(event_id, "missing timestamp"))
            return None

        # ── Conversions & enrichment ──────────────────────────────────────────
        event_time = ms_to_utc(props.time)
        updated_time = ms_to_utc(props.updated) if props.updated else None
        significance = props.sig or 0

        mag_class = classify_magnitude(props.mag)
        depth_class = classify_depth(depth_km)
        energy = calculate_energy_joules(props.mag)
        risk = calculate_risk_score(
            magnitude=props.mag,
            depth_km=depth_km,
            significance=significance,
            felt=props.felt,
            max_depth_km=self.max_depth_km,
            max_significance=self.max_significance,
            max_felt=self.max_felt,
        )
        region_id = assign_region(lat, lon, self.regions)

        return SeismicEvent(
            event_id=event_id,
            magnitude=props.mag,
            magnitude_class=mag_class,
            place=props.place or "",
            event_time=event_time,
            updated_time=updated_time,
            latitude=lat,
            longitude=lon,
            depth_km=depth_km,
            depth_class=depth_class,
            energy_joules=energy,
            risk_score=risk,
            region_id=region_id,
            felt=props.felt,
            cdi=props.cdi,
            mmi=props.mmi,
            alert_level=props.alert,
            tsunami=props.tsunami or 0,
            significance=significance,
            net=props.net,
            mag_type=props.magType,
            status=props.status,
            raw_place=props.place,
        )

    @staticmethod
    def _quality_entry(event_id: str, reason: str) -> Dict[str, Any]:
        return {
            "event_id": event_id,
            "motivo_rechazo": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
