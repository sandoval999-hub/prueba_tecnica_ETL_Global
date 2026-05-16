"""
tests/test_models.py — Unit tests for Pydantic v2 USGS response validation
and internal dataclasses for the seismic ETL pipeline.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import (
    USGSFeature,
    USGSGeometry,
    USGSProperties,
    USGSResponse,
    USGSMetadata,
    RegionConfig,
    SeismicEvent,
)


@pytest.mark.unit
def test_valid_usgs_feature():
    """Valida que un feature completo con todos los campos se parsea correctamente."""
    feature = USGSFeature(
        type="Feature",
        properties=USGSProperties(
            mag=5.4,
            place="Japan",
            time=1714480000000,
            type="earthquake"
        ),
        geometry=USGSGeometry(type="Point", coordinates=[142.5, 41.8, 35.0]),
        id="us7000test1",
    )
    assert feature.id == "us7000test1"
    assert feature.properties.mag == 5.4


@pytest.mark.unit
def test_invalid_geometry_wrong_length():
    """coordinates con solo 2 elementos debe lanzar ValidationError."""
    with pytest.raises(ValidationError):
        USGSGeometry(type="Point", coordinates=[142.5, 41.8])


@pytest.mark.unit
def test_null_felt_accepted():
    """felt=None en properties se debe aceptar sin error (valor opcional nulo)."""
    props = USGSProperties(
        mag=5.0,
        place="Test place",
        time=1714480000000,
        felt=None,
        type="earthquake",
    )
    assert props.felt is None


@pytest.mark.unit
def test_null_magnitude_accepted_by_pydantic():
    """mag=None es aceptado por Pydantic (el filtrado de nulos lo hace el Transformer)."""
    props = USGSProperties(
        mag=None,
        place="Test place",
        time=1714480000000,
        type="earthquake",
    )
    assert props.mag is None


@pytest.mark.unit
def test_coordinates_longitude_first():
    """
    GeoJSON standard: [longitude, latitude, depth].
    Verifica que el orden posicional se mantenga intacto.
    """
    geom = USGSGeometry(type="Point", coordinates=[142.5, 41.8, 35.0])
    assert geom.coordinates[0] == 142.5  # longitud
    assert geom.coordinates[1] == 41.8   # latitud
    assert geom.coordinates[2] == 35.0   # profundidad


@pytest.mark.unit
def test_unknown_event_type_accepted():
    """
    type="quarry blast" es aceptado por el modelo Pydantic.
    El filtrado de tipos no sísmicos lo hace el Extractor.
    """
    props = USGSProperties(
        mag=2.1,
        place="Quarryville",
        time=1714300000000,
        type="quarry blast"
    )
    assert props.type == "quarry blast"


@pytest.mark.unit
def test_empty_event_id_raises():
    """id="" lanza ValidationError gracias al model_validator."""
    with pytest.raises(ValidationError):
        USGSFeature(
            type="Feature",
            properties=USGSProperties(mag=5.4, type="earthquake"),
            geometry=USGSGeometry(type="Point", coordinates=[142.5, 41.8, 35.0]),
            id="",
        )


@pytest.mark.unit
def test_usgs_response_parses_feature_list():
    """USGSResponse con una lista de features cuenta la longitud correctamente."""
    feature1 = USGSFeature(
        type="Feature",
        properties=USGSProperties(mag=5.4, type="earthquake"),
        geometry=USGSGeometry(type="Point", coordinates=[142.5, 41.8, 35.0]),
        id="test1",
    )
    feature2 = USGSFeature(
        type="Feature",
        properties=USGSProperties(mag=7.8, type="earthquake"),
        geometry=USGSGeometry(type="Point", coordinates=[141.0, 35.7, 10.0]),
        id="test2",
    )
    response = USGSResponse(
        type="FeatureCollection",
        metadata=USGSMetadata(count=2),
        features=[feature1, feature2],
        bbox=[-180.0, -90.0, 0.0, 180.0, 90.0, 700.0]
    )
    assert len(response.features) == 2


@pytest.mark.unit
def test_region_config_contains_inside():
    """Verifica que un punto dentro del bounding box retorna True."""
    japan = RegionConfig(
        region_id="japan",
        display_name="Japón",
        min_lat=30.0,
        max_lat=46.0,
        min_lon=128.0,
        max_lon=146.0,
    )
    assert japan.contains(38.0, 136.0) is True


@pytest.mark.unit
def test_region_config_contains_outside():
    """Verifica que un punto fuera del bounding box retorna False."""
    japan = RegionConfig(
        region_id="japan",
        display_name="Japón",
        min_lat=30.0,
        max_lat=46.0,
        min_lon=128.0,
        max_lon=146.0,
    )
    assert japan.contains(0.0, 0.0) is False  # Gulf of Guinea


@pytest.mark.unit
def test_seismic_event_has_tsunami_property():
    """Verifica que la propiedad calculada has_tsunami funciona correctamente."""
    from datetime import datetime, timezone
    
    event_with_tsunami = SeismicEvent(
        event_id="us7000test1",
        magnitude=5.4,
        magnitude_class="moderate",
        place="Japan",
        event_time=datetime.now(timezone.utc),
        updated_time=datetime.now(timezone.utc),
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
        tsunami=1,
        significance=449,
        net="us",
        mag_type="mww",
        status="reviewed",
        raw_place="Japan",
    )
    assert event_with_tsunami.has_tsunami is True

    event_no_tsunami = SeismicEvent(
        event_id="us7000test2",
        magnitude=5.4,
        magnitude_class="moderate",
        place="Japan",
        event_time=datetime.now(timezone.utc),
        updated_time=datetime.now(timezone.utc),
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
        raw_place="Japan",
    )
    assert event_no_tsunami.has_tsunami is False


@pytest.mark.unit
def test_longitude_out_of_range_raises():
    """longitud > 180 debe lanzar ValidationError en la validación de coordenadas."""
    with pytest.raises(ValidationError):
        USGSGeometry(type="Point", coordinates=[200.0, 41.8, 35.0])
