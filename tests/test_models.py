"""
tests/test_models.py — Unit tests for Pydantic v2 USGS response validation.

Tests:
  - Valid full GeoJSON response parses successfully
  - Missing required fields raise ValidationError
  - Invalid coordinate ranges raise ValidationError
  - Optional null fields are accepted
  - USGSFeature id is required
  - Coordinates order: [longitude, latitude, depth]
  - Unknown type values are accepted (filtering is done in transformer)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import USGSFeature, USGSGeometry, USGSProperties, USGSResponse


class TestUSGSGeometry:
    @pytest.mark.unit
    def test_valid_geometry(self):
        """Valid 3-element coordinate array is accepted."""
        geom = USGSGeometry(type="Point", coordinates=[142.5, 41.8, 35.0])
        assert geom.coordinates[0] == 142.5
        assert geom.coordinates[1] == 41.8
        assert geom.coordinates[2] == 35.0

    @pytest.mark.unit
    def test_requires_three_coordinates(self):
        """Coordinates with only 2 elements should raise ValidationError."""
        with pytest.raises(ValidationError):
            USGSGeometry(type="Point", coordinates=[142.5, 41.8])

    @pytest.mark.unit
    def test_invalid_longitude_raises(self):
        """Longitude > 180 should raise ValidationError."""
        with pytest.raises(ValidationError):
            USGSGeometry(type="Point", coordinates=[200.0, 41.8, 35.0])

    @pytest.mark.unit
    def test_invalid_latitude_raises(self):
        """Latitude > 90 should raise ValidationError."""
        with pytest.raises(ValidationError):
            USGSGeometry(type="Point", coordinates=[142.5, 95.0, 35.0])

    @pytest.mark.unit
    def test_boundary_values_accepted(self):
        """Extreme boundary values (-180, -90) should be accepted."""
        geom = USGSGeometry(type="Point", coordinates=[-180.0, -90.0, 0.0])
        assert geom.coordinates[0] == -180.0


class TestUSGSProperties:
    @pytest.mark.unit
    def test_all_nullable_fields_accept_none(self):
        """Nullable fields (felt, cdi, mmi, alert) accept None values."""
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
        """Complete valid feature parses without error."""
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
        """Empty string for event id should raise ValidationError."""
        with pytest.raises(ValidationError):
            USGSFeature(
                type="Feature",
                properties=USGSProperties(mag=5.4),
                geometry=USGSGeometry(type="Point", coordinates=[142.5, 41.8, 35.0]),
                id="",
            )

    @pytest.mark.unit
    def test_unknown_type_accepted(self):
        """Non-earthquake types like 'quarry blast' should be accepted by the model.
        
        Filtering is done in the transformer, not at the validation level.
        """
        feature = USGSFeature(
            type="Feature",
            properties=USGSProperties(
                mag=2.1, place="Quarryville", type="quarry blast", time=1714300000000,
            ),
            geometry=USGSGeometry(type="Point", coordinates=[-118.0, 34.2, 5.0]),
            id="us7000test3",
        )
        assert feature.properties.type == "quarry blast"


class TestUSGSResponse:
    @pytest.mark.unit
    def test_full_response_parses(self, sample_api_response):
        """Complete GeoJSON response with 5 features parses correctly."""
        response = USGSResponse.model_validate(sample_api_response)
        assert response.type == "FeatureCollection"
        assert len(response.features) == 5
        assert response.metadata.count == 5

    @pytest.mark.unit
    def test_earthquake_count(self, sample_api_response):
        """Sample has 4 earthquakes (incl. 1 deleted) + 1 quarry blast."""
        response = USGSResponse.model_validate(sample_api_response)
        earthquakes = [
            f for f in response.features
            if (f.properties.type or "").lower() == "earthquake"
        ]
        assert len(earthquakes) == 4

    @pytest.mark.unit
    def test_quarry_blast_present(self, sample_api_response):
        """Sample contains exactly 1 quarry blast event."""
        response = USGSResponse.model_validate(sample_api_response)
        quarry = [
            f for f in response.features
            if (f.properties.type or "").lower() == "quarry blast"
        ]
        assert len(quarry) == 1

    @pytest.mark.unit
    def test_deleted_event_present(self, sample_api_response):
        """Sample contains exactly 1 event with status=deleted."""
        response = USGSResponse.model_validate(sample_api_response)
        deleted = [
            f for f in response.features
            if (f.properties.status or "").lower() == "deleted"
        ]
        assert len(deleted) == 1

    @pytest.mark.unit
    def test_null_felt_event_present(self, sample_api_response):
        """Sample contains an earthquake with felt=null and alert=null (edge case)."""
        response = USGSResponse.model_validate(sample_api_response)
        null_felt = [
            f for f in response.features
            if f.properties.felt is None
            and (f.properties.type or "").lower() == "earthquake"
        ]
        assert len(null_felt) >= 1

    @pytest.mark.unit
    def test_missing_features_key_raises(self):
        """Response missing the 'features' key should raise ValidationError."""
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

    @pytest.mark.unit
    def test_coordinates_longitude_first(self, sample_api_response):
        """Ensure coordinates[0] is longitude (can be negative for western hemisphere)."""
        response = USGSResponse.model_validate(sample_api_response)
        # us7000test3 (quarry blast in California): coordinates = [-118.0, 34.2, 5.0]
        feature = next(f for f in response.features if f.id == "us7000test3")
        coords = feature.geometry.coordinates
        assert coords[0] == -118.0  # longitude (western hemisphere → negative)
        assert coords[1] == 34.2    # latitude (northern hemisphere → positive)
