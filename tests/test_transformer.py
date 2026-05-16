"""
tests/test_transformer.py — Unit tests for transformer.py

Tests:
  - classify_magnitude: all 7 Richter classes + boundary 2.0
  - classify_depth: all 3 depth classes
  - calculate_energy_joules: known values
  - calculate_risk_score: boundary conditions, null felt, cap 100
  - assign_region: known coordinates + global_other
  - ms_to_utc: epoch and known timestamp
  - SeismicTransformer.transform: full pipeline, deduplication, validation
  - Discard filters: quarry blast, deleted status, null magnitude
  - Coordinate order: longitude=geometry[0], latitude=geometry[1]
"""

from __future__ import annotations

import math
import pytest

from src.transformer import (
    SeismicTransformer,
    assign_region,
    calculate_energy_joules,
    calculate_risk_score,
    classify_depth,
    classify_magnitude,
    ms_to_utc,
)


# ─────────────────────────────────────────────────────────────────────────────
# classify_magnitude
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyMagnitude:
    @pytest.mark.unit
    def test_classify_magnitude_micro(self):
        """Magnitude < 2.0 → 'micro'."""
        assert classify_magnitude(0.0) == "micro"
        assert classify_magnitude(1.9) == "micro"

    @pytest.mark.unit
    def test_classify_magnitude_boundary_2_0(self):
        """Exactly 2.0 → 'minor' (not micro). Critical boundary test."""
        assert classify_magnitude(2.0) == "minor"

    @pytest.mark.unit
    def test_classify_magnitude_minor(self):
        """Magnitude 2.0–3.9 → 'minor'."""
        assert classify_magnitude(2.5) == "minor"
        assert classify_magnitude(3.9) == "minor"

    @pytest.mark.unit
    def test_classify_magnitude_light(self):
        """Magnitude 4.0–4.9 → 'light'."""
        assert classify_magnitude(4.0) == "light"
        assert classify_magnitude(4.9) == "light"

    @pytest.mark.unit
    def test_classify_magnitude_moderate(self):
        """Magnitude 5.0–5.9 → 'moderate'."""
        assert classify_magnitude(5.0) == "moderate"
        assert classify_magnitude(5.9) == "moderate"

    @pytest.mark.unit
    def test_classify_magnitude_strong(self):
        """Magnitude 6.0–6.9 → 'strong'."""
        assert classify_magnitude(6.0) == "strong"
        assert classify_magnitude(6.9) == "strong"

    @pytest.mark.unit
    def test_classify_magnitude_major(self):
        """Magnitude 7.0–7.9 → 'major'."""
        assert classify_magnitude(7.0) == "major"
        assert classify_magnitude(7.9) == "major"

    @pytest.mark.unit
    def test_classify_magnitude_great(self):
        """Magnitude ≥ 8.0 → 'great'."""
        assert classify_magnitude(8.0) == "great"
        assert classify_magnitude(9.5) == "great"


# ─────────────────────────────────────────────────────────────────────────────
# classify_depth
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyDepth:
    @pytest.mark.unit
    def test_classify_depth_shallow(self):
        """Depth 0–70 km → 'shallow' (includes boundary at 70)."""
        assert classify_depth(0.0) == "shallow"
        assert classify_depth(70.0) == "shallow"

    @pytest.mark.unit
    def test_classify_depth_intermediate(self):
        """Depth 70–300 km → 'intermediate'."""
        assert classify_depth(70.1) == "intermediate"
        assert classify_depth(150.0) == "intermediate"
        assert classify_depth(300.0) == "intermediate"

    @pytest.mark.unit
    def test_classify_depth_deep(self):
        """Depth > 300 km → 'deep'."""
        assert classify_depth(300.1) == "deep"
        assert classify_depth(650.0) == "deep"


# ─────────────────────────────────────────────────────────────────────────────
# calculate_energy_joules
# ─────────────────────────────────────────────────────────────────────────────

