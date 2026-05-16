"""
tests/test_loader.py — Unit tests for loader.py

Uses an in-memory SQLite database via SQLAlchemy to avoid requiring MySQL.
Marks: @pytest.mark.unit
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from src.models import QuarantineRecord, SeismicEvent


@pytest.fixture
def sample_seismic_event() -> SeismicEvent:
    return SeismicEvent(
        event_id="us7000test1",
        magnitude=5.4,
        magnitude_class="moderate",
        place="28 km SSW of Shizunai, Japan",
        event_time=datetime(2024, 4, 30, 12, 0, 0),
        updated_time=datetime(2024, 4, 30, 14, 0, 0),
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
    )


@pytest.fixture
def tsunami_event(sample_seismic_event) -> SeismicEvent:
    ev = SeismicEvent(**sample_seismic_event.__dict__)
    ev.event_id = "us7000test2"
    ev.tsunami = 1
    ev.magnitude = 7.8
    ev.magnitude_class = "major"
    return ev


class TestSeismicLoaderUnit:
    """
    Unit tests that do NOT require a real MySQL connection.
    They test the loader's logic by mocking the SQLAlchemy engine.
    """

    @pytest.mark.unit
    def test_load_events_empty_list_returns_zero(self, sample_cfg):
        """Loading an empty list should return 0 without any DB calls."""
        from src.loader import SeismicLoader

        with patch("src.loader.create_engine") as mock_engine:
            loader = SeismicLoader("mysql+pymysql://x:y@localhost/db", sample_cfg)
            result = loader.load_events([], run_id=1)
        assert result == 0

    @pytest.mark.unit
    def test_batch_size_configurable(self, sample_cfg):
        """batch_size should be read from config."""
        from src.loader import SeismicLoader

        cfg = dict(sample_cfg)
        cfg["pipeline"] = {"batch_size": 50}

        with patch("src.loader.create_engine"):
            loader = SeismicLoader("mysql+pymysql://x:y@localhost/db", cfg)
        assert loader.batch_size == 50

    @pytest.mark.unit
    def test_default_batch_size(self, sample_cfg):
        """Default batch_size should be 200."""
        from src.loader import SeismicLoader

        with patch("src.loader.create_engine"):
            loader = SeismicLoader("mysql+pymysql://x:y@localhost/db", sample_cfg)
        assert loader.batch_size == 200

    @pytest.mark.unit
    def test_tsunami_events_identified(self, sample_seismic_event, tsunami_event):
        """Events with tsunami=1 should be identified by has_tsunami property."""
        assert not sample_seismic_event.has_tsunami
        assert tsunami_event.has_tsunami

    @pytest.mark.unit
    def test_quarantine_record_structure(self):
        """QuarantineRecord should store all required fields."""
        rec = QuarantineRecord(
            event_id="bad_event",
            raw_json='{"id": "bad_event"}',
            rejection_reason="KeyError: mag",
            attempts=1,
            pipeline_mode="daily",
        )
        assert rec.event_id == "bad_event"
        assert rec.attempts == 1
        assert rec.pipeline_mode == "daily"
        assert isinstance(rec.created_at, datetime)


class TestCircuitBreaker:
    """Test circuit breaker state machine."""

    @pytest.mark.unit
    def test_initial_state_is_closed(self, tmp_path):
        from src.utils import CircuitBreaker

        cb = CircuitBreaker(
            state_file=str(tmp_path / "cb.json"),
            failure_threshold=3,
        )
        assert cb.state == "closed"
        assert cb.failures == 0

    @pytest.mark.unit
    def test_records_failure(self, tmp_path):
        from src.utils import CircuitBreaker

        cb = CircuitBreaker(
            state_file=str(tmp_path / "cb.json"),
            failure_threshold=3,
        )
        cb.record_failure()
        assert cb.failures == 1
        assert cb.state == "half-open"

    @pytest.mark.unit
    def test_opens_at_threshold(self, tmp_path):
        from src.utils import CircuitBreaker

        cb = CircuitBreaker(
            state_file=str(tmp_path / "cb.json"),
            failure_threshold=3,
        )
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"

    @pytest.mark.unit
    def test_check_raises_when_open(self, tmp_path):
        from src.utils import CircuitBreaker, CircuitBreakerOpen

        cb = CircuitBreaker(
            state_file=str(tmp_path / "cb.json"),
            failure_threshold=3,
        )
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()

        with pytest.raises(CircuitBreakerOpen):
            cb.check()

    @pytest.mark.unit
    def test_reset_closes_circuit(self, tmp_path):
        from src.utils import CircuitBreaker

        cb = CircuitBreaker(
            state_file=str(tmp_path / "cb.json"),
            failure_threshold=3,
        )
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"

        cb.reset()
        assert cb.state == "closed"
        assert cb.failures == 0

    @pytest.mark.unit
    def test_success_resets_failures(self, tmp_path):
        from src.utils import CircuitBreaker

        cb = CircuitBreaker(
            state_file=str(tmp_path / "cb.json"),
            failure_threshold=3,
        )
        cb.record_failure()
        cb.record_success()
        assert cb.state == "closed"
        assert cb.failures == 0

    @pytest.mark.unit
    def test_state_persisted_to_file(self, tmp_path):
        from src.utils import CircuitBreaker

        state_file = str(tmp_path / "cb.json")
        cb = CircuitBreaker(state_file=state_file, failure_threshold=3)
        cb.record_failure()

        # Reload from file
        cb2 = CircuitBreaker(state_file=state_file, failure_threshold=3)
        assert cb2.failures == 1
