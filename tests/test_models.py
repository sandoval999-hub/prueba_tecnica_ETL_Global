"""
tests/test_models.py — Unit tests for Pydantic v2 USGS response validation.

Tests:
  - Valid full GeoJSON response parses successfully
  - Missing required fields raise ValidationError
  - Invalid coordinate ranges raise ValidationError
  - Optional null fields are accepted
  - USGSFeature id is required
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import USGSFeature, USGSGeometry, USGSProperties, USGSResponse


class TestUSGSGeometry:
    @pytest.mark.unit
    def test_valid_geometry(self):
        geom = USGSGeometry(type="Point", coordinates=[142.5, 41.8, 35.0])
        assert geom.coordinates[0] == 142.5
        assert geom.coordinates[1] == 41.8
        assert geom.coordinates[2] == 35.0

    @pytest.mark.unit
    def test_requires_three_coordinates(self):
        with pytest.raises(ValidationError):
            USGSGeometry(type="Point", coordinates=[142.5, 41.8])

    @pytest.mark.unit
    def test_invalid_longitude_raises(self):
        with pytest.raises(ValidationError):
            USGSGeometry(type="Point", coordinates=[200.0, 41.8, 35.0])

    @pytest.mark.unit
    def test_invalid_latitude_raises(self):
        with pytest.raises(ValidationError):
            USGSGeometry(type="Point", coordinates=[142.5, 95.0, 35.0])

    @pytest.mark.unit
    def test_boundary_values_accepted(self):
        geom = USGSGeometry(type="Point", coordinates=[-180.0, -90.0, 0.0])
        assert geom.coordinates[0] == -180.0


class TestUSGSProperties:
    @pytest.mark.unit
    def test_all_nullable_fields_accept_none(self):
        props = USGSProperties(
            mag=5.0,
            place="Test place",
            time=1714480000000,
            felt=None,
            cdi=None,
            mmi=None,
            alert=None,
            sig=200,
            type="earthquake",
        )
        assert props.felt is None
        assert props.cdi is None
        assert props.alert is None

    @pytest.mark.unit
    def test_all_fields_are_optional(self):
        """USGSProperties should parse even with an empty dict."""
        props = USGSProperties()
        assert props.mag is None
        assert props.time is None


class TestUSGSFeature:
    @pytest.mark.unit
    def test_valid_feature(self):
        feature = USGSFeature(
            type="Feature",
            properties=USGSProperties(
                mag=5.4, place="Japan", time=1714480000000, type="earthquake"
            ),
            geometry=USGSGeometry(type="Point", coordinates=[142.5, 41.8, 35.0]),
            id="us7000m4vf",
        )
        assert feature.id == "us7000m4vf"
        assert feature.properties.mag == 5.4

    @pytest.mark.unit
    def test_empty_event_id_raises(self):
        with pytest.raises(ValidationError):
            USGSFeature(
                type="Feature",
                properties=USGSProperties(mag=5.4),
                geometry=USGSGeometry(type="Point", coordinates=[142.5, 41.8, 35.0]),
                id="",
            )


class TestUSGSResponse:
    @pytest.mark.unit
    def test_full_response_parses(self, sample_api_response):
        response = USGSResponse.model_validate(sample_api_response)
        assert response.type == "FeatureCollection"
        assert len(response.features) == 4
        assert response.metadata.count == 4

    @pytest.mark.unit
    def test_earthquake_count(self, sample_api_response):
        response = USGSResponse.model_validate(sample_api_response)
        earthquakes = [
            f for f in response.features
            if (f.properties.type or "").lower() == "earthquake"
        ]
        # 3 earthquakes + 1 quarry blast in the sample
        assert len(earthquakes) == 3

    @pytest.mark.unit
    def test_quarry_blast_present(self, sample_api_response):
        response = USGSResponse.model_validate(sample_api_response)
        quarry = [
            f for f in response.features
            if (f.properties.type or "").lower() == "quarry blast"
        ]
        assert len(quarry) == 1

    @pytest.mark.unit
    def test_missing_features_key_raises(self):
        with pytest.raises(ValidationError):
            USGSResponse.model_validate({
                "type": "FeatureCollection",
                "metadata": {"generated": 123, "count": 0},
                # "features" intentionally missing
            })

    @pytest.mark.unit
    def test_feature_coordinates_order(self, sample_api_response):
        """Verify [lon, lat, depth] order — classic trap for new data engineers."""
        response = USGSResponse.model_validate(sample_api_response)
        # us7000test1: coordinates = [142.5, 41.8, 35.0]
        feature = next(f for f in response.features if f.id == "us7000test1")
        coords = feature.geometry.coordinates
        assert coords[0] == 142.5   # longitude, NOT latitude
        assert coords[1] == 41.8   # latitude
        assert coords[2] == 35.0   # depth_km