class TestCalculateEnergy:
    @pytest.mark.unit
    def test_magnitude_zero(self):
        """log10(E) = 1.5*0 + 4.8 = 4.8  → E = 10^4.8."""
        expected = math.pow(10, 4.8)
        assert abs(calculate_energy_joules(0.0) - expected) < 1.0

    @pytest.mark.unit
    def test_calculate_energy_mag_5(self):
        """log10(E) = 1.5*5 + 4.8 = 12.3 → E = 10^12.3 ≈ 1.995e12."""
        expected = math.pow(10, 12.3)
        result = calculate_energy_joules(5.0)
        assert abs(result - expected) < 1e6
        # Verify order of magnitude
        assert 1e12 < result < 1e13

    @pytest.mark.unit
    def test_magnitude_8(self):
        """log10(E) = 1.5*8 + 4.8 = 16.8 → E = 10^16.8."""
        expected = math.pow(10, 16.8)
        assert abs(calculate_energy_joules(8.0) - expected) < 1e10

    @pytest.mark.unit
    def test_larger_magnitude_more_energy(self):
        """Higher magnitude should always produce more energy."""
        assert calculate_energy_joules(7.0) > calculate_energy_joules(6.0)


# ─────────────────────────────────────────────────────────────────────────────
# calculate_risk_score
# ─────────────────────────────────────────────────────────────────────────────

class TestCalculateRiskScore:
    @pytest.mark.unit
    def test_zero_magnitude_and_depth_gives_low_score(self):
        """Zero mag + maximum depth → very low score."""
        score = calculate_risk_score(0.0, 700.0, 0, None)
        assert 0.0 <= score <= 10.0

    @pytest.mark.unit
    def test_risk_score_all_components(self):
        """Verify exact formula: 0.40*mag + 0.25*depth + 0.20*sig + 0.15*pop."""
        # mag=5.0 → mag_norm = 50.0
        # depth=0.0 → depth_norm = 100.0
        # sig=500 → sig_norm = 50.0
        # felt=50 → pop_proxy = 50.0
        score = calculate_risk_score(5.0, 0.0, 500, 50)
        expected = (50.0 * 0.40) + (100.0 * 0.25) + (50.0 * 0.20) + (50.0 * 0.15)
        assert abs(score - expected) < 0.01

    @pytest.mark.unit
    def test_high_magnitude_high_score(self):
        """Magnitude 9.0 + shallow + high significance → score > 80."""
        score = calculate_risk_score(9.0, 0.0, 1000, 100)
        assert score > 80.0

    @pytest.mark.unit
    def test_score_in_range(self):
        """Risk score should always be in [0, 100] regardless of inputs."""
        for mag in [1.0, 3.5, 6.2, 8.8]:
            for depth in [0.0, 50.0, 200.0, 500.0]:
                score = calculate_risk_score(mag, depth, 300, 50)
                assert 0.0 <= score <= 100.0, f"Score out of range for mag={mag} depth={depth}"

    @pytest.mark.unit
    def test_risk_score_null_felt(self):
        """felt=None → pop_proxy component is 0. Score equals null-felt score."""
        score_null = calculate_risk_score(5.0, 30.0, 400, None)
        score_zero = calculate_risk_score(5.0, 30.0, 400, 0)
        assert score_null == score_zero

    @pytest.mark.unit
    def test_shallow_riskier_than_deep(self):
        """Shallow earthquakes should produce higher risk scores than deep ones."""
        shallow = calculate_risk_score(5.0, 10.0, 400, 50)
        deep = calculate_risk_score(5.0, 600.0, 400, 50)
        assert shallow > deep

    @pytest.mark.unit
    def test_risk_score_cap_100(self):
        """Extreme values (mag=15) should cap at 100, not exceed it."""
        score = calculate_risk_score(15.0, 0.0, 9999, 9999)
        assert score <= 100.0

    @pytest.mark.unit
    def test_felt_capped_at_max(self):
        """felt values above max_felt (100) should cap pop_proxy at 100."""
        score_100 = calculate_risk_score(5.0, 30.0, 400, 100)
        score_9999 = calculate_risk_score(5.0, 30.0, 400, 9999)
        assert score_100 == score_9999


