"""
tests/test_extractor.py — Unit tests for extractor.py

All tests use mocked HTTP responses — NO real API calls are made.
Marks: @pytest.mark.unit for fast unit tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.extractor import USGSExtractor
from src.models import RegionConfig
from src.utils import CircuitBreaker, CircuitBreakerOpen


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_mock_response(status_code: int, json_data: Dict[str, Any]) -> MagicMock:
    """Build a mock requests.Response object."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.headers = {}
    if status_code >= 400:
        mock_resp.raise_for_status.side_effect = requests.HTTPError(
            f"HTTP Error {status_code}", response=mock_resp
        )
    else:
        mock_resp.raise_for_status.return_value = None
    return mock_resp


@pytest.fixture
def circuit_breaker(tmp_path) -> CircuitBreaker:
    cb = CircuitBreaker(
        state_file=str(tmp_path / "cb_state.json"),
        failure_threshold=3,
    )
    return cb


@pytest.fixture
def extractor(sample_cfg, circuit_breaker) -> USGSExtractor:
    return USGSExtractor(sample_cfg, circuit_breaker)


class TestUSGSExtractorInit:
    @pytest.mark.unit
    def test_extractor_created(self, extractor):
        assert extractor.base_url == "https://earthquake.usgs.gov/fdsnws/event/1/query"
        assert extractor.max_retries == 1


class TestExtractAlert:
    @pytest.mark.unit
    def test_saves_raw_json(self, extractor, sample_api_response):
        """extract_alert should save raw json to disk and return the file paths."""
        mock_resp = _make_mock_response(200, sample_api_response)
        with patch("requests.get", return_value=mock_resp):
            file_paths = extractor.extract_alert()
        # 1 API call -> 1 file saved
        assert len(file_paths) == 1
        assert Path(file_paths[0]).exists()

    @pytest.mark.unit
    def test_circuit_breaker_records_success(self, extractor, circuit_breaker, sample_api_response):
        mock_resp = _make_mock_response(200, sample_api_response)
        with patch("requests.get", return_value=mock_resp):
            extractor.extract_alert()
        assert circuit_breaker.state == "closed"
        assert circuit_breaker.failures == 0

    @pytest.mark.unit
    def test_raises_on_server_error_after_retries(self, extractor, circuit_breaker):
        mock_resp = _make_mock_response(503, {})
        with patch("requests.get", return_value=mock_resp):
            with pytest.raises(requests.HTTPError):
                extractor.extract_alert()

    @pytest.mark.unit
    def test_circuit_breaker_opens_after_threshold(self, sample_cfg, tmp_path, sample_api_response):
        cb = CircuitBreaker(
            state_file=str(tmp_path / "cb2.json"),
            failure_threshold=3,
        )
        # Override config so max_retries = 3 to trigger threshold
        cfg = dict(sample_cfg)
        cfg["api"] = dict(sample_cfg["api"])
        cfg["api"]["max_retries"] = 3

        ext = USGSExtractor(cfg, cb)
        mock_resp = _make_mock_response(500, {})
        with patch("requests.get", return_value=mock_resp):
            with pytest.raises((requests.HTTPError, CircuitBreakerOpen)):
                ext.extract_alert()
        # After 3 failures the circuit should be open
        assert cb.state == "open"


class TestExtractDaily:
    @pytest.mark.unit
    def test_returns_events_for_region(
        self, extractor, all_regions, sample_api_response
    ):
        mock_resp = _make_mock_response(200, sample_api_response)
        with patch("requests.get", return_value=mock_resp) as mock_get:
            features = extractor.extract_daily(
                regions=[all_regions[2]],  # japan
                lookback_hours=24,
                min_magnitude=1.0,
            )
        mock_get.assert_called_once()
        assert isinstance(features, list)

    @pytest.mark.unit
    def test_called_once_per_region(self, extractor, all_regions, sample_api_response):
        mock_resp = _make_mock_response(200, sample_api_response)
        with patch("requests.get", return_value=mock_resp) as mock_get:
            extractor.extract_daily(
                regions=all_regions[:3],
                lookback_hours=24,
            )
        assert mock_get.call_count == 3


class TestCircuitBreakerCheck:
    @pytest.mark.unit
    def test_open_circuit_raises_immediately(self, sample_cfg, tmp_path, sample_api_response):
        cb = CircuitBreaker(
            state_file=str(tmp_path / "cb_open.json"),
            failure_threshold=3,
        )
        cb._state["state"] = "open"
        cb._state["last_failure"] = "2025-01-01T00:00:00"
        cb._save_state()

        ext = USGSExtractor(sample_cfg, cb)
        with pytest.raises(CircuitBreakerOpen):
            ext.extract_alert()


class TestDateRangeSegmentation:
    @pytest.mark.unit
    def test_segments_long_range(self):
        from datetime import date
        from src.utils import date_range_segments

        segments = list(
            date_range_segments(date(2024, 1, 1), date(2024, 4, 30), max_days=30)
        )
        # 4 months = ~120 days → at least 4 segments
        assert len(segments) >= 4
        # First segment starts on start_date
        assert segments[0][0] == date(2024, 1, 1)
        # Last segment ends on or before end_date
        assert segments[-1][1] <= date(2024, 4, 30)

    @pytest.mark.unit
    def test_no_gap_between_segments(self):
        from datetime import date, timedelta
        from src.utils import date_range_segments

        segments = list(
            date_range_segments(date(2024, 1, 1), date(2024, 3, 31), max_days=30)
        )
        for i in range(len(segments) - 1):
            end = segments[i][1]
            start_next = segments[i + 1][0]
            assert start_next == end + timedelta(days=1)
