"""
src/models.py — Pydantic v2 models for USGS API response validation
and internal dataclasses for the seismic ETL pipeline.

These models protect the pipeline from silent KeyError / AttributeError
caused by undocumented API changes.  If the USGS schema changes, we get
a clear ValidationError immediately at the Extract phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ─────────────────────────────────────────────
# Pydantic v2 — USGS GeoJSON response models
# ─────────────────────────────────────────────

class USGSGeometry(BaseModel):
    """Geometry block: coordinates[0]=lon, [1]=lat, [2]=depth_km"""

    type: str
    coordinates: List[float] = Field(..., min_length=3)

    @field_validator("coordinates")
    @classmethod
    def validate_coordinates(cls, v: List[float]) -> List[float]:
        if len(v) < 3:
            raise ValueError(
                f"coordinates must have at least 3 elements (lon, lat, depth), got {len(v)}"
            )
        lon, lat = v[0], v[1]
        if not (-180.0 <= lon <= 180.0):
            raise ValueError(f"longitude {lon} out of range [-180, 180]")
        if not (-90.0 <= lat <= 90.0):
            raise ValueError(f"latitude {lat} out of range [-90, 90]")
        return v


class USGSProperties(BaseModel):
    """Properties block of a USGS Feature — non-critical fields are Optional."""

    mag: Optional[float] = None
    place: Optional[str] = None
    time: Optional[int] = None            # UNIX ms
    updated: Optional[int] = None         # UNIX ms
    tz: Optional[int] = None
    url: Optional[str] = None
    detail: Optional[str] = None
    felt: Optional[int] = None
    cdi: Optional[float] = None
    mmi: Optional[float] = None
    alert: Optional[str] = None           # green | yellow | orange | red
    status: Optional[str] = None          # automatic | reviewed | deleted
    tsunami: Optional[int] = None
    sig: Optional[int] = None
    net: Optional[str] = None
    code: Optional[str] = None
    nst: Optional[int] = None
    dmin: Optional[float] = None
    rms: Optional[float] = None
    gap: Optional[float] = None
    magType: Optional[str] = None
    type: Optional[str] = None            # earthquake | quarry blast | explosion …


class USGSFeature(BaseModel):
    """Single GeoJSON Feature representing one seismic event."""

    type: str
    properties: USGSProperties
    geometry: USGSGeometry
    id: str

    @model_validator(mode="after")
    def validate_event_id(self) -> "USGSFeature":
        if not self.id:
            raise ValueError("Event id must not be empty")
        return self


class USGSMetadata(BaseModel):
    """Metadata block at the root of the GeoJSON FeatureCollection."""

    generated: Optional[int] = None
    url: Optional[str] = None
    title: Optional[str] = None
    status: Optional[int] = None
    api: Optional[str] = None
    count: Optional[int] = None


class USGSResponse(BaseModel):
    """Root GeoJSON FeatureCollection returned by the USGS API."""

    type: str
    metadata: USGSMetadata
    features: List[USGSFeature]
    bbox: Optional[List[float]] = None


# ─────────────────────────────────────────────
# Internal dataclasses — post-transform records
# ─────────────────────────────────────────────

@dataclass
class SeismicEvent:
    """Fully transformed seismic event ready to be loaded into MySQL."""

    event_id: str
    magnitude: float
    magnitude_class: str
    place: str
    event_time: datetime
    updated_time: Optional[datetime]
    latitude: float
    longitude: float
    depth_km: float
    depth_class: str
    energy_joules: float
    risk_score: float
    region_id: str
    felt: Optional[int]
    cdi: Optional[float]
    mmi: Optional[float]
    alert_level: Optional[str]
    tsunami: int
    significance: int
    net: Optional[str]
    mag_type: Optional[str]
    status: Optional[str]
    raw_place: Optional[str]

    # ---------- convenience helpers ----------
    @property
    def has_tsunami(self) -> bool:
        return self.tsunami == 1


@dataclass
class QuarantineRecord:
    """Record that failed transformation and must be reviewed manually."""

    event_id: str
    raw_json: str
    rejection_reason: str
    attempts: int
    pipeline_mode: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class QualityEntry:
    """Record for events discarded during quality validation (written to log_calidad_datos).

    Attributes:
        event_id: USGS event identifier.
        motivo_rechazo: Reason the event was discarded.
        pipeline_mode: Pipeline mode when event was discarded (daily|alert|historical).
        created_at: Timestamp of the quality entry creation.
    """

    event_id: str
    motivo_rechazo: str
    pipeline_mode: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PipelineRun:
    """Metadata for a single pipeline execution (written to log_ejecuciones)."""

    run_id: Optional[int]
    mode: str
    start_time: datetime
    end_time: Optional[datetime]
    status: str                        # running | success | error
    regions_processed: int
    events_extracted: int
    events_loaded: int
    events_discarded: int
    events_quarantined: int
    circuit_breaker_state: str         # closed | open | half-open
    error_message: Optional[str]


@dataclass
class RegionConfig:
    """Configuration for a single monitoring region."""

    region_id: str
    display_name: str
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def contains(self, lat: float, lon: float) -> bool:
        """Return True if the given coordinates fall inside this region's bounding box."""
        return (
            self.min_lat <= lat <= self.max_lat
            and self.min_lon <= lon <= self.max_lon
        )