# ─────────────────────────────────────────────────────────────────────────────
# assign_region
# ─────────────────────────────────────────────────────────────────────────────

class TestAssignRegion:
    @pytest.mark.unit
    def test_assign_region_japan(self, all_regions):
        """Tokyo coordinates (35.6, 139.7) → 'japan'."""
        region = assign_region(35.6, 139.7, all_regions)
        assert region == "japan"

    @pytest.mark.unit
    def test_california_coordinates(self, all_regions):
        """San Francisco (37.7, -122.4) → 'california'."""
        region = assign_region(37.7, -122.4, all_regions)
        assert region == "california"

    @pytest.mark.unit
    def test_indonesia_coordinates(self, all_regions):
        """Sumatra (-3.5, 99.0) → 'indonesia'."""
        region = assign_region(-3.5, 99.0, all_regions)
        assert region == "indonesia"

    @pytest.mark.unit
    def test_assign_region_global_other(self, all_regions):
        """Gulf of Guinea (0.0, 0.0) → 'global_other' (not in any region)."""
        region = assign_region(0.0, 0.0, all_regions)
        assert region == "global_other"

    @pytest.mark.unit
    def test_pacific_northwest(self, all_regions):
        """Seattle (47.6, -122.3) → 'pacific_northwest'."""
        region = assign_region(47.6, -122.3, all_regions)
        assert region == "pacific_northwest"


# ─────────────────────────────────────────────────────────────────────────────
# ms_to_utc
# ─────────────────────────────────────────────────────────────────────────────

class TestMsToUtc:
    @pytest.mark.unit
    def test_converts_correctly(self):
        """UNIX epoch (0 ms) → 1970-01-01 UTC."""
        from datetime import timezone
        dt = ms_to_utc(0)
        assert dt.year == 1970
        assert dt.tzinfo == timezone.utc

    @pytest.mark.unit
    def test_known_timestamp(self):
        """2024-04-30 16:00:00 UTC  →  1714492800000 ms."""
        dt = ms_to_utc(1714492800000)
        assert dt.year == 2024
        assert dt.month == 4
        assert dt.day == 30


# ─────────────────────────────────────────────────────────────────────────────
# SeismicTransformer integration
# ─────────────────────────────────────────────────────────────────────────────

