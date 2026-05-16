"""
tests/conftest.py — Shared pytest fixtures for the seismic ETL test suite.

All fixtures in this file are available to every test file without import.

Provides:
  - Region configurations (japan, california, indonesia, all_regions)
  - Pipeline configuration dict (sample_cfg)
  - USGS API response loaded from JSON fixture (sample_api_response)
  - Pre-parsed USGSFeature lists (sample_usgs_features, earthquake_features)
  - Pre-built SeismicEvent instances (sample_events)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.models import RegionConfig, SeismicEvent


# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ─────────────────────────────────────────────────────────────────────────────
# Shared region configs
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def japan_region() -> RegionConfig:
    """Bounding box for Japan (Cinturón del Pacífico)."""
    return RegionConfig(
        region_id="japan",
        display_name="Japón",
        min_lat=30.0,
        max_lat=46.0,
        min_lon=128.0,
        max_lon=146.0,
    )


@pytest.fixture
def california_region() -> RegionConfig:
    """Bounding box for California (Falla de San Andrés)."""
    return RegionConfig(
        region_id="california",
        display_name="California",
        min_lat=32.0,
        max_lat=42.0,
        min_lon=-125.0,
        max_lon=-114.0,
    )


@pytest.fixture
def indonesia_region() -> RegionConfig:
    """Bounding box for Indonesia (Archipiélago de Sunda)."""
    return RegionConfig(
        region_id="indonesia",
        display_name="Indonesia",
        min_lat=-11.0,
        max_lat=6.0,
        min_lon=95.0,
        max_lon=141.0,
    )


@pytest.fixture
def all_regions(
    japan_region,
    california_region,
    indonesia_region,
) -> List[RegionConfig]:
    """All 8 monitoring regions for comprehensive testing."""
    return [
        RegionConfig("pacific_northwest", "Pacífico Noroeste", 41.0, 49.0, -130.0, -116.0),
        california_region,
        japan_region,
        indonesia_region,
        RegionConfig("south_america_west", "Chile-Perú",       -56.0, -5.0,  -82.0, -66.0),
        RegionConfig("mediterranean",      "Mediterráneo",       34.0, 45.0,   10.0,  45.0),
        RegionConfig("himalaya",           "Himalaya",           24.0, 36.0,   72.0,  96.0),
        RegionConfig("new_zealand",        "Nueva Zelanda",     -50.0,-34.0,  165.0, 180.0),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Sample config (mirrors config.yaml structure)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_cfg() -> Dict[str, Any]:
    """Minimal valid pipeline config with all required sections."""
    return {
        "api": {
            "base_url": "https://earthquake.usgs.gov/fdsnws/event/1/query",
            "rate_limit_seconds": 0.0,
            "request_timeout_seconds": 10,
            "max_retries": 1,
            "retry_backoff_seconds": 0,
            "max_segment_days": 30,
            "result_limit": 20000,
        },
        "pipeline": {
            "batch_size": 200,
            "daily_lookback_hours": 24,
            "alert_lookback_hours": 1,
            "daily_min_magnitude": 1.0,
            "alert_min_magnitude": 4.5,
            "historical_min_magnitude": 2.5,
        },
        "risk_score": {
            "max_depth_km": 700.0,
            "max_significance": 1000.0,
            "max_felt_reports": 100.0,
            "high_risk_threshold": 70,
        },
        "database": {
            "pool_size": 2,
            "max_overflow": 2,
            "pool_timeout": 5,
            "pool_recycle": 3600,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Sample API response fixture (loaded from JSON file, no HTTP calls)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_api_response() -> Dict[str, Any]:
    """Load the complete USGS GeoJSON fixture from disk."""
    response_path = FIXTURES_DIR / "sample_response.json"
    with open(response_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def sample_usgs_features(sample_api_response):
    """Return validated USGSFeature list from the sample JSON."""
    from src.models import USGSResponse
    parsed = USGSResponse.model_validate(sample_api_response)
    return parsed.features


@pytest.fixture
def earthquake_features(sample_usgs_features):
    """Only the earthquake-type features from the sample (filter quarry blast)."""
    return [
        f for f in sample_usgs_features
        if (f.properties.type or "").lower() == "earthquake"
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Pre-built SeismicEvent instances for loader/aggregator tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_events() -> List[SeismicEvent]:
    """Three pre-transformed SeismicEvent instances for unit tests."""
    return [
        SeismicEvent(
            event_id="us7000test1",
            magnitude=5.4,
            magnitude_class="moderate",
            place="28 km SSW of Shizunai, Japan",
            event_time=datetime(2024, 4, 30, 12, 0, 0, tzinfo=timezone.utc),
            updated_time=datetime(2024, 4, 30, 14, 0, 0, tzinfo=timezone.utc),
            latitude=41.8,
            longitude=142.5,
            depth_km=35.0,
            depth_class="shallow",
            energy_joules=2.0e12,
            risk_score=62.5,
            region_id="japan",
            felt=120,
            cdi=4.2,
            mmi=5.1,
            alert_level="green",
            tsunami=0,
            significance=449,
            net="us",
            mag_type="mww",
            status="reviewed",
            raw_place="28 km SSW of Shizunai, Japan",
        ),
        SeismicEvent(
            event_id="us7000test2",
            magnitude=7.8,
            magnitude_class="major",
            place="100 km E of Tokyo, Japan",
            event_time=datetime(2024, 4, 29, 12, 0, 0, tzinfo=timezone.utc),
            updated_time=datetime(2024, 4, 29, 14, 0, 0, tzinfo=timezone.utc),
            latitude=35.7,
            longitude=141.0,
            depth_km=10.0,
            depth_class="shallow",
            energy_joules=1.0e16,
            risk_score=89.3,
            region_id="japan",
            felt=None,
            cdi=None,
            mmi=None,
            alert_level=None,
            tsunami=1,
            significance=950,
            net="us",
            mag_type="mw",
            status="reviewed",
            raw_place="100 km E of Tokyo, Japan",
        ),
        SeismicEvent(
            event_id="us7000test4",
            magnitude=8.9,
            magnitude_class="great",
            place="Off the coast of Sumatra",
            event_time=datetime(2024, 4, 26, 12, 0, 0, tzinfo=timezone.utc),
            updated_time=datetime(2024, 4, 26, 14, 0, 0, tzinfo=timezone.utc),
            latitude=-3.5,
            longitude=99.0,
            depth_km=25.0,
            depth_class="shallow",
            energy_joules=5.0e17,
            risk_score=95.1,
            region_id="indonesia",
            felt=10000,
            cdi=9.0,
            mmi=9.5,
            alert_level="red",
            tsunami=1,
            significance=1500,
            net="us",
            mag_type="mww",
            status="reviewed",
            raw_place="Off the coast of Sumatra",
        ),
    ]
