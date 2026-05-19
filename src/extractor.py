"""
src/extractor.py — Extract phase of the seismic ETL pipeline.

Responsibilities:
  - Call the USGS Earthquake Hazards API for each monitoring region
  - Handle retries with exponential backoff
  - Respect rate limits and honour Retry-After on HTTP 429
  - Save raw API responses to local disk (data/raw/) to simulate a Data Lake Bronze layer
  - Segment long historical date ranges into ≤30-day blocks
  - Integrate with the CircuitBreaker to fail fast when the API is down
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from src.logger_config import get_logger
from src.models import RegionConfig
from src.utils import CircuitBreaker, CircuitBreakerOpen, RateLimiter, date_range_segments

logger = get_logger("extractor")


class USGSExtractor:
    """
    Extracts seismic events from the USGS Earthquake Hazards Program API.
    Writes raw JSON directly to disk.
    """

    def __init__(self, cfg: Dict[str, Any], circuit_breaker: CircuitBreaker) -> None:
        api_cfg = cfg.get("api", {})
        self.base_url: str = api_cfg.get("base_url", "https://earthquake.usgs.gov/fdsnws/event/1/query")
        self.timeout: int = int(api_cfg.get("request_timeout_seconds", 30))
        self.max_retries: int = int(api_cfg.get("max_retries", 3))
        self.retry_backoff: float = float(api_cfg.get("retry_backoff_seconds", 2.0))
        self.max_segment_days: int = int(api_cfg.get("max_segment_days", 30))
        self.result_limit: int = int(api_cfg.get("result_limit", 20000))

        rate_secs = float(api_cfg.get("rate_limit_seconds", 1.0))
        self.rate_limiter = RateLimiter(seconds=rate_secs)
        self.circuit_breaker = circuit_breaker

        # Bronze layer local path
        self.raw_data_dir = Path("data/raw/usgs")
        self.raw_data_dir.mkdir(parents=True, exist_ok=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Public extraction methods
    # ─────────────────────────────────────────────────────────────────────────

    def extract_daily(
        self,
        regions: List[RegionConfig],
        lookback_hours: int = 24,
        min_magnitude: float = 1.0,
    ) -> List[str]:
        """Extract events from the last N hours for all (or one) regions."""
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=lookback_hours)
        saved_files: List[str] = []

        for region in regions:
            logger.info("Daily extract | region=%s | last %dh | mag≥%.1f",
                        region.region_id, lookback_hours, min_magnitude)
            files = self._fetch_region(
                region=region,
                start=start,
                end=now,
                min_magnitude=min_magnitude,
                mode="daily"
            )
            saved_files.extend(files)

        logger.info("Daily extract complete: %d total files saved", len(saved_files))
        return saved_files

    def extract_alert(
        self,
        lookback_hours: int = 1,
        min_magnitude: float = 4.5,
    ) -> List[str]:
        """Extract global events from the last hour with mag ≥ 4.5 (no region filter)."""
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=lookback_hours)

        logger.info("Alert extract | last %dh | global | mag≥%.1f",
                    lookback_hours, min_magnitude)

        params = self._build_params(
            start=start,
            end=now,
            min_magnitude=min_magnitude,
        )
        files = self._fetch_with_retry(params, region_id="global", mode="alert")
        logger.info("Alert extract complete: %d files saved", len(files))
        return files

    def extract_historical(
        self,
        regions: List[RegionConfig],
        start_date: datetime,
        end_date: datetime,
        min_magnitude: float = 2.5,
    ) -> List[str]:
        """
        Extract historical data for a (possibly long) date range.
        Automatically segments into ≤30-day blocks to respect USGS limits.
        """
        saved_files: List[str] = []

        for region in regions:
            logger.info(
                "Historical extract | region=%s | %s → %s | mag≥%.1f",
                region.region_id,
                start_date.date(),
                end_date.date(),
                min_magnitude,
            )

            for seg_start, seg_end in date_range_segments(
                start_date.date(), end_date.date(), max_days=self.max_segment_days
            ):
                seg_start_dt = datetime.combine(seg_start, datetime.min.time()).replace(
                    tzinfo=timezone.utc
                )
                seg_end_dt = datetime.combine(seg_end, datetime.max.time()).replace(
                    tzinfo=timezone.utc
                )
                logger.debug(
                    "  Segment %s → %s for region %s",
                    seg_start, seg_end, region.region_id,
                )
                files = self._fetch_region(
                    region=region,
                    start=seg_start_dt,
                    end=seg_end_dt,
                    min_magnitude=min_magnitude,
                    mode="historical"
                )
                saved_files.extend(files)
                # Rate limit between segments to avoid saturating USGS
                self.rate_limiter.wait()

        logger.info("Historical extract complete: %d total files saved", len(saved_files))
        return saved_files

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _fetch_region(
        self,
        region: RegionConfig,
        start: datetime,
        end: datetime,
        min_magnitude: float,
        mode: str
    ) -> List[str]:
        """Build params for a specific region bounding box and fetch."""
        params = self._build_params(
            start=start,
            end=end,
            min_magnitude=min_magnitude,
            min_lat=region.min_lat,
            max_lat=region.max_lat,
            min_lon=region.min_lon,
            max_lon=region.max_lon,
        )
        return self._fetch_with_retry(params, region_id=region.region_id, mode=mode)

    def _build_params(
        self,
        start: datetime,
        end: datetime,
        min_magnitude: float,
        min_lat: Optional[float] = None,
        max_lat: Optional[float] = None,
        min_lon: Optional[float] = None,
        max_lon: Optional[float] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "format": "geojson",
            "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "endtime": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "minmagnitude": min_magnitude,
            "orderby": "time",
            "limit": self.result_limit,
        }
        if min_lat is not None:
            params["minlatitude"] = min_lat
        if max_lat is not None:
            params["maxlatitude"] = max_lat
        if min_lon is not None:
            params["minlongitude"] = min_lon
        if max_lon is not None:
            params["maxlongitude"] = max_lon
        return params

    def _fetch_with_retry(
        self,
        params: Dict[str, Any],
        region_id: str,
        mode: str
    ) -> List[str]:
        """
        Execute an API request with retry + exponential backoff.
        Integrates with the circuit breaker and rate limiter.
        Saves raw JSON directly to disk. Returns a list containing the file path.
        """
        self.circuit_breaker.check()

        last_exc: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            self.rate_limiter.wait()
            t0 = time.monotonic()
            try:
                response = requests.get(
                    self.base_url, params=params, timeout=self.timeout
                )

                elapsed_ms = int((time.monotonic() - t0) * 1000)

                # ── HTTP 429 — rate limited by USGS ─────────────────────────
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    logger.warning(
                        "HTTP 429 (Too Many Requests) for region=%s. "
                        "Waiting %ds (Retry-After header).",
                        region_id, retry_after,
                    )
                    self.circuit_breaker.record_failure()
                    time.sleep(retry_after)
                    continue

                # ── Server errors (5xx) ──────────────────────────────────────
                if response.status_code >= 500:
                    raise requests.HTTPError(
                        f"Server error {response.status_code}", response=response
                    )

                response.raise_for_status()

                # ── Save Raw JSON ───────────────────────────────────────────
                raw = response.json()
                total = raw.get("metadata", {}).get("count") or len(raw.get("features", []))
                
                logger.info(
                    "region=%s | status=%d | events=%d | time=%dms",
                    region_id, response.status_code, total, elapsed_ms,
                )

                now = datetime.now(timezone.utc)
                # s3://datalake/bronze/usgs_seismic/year=2024/month=05/day=16/
                out_dir = self.raw_data_dir / mode / f"year={now.year}" / f"month={now.month:02d}" / f"day={now.day:02d}"
                out_dir.mkdir(parents=True, exist_ok=True)
                
                filename = f"{region_id}_{int(now.timestamp())}_{total}.json"
                file_path = out_dir / filename
                
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(raw, f, separators=(',', ':'))

                logger.debug("Raw data saved to %s", file_path)

                self.circuit_breaker.record_success()
                return [str(file_path)]

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                logger.warning(
                    "Attempt %d/%d failed for region=%s: %s (%dms)",
                    attempt, self.max_retries, region_id, exc, elapsed_ms,
                )
                last_exc = exc
                self.circuit_breaker.record_failure()
                if self.circuit_breaker.state == "open":
                    raise CircuitBreakerOpen(
                        f"Circuit breaker OPEN after failure on region={region_id}"
                    )
            except requests.HTTPError as exc:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                logger.warning(
                    "Attempt %d/%d HTTP error for region=%s: %s (%dms)",
                    attempt, self.max_retries, region_id, exc, elapsed_ms,
                )
                last_exc = exc
                self.circuit_breaker.record_failure()
                if self.circuit_breaker.state == "open":
                    raise CircuitBreakerOpen(
                        f"Circuit breaker OPEN after HTTP error on region={region_id}"
                    )
            except Exception as exc:
                logger.error("Unexpected error for region=%s: %s", region_id, exc)
                last_exc = exc
                self.circuit_breaker.record_failure()
                if self.circuit_breaker.state == "open":
                    raise CircuitBreakerOpen(
                        f"Circuit breaker OPEN after unexpected error on region={region_id}"
                    )

            if attempt < self.max_retries:
                backoff = self.retry_backoff * (2 ** (attempt - 1))
                logger.info("Backoff: waiting %.1fs before attempt %d", backoff, attempt + 1)
                time.sleep(backoff)

        logger.error(
            "All %d retries exhausted for region=%s", self.max_retries, region_id
        )
        if last_exc:
            raise last_exc
        return []
