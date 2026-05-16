"""
tests/conftest.py — Shared pytest fixtures for the seismic ETL test suite.

All fixtures in this file are available to every test file without import.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.models import RegionConfig


# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ─────────────────────────────────────────────────────────────────────────────
# Shared region configs
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def japan_region() -> RegionConfig:
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
# Sample config
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_cfg() -> Dict[str, Any]:
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
