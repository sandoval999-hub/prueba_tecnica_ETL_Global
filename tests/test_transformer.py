"""
tests/test_transformer.py — Unit tests for transformer.py

Tests:
  - classify_magnitude: all 7 Richter classes
  - classify_depth: all 3 depth classes
  - calculate_energy_joules: known values
  - calculate_risk_score: boundary conditions, null felt
  - assign_region: known coordinates
  - SeismicTransformer.transform: full pipeline, deduplication, validation
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
    def test_micro(self):
        assert classify_magnitude(0.0) == "micro"
        assert classify_magnitude(1.9) == "micro"

    @pytest.mark.unit
    def test_boundary_micro_minor(self):
        assert classify_magnitude(2.0) == "minor"

    @pytest.mark.unit
    def test_minor(self):
        assert classify_magnitude(2.5) == "minor"
        assert classify_magnitude(3.9) == "minor"

    @pytest.mark.unit
    def test_light(self):
        assert classify_magnitude(4.0) == "light"
        assert classify_magnitude(4.9) == "light"

    @pytest.mark.unit
    def test_moderate(self):
        assert classify_magnitude(5.0) == "moderate"
        assert classify_magnitude(5.9) == "moderate"

    @pytest.mark.unit
    def test_strong(self):
        assert classify_magnitude(6.0) == "strong"
        assert classify_magnitude(6.9) == "strong"

    @pytest.mark.unit
    def test_major(self):
        assert classify_magnitude(7.0) == "major"
        assert classify_magnitude(7.9) == "major"

    @pytest.mark.unit
    def test_great(self):
        assert classify_magnitude(8.0) == "great"
        assert classify_magnitude(9.5) == "great"


# ─────────────────────────────────────────────────────────────────────────────
# classify_depth
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyDepth:
    @pytest.mark.unit
    def test_shallow_zero(self):
        assert classify_depth(0.0) == "shallow"

    @pytest.mark.unit
    def test_shallow_boundary(self):
        assert classify_depth(70.0) == "shallow"

    @pytest.mark.unit
    def test_intermediate(self):
        assert classify_depth(70.1) == "intermediate"
        assert classify_depth(150.0) == "intermediate"
        assert classify_depth(300.0) == "intermediate"

    @pytest.mark.unit
    def test_deep(self):
        assert classify_depth(300.1) == "deep"
        assert classify_depth(650.0) == "deep"


# ─────────────────────────────────────────────────────────────────────────────
# calculate_energy_joules
# ─────────────────────────────────────────────────────────────────────────────

class TestCalculateEnergy:
    @pytest.mark.unit
    def test_magnitude_zero(self):
        # log10(E) = 1.5*0 + 4.8 = 4.8  → E = 10^4.8
        expected = math.pow(10, 4.8)
        assert abs(calculate_energy_joules(0.0) - expected) < 1.0

    @pytest.mark.unit
    def test_magnitude_5(self):
        # log10(E) = 1.5*5 + 4.8 = 12.3 → E = 10^12.3
        expected = math.pow(10, 12.3)
        assert abs(calculate_energy_joules(5.0) - expected) < 1e6

    @pytest.mark.unit
    def test_magnitude_8(self):
        # log10(E) = 1.5*8 + 4.8 = 16.8 → E = 10^16.8
        expected = math.pow(10, 16.8)
        assert abs(calculate_energy_joules(8.0) - expected) < 1e10

    @pytest.mark.unit
    def test_larger_magnitude_more_energy(self):
        assert calculate_energy_joules(7.0) > calculate_energy_joules(6.0)


# ─────────────────────────────────────────────────────────────────────────────
# calculate_risk_score
# ─────────────────────────────────────────────────────────────────────────────

class TestCalculateRiskScore:
    @pytest.mark.unit
    def test_zero_magnitude_and_depth_gives_low_score(self):
        score = calculate_risk_score(0.0, 700.0, 0, None)
        assert 0.0 <= score <= 10.0

    @pytest.mark.unit
    def test_high_magnitude_high_score(self):
        score = calculate_risk_score(9.0, 0.0, 1000, 100)
        assert score > 80.0

    @pytest.mark.unit
    def test_score_in_range(self):
        for mag in [1.0, 3.5, 6.2, 8.8]:
            for depth in [0.0, 50.0, 200.0, 500.0]:
                score = calculate_risk_score(mag, depth, 300, 50)
                assert 0.0 <= score <= 100.0, f"Score out of range for mag={mag} depth={depth}"

    @pytest.mark.unit
    def test_null_felt_is_zero_proxy(self):
        score_null = calculate_risk_score(5.0, 30.0, 400, None)
        score_zero = calculate_risk_score(5.0, 30.0, 400, 0)
        assert score_null == score_zero

    @pytest.mark.unit
    def test_shallow_riskier_than_deep(self):
        shallow = calculate_risk_score(5.0, 10.0, 400, 50)
        deep = calculate_risk_score(5.0, 600.0, 400, 50)
        assert shallow > deep

    @pytest.mark.unit
    def test_felt_capped_at_max(self):
        score_100 = calculate_risk_score(5.0, 30.0, 400, 100)
        score_9999 = calculate_risk_score(5.0, 30.0, 400, 9999)
        assert score_100 == score_9999


# ─────────────────────────────────────────────────────────────────────────────
# assign_region
# ─────────────────────────────────────────────────────────────────────────────

class TestAssignRegion:
    @pytest.mark.unit
    def test_japan_coordinates(self, all_regions):
        region = assign_region(35.6, 139.7, all_regions)  # Tokyo
        assert region == "japan"

    @pytest.mark.unit
    def test_california_coordinates(self, all_regions):
        region = assign_region(37.7, -122.4, all_regions)  # San Francisco
        assert region == "california"

    @pytest.mark.unit
    def test_indonesia_coordinates(self, all_regions):
        region = assign_region(-3.5, 99.0, all_regions)   # Sumatra
        assert region == "indonesia"

    @pytest.mark.unit
    def test_global_other_for_unknown_location(self, all_regions):
        region = assign_region(0.0, 0.0, all_regions)     # Gulf of Guinea
        assert region == "global_other"

    @pytest.mark.unit
    def test_pacific_northwest(self, all_regions):
        region = assign_region(47.6, -122.3, all_regions)  # Seattle
        assert region == "pacific_northwest"


# ─────────────────────────────────────────────────────────────────────────────
# ms_to_utc
# ─────────────────────────────────────────────────────────────────────────────

class TestMsToUtc:
    @pytest.mark.unit
    def test_converts_correctly(self):
        from datetime import timezone
        dt = ms_to_utc(0)
        assert dt.year == 1970
        assert dt.tzinfo == timezone.utc

    @pytest.mark.unit
    def test_known_timestamp(self):
        # 2024-04-30 16:00:00 UTC  →  1714492800000 ms
        dt = ms_to_utc(1714492800000)
        assert dt.year == 2024
        assert dt.month == 4
        assert dt.day == 30


# ─────────────────────────────────────────────────────────────────────────────
# SeismicTransformer integration
# ─────────────────────────────────────────────────────────────────────────────

class TestSeismicTransformer:
    @pytest.mark.unit
    def test_transforms_valid_events(self, sample_cfg, all_regions, earthquake_features):
        transformer = SeismicTransformer(sample_cfg, all_regions)
        events, quality, quarantine = transformer.transform(earthquake_features, "daily")

        # Sample has 3 earthquakes (test4=indonesia, test1=japan, test2=japan)
        assert len(events) > 0
        assert len(quarantine) == 0

    @pytest.mark.unit
    def test_deduplication(self, sample_cfg, all_regions, earthquake_features):
        # Pass the same features twice
        doubled = earthquake_features + earthquake_features
        transformer = SeismicTransformer(sample_cfg, all_regions)
        events, quality, _ = transformer.transform(doubled, "daily")

        # Should have same count as original (no duplicates)
        events_orig, _, _ = transformer.transform(earthquake_features, "daily")
        assert len(events) == len(events_orig)

        # Quality log should contain duplicate entries
        dup_entries = [e for e in quality if "duplicate" in e["motivo_rechazo"]]
        assert len(dup_entries) == len(earthquake_features)

    @pytest.mark.unit
    def test_region_assignment(self, sample_cfg, all_regions, earthquake_features):
        transformer = SeismicTransformer(sample_cfg, all_regions)
        events, _, _ = transformer.transform(earthquake_features, "daily")

        regions = {e.region_id for e in events}
        # us7000test1 and us7000test2 are in Japan
        assert "japan" in regions

    @pytest.mark.unit
    def test_risk_score_in_range(self, sample_cfg, all_regions, earthquake_features):
        transformer = SeismicTransformer(sample_cfg, all_regions)
        events, _, _ = transformer.transform(earthquake_features, "daily")
        for ev in events:
            assert 0.0 <= ev.risk_score <= 100.0

    @pytest.mark.unit
    def test_tsunami_flag(self, sample_cfg, all_regions, earthquake_features):
        transformer = SeismicTransformer(sample_cfg, all_regions)
        events, _, _ = transformer.transform(earthquake_features, "daily")
        tsunami_events = [e for e in events if e.tsunami == 1]
        assert len(tsunami_events) >= 1  # test2 and test4 have tsunami=1

    @pytest.mark.unit
    def test_magnitude_class_assigned(self, sample_cfg, all_regions, earthquake_features):
        transformer = SeismicTransformer(sample_cfg, all_regions)
        events, _, _ = transformer.transform(earthquake_features, "alert")
        for ev in events:
            assert ev.magnitude_class in {
                "micro", "minor", "light", "moderate", "strong", "major", "great"
            }