class TestSeismicTransformer:
    @pytest.mark.unit
    def test_transforms_valid_events(self, sample_cfg, all_regions, raw_json_files):
        """Valid earthquakes should be transformed successfully."""
        transformer = SeismicTransformer(sample_cfg, all_regions)
        events, quality, quarantine = transformer.transform(raw_json_files, "daily")

        # 4 earthquakes from fixture: test1, test2, test4 valid + test5 deleted → 3 valid
        assert len(events) == 3
        assert len(quarantine) == 0

    @pytest.mark.unit
    def test_transform_discards_deleted_status(self, sample_cfg, all_regions, raw_json_files):
        """Events with status='deleted' should be discarded with quality entry."""
        transformer = SeismicTransformer(sample_cfg, all_regions)
        events, quality, _ = transformer.transform(raw_json_files, "daily")

        # us7000test5 has status=deleted → discarded
        deleted_entries = [e for e in quality if "deleted" in e["motivo_rechazo"]]
        assert len(deleted_entries) == 1

    @pytest.mark.unit
    def test_transform_discards_quarry_blast(self, sample_cfg, all_regions, raw_json_files):
        """Quarry blast events should NOT appear in output (filtered in transform)."""
        transformer = SeismicTransformer(sample_cfg, all_regions)
        events, quality, quarantine = transformer.transform(raw_json_files, "daily")
        
        # Verify no event is a quarry blast
        for e in events:
            assert e.event_id != "nc74034441"  # the ID of the quarry blast in the sample
        
        # We don't even add quarry blasts to quality log anymore, we filter them at reading time
        assert not any("quarry blast" in q.get("motivo_rechazo", "") for q in quality)

    @pytest.mark.unit
    def test_transform_discards_null_magnitude(self, sample_cfg, all_regions, raw_json_files, tmp_path):
        """Events with mag=None should be discarded."""
        import json
        # Read the raw file, set mag to None, rewrite
        with open(raw_json_files[0], "r") as f:
            data = json.load(f)
        data["features"][0]["properties"]["mag"] = None
        new_file = tmp_path / "null_mag.json"
        with open(new_file, "w") as f:
            json.dump(data, f)

        transformer = SeismicTransformer(sample_cfg, all_regions)
        events, quality, _ = transformer.transform([str(new_file)], "daily")
        assert len(events) == 2  # The sample originally had 3 valid, mutating one leaves 2
        assert any("magnitude" in e["motivo_rechazo"] for e in quality)

    @pytest.mark.unit
    def test_deduplication(self, sample_cfg, all_regions, raw_json_files):
        """Duplicate event_ids in the same run should keep only the first."""
        doubled = raw_json_files + raw_json_files
        transformer = SeismicTransformer(sample_cfg, all_regions)
        events, quality, _ = transformer.transform(doubled, "daily")

        events_orig, _, _ = transformer.transform(raw_json_files, "daily")
        assert len(events) == len(events_orig)

        dup_entries = [e for e in quality if "duplicate" in e["motivo_rechazo"]]
        assert len(dup_entries) == 4  # All 4 earthquake features in the second file are duplicates

    @pytest.mark.unit
    def test_region_assignment(self, sample_cfg, all_regions, raw_json_files):
        """Ensure assign_region works during transform."""
        transformer = SeismicTransformer(sample_cfg, all_regions)
        events, _, _ = transformer.transform(raw_json_files, "daily")

        regions = {e.region_id for e in events}
        assert "japan" in regions  # test1 and test2

    @pytest.mark.unit
    def test_risk_score_in_range(self, sample_cfg, all_regions, raw_json_files):
        """All transformed events should have risk_score in [0, 100]."""
        transformer = SeismicTransformer(sample_cfg, all_regions)
        events, _, _ = transformer.transform(raw_json_files, "daily")
        for ev in events:
            assert 0.0 <= ev.risk_score <= 100.0

    @pytest.mark.unit
    def test_tsunami_flag(self, sample_cfg, all_regions, raw_json_files):
        """Ensure the boolean-like tsunami integer is captured correctly."""
        transformer = SeismicTransformer(sample_cfg, all_regions)
        events, _, _ = transformer.transform(raw_json_files, "daily")
        tsunami_events = [e for e in events if e.tsunami == 1]
        assert len(tsunami_events) >= 1  # test2 and test4 have tsunami=1

    @pytest.mark.unit
    def test_magnitude_class_assigned(self, sample_cfg, all_regions, raw_json_files):
        """Every transformed event must have a valid magnitude_class."""
        transformer = SeismicTransformer(sample_cfg, all_regions)
        events, _, _ = transformer.transform(raw_json_files, "alert")
        for ev in events:
            assert ev.magnitude_class in {
                "micro", "minor", "light", "moderate", "strong", "major", "great"
            }

    @pytest.mark.unit
    def test_coordinates_order(self, sample_cfg, all_regions, raw_json_files):
        """Verify longitude = coordinates[0], latitude = coordinates[1].
        
        This is the most common bug in geospatial ETL: confusing lat/lon order.
        USGS uses [longitude, latitude, depth] (GeoJSON standard).
        """
        transformer = SeismicTransformer(sample_cfg, all_regions)
        events, _, _ = transformer.transform(raw_json_files, "daily")

        # us7000test1: coordinates = [142.5, 41.8, 35.0]
        test1 = next(e for e in events if e.event_id == "us7000test1")
        assert test1.longitude == 142.5  # coordinates[0]
        assert test1.latitude == 41.8    # coordinates[1]
        assert test1.depth_km == 35.0    # coordinates[2]
