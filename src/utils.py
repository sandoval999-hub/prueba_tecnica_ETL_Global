"""
src/utils.py — Cross-cutting utilities for the seismic ETL pipeline.

Includes:
  - CircuitBreaker  : prevents hammering a failing API
  - retry_with_backoff : decorator for HTTP calls
  - RateLimiter       : enforces per-request delay
  - MySQLAdvisoryLock : prevents parallel pipeline instances
  - load_config       : YAML + .env loader
  - date_range_segments: splits long date ranges into 30-day blocks
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

import yaml
from dotenv import load_dotenv
from sqlalchemy import text

from src.logger_config import get_logger

logger = get_logger("utils")


# ─────────────────────────────────────────────────────────────────────────────
# Config loader
# ─────────────────────────────────────────────────────────────────────────────

def load_config(config_path: str = "config/config.yaml") -> Dict[str, Any]:
    """Load YAML config file and inject environment variables from .env."""
    # Look for .env in repo root and config/
    for env_path in [".env", "config/.env"]:
        if Path(env_path).exists():
            load_dotenv(env_path)
            logger.debug("Loaded environment variables from %s", env_path)

    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    logger.debug("Configuration loaded from %s", config_path)
    return cfg


def get_db_url(cfg: Dict[str, Any]) -> str:
    """Build SQLAlchemy connection URL from environment variables."""
    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = os.environ.get("DB_PORT", "3306")
    name = os.environ.get("DB_NAME", "seismic_db")
    user = os.environ.get("DB_USER", "root")
    password = os.environ.get("DB_PASSWORD", "")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset=utf8mb4"


# ─────────────────────────────────────────────────────────────────────────────
# Date range segmentation
# ─────────────────────────────────────────────────────────────────────────────

def date_range_segments(
    start: date,
    end: date,
    max_days: int = 30,
) -> Generator[Tuple[date, date], None, None]:
    """
    Yield (segment_start, segment_end) pairs covering [start, end].
    Each segment spans at most max_days days.

    Example: 2024-01-01 → 2024-06-30 with max_days=30 yields
    (2024-01-01, 2024-01-31), (2024-02-01, 2024-03-01), …
    """
    current = start
    while current < end:
        segment_end = min(current + timedelta(days=max_days), end)
        yield current, segment_end
        current = segment_end + timedelta(days=1)


# ─────────────────────────────────────────────────────────────────────────────
# Circuit Breaker
# ─────────────────────────────────────────────────────────────────────────────

class CircuitBreakerOpen(Exception):
    """Raised when a request is attempted while the circuit is open."""


class CircuitBreaker:
    """
    Simple three-state circuit breaker persisted to a JSON file.

    States:
      closed    — normal operation
      open      — too many consecutive failures; reject calls immediately
      half-open — (future) would probe with a single call; here we just
                  auto-reset on pipeline startup after checking the file.

    The state is stored in logs/circuit_breaker_state.json so it survives
    process restarts and can be inspected / reset by ops.
    """

    _CLOSED = "closed"
    _OPEN = "open"
    _HALF_OPEN = "half-open"

    def __init__(self, state_file: str, failure_threshold: int = 3) -> None:
        self.state_file = Path(state_file)
        self.failure_threshold = failure_threshold
        self._state: Dict[str, Any] = self._load_state()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load_state(self) -> Dict[str, Any]:
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError):
                pass
        return {"state": self._CLOSED, "failures": 0, "last_failure": None}

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as fh:
            json.dump(self._state, fh, default=str, indent=2)

    # ── public interface ──────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state["state"]

    @property
    def failures(self) -> int:
        return self._state.get("failures", 0)

    def check(self) -> None:
        """
        Call before making an API request.
        Raises CircuitBreakerOpen if the circuit is open.
        """
        if self._state["state"] == self._OPEN:
            last = self._state.get("last_failure", "unknown")
            raise CircuitBreakerOpen(
                f"Circuit breaker OPEN: API USGS no disponible. Última falla: {last}"
            )

    def record_success(self) -> None:
        """Reset failure counter on a successful call."""
        if self._state["failures"] > 0 or self._state["state"] != self._CLOSED:
            logger.info("CircuitBreaker: recording success, resetting to closed")
        self._state = {"state": self._CLOSED, "failures": 0, "last_failure": None}
        self._save_state()

    def record_failure(self) -> None:
        """Increment failure counter; open circuit if threshold is reached."""
        self._state["failures"] = self._state.get("failures", 0) + 1
        self._state["last_failure"] = datetime.now(timezone.utc).isoformat()
        if self._state["failures"] >= self.failure_threshold:
            if self._state["state"] != self._OPEN:
                logger.error(
                    "CircuitBreaker: threshold %d reached — circuit OPEN",
                    self.failure_threshold,
                )
            self._state["state"] = self._OPEN
        else:
            self._state["state"] = self._HALF_OPEN
            logger.warning(
                "CircuitBreaker: failure %d/%d — state = half-open",
                self._state["failures"],
                self.failure_threshold,
            )
        self._save_state()

    def reset(self) -> None:
        """Manually reset (e.g., called by health_check.py after ops intervention)."""
        logger.info("CircuitBreaker: manual reset → closed")
        self._state = {"state": self._CLOSED, "failures": 0, "last_failure": None}
        self._save_state()


# ─────────────────────────────────────────────────────────────────────────────
# Rate Limiter
# ─────────────────────────────────────────────────────────────────────────────

class RateLimiter:
    """Enforce a minimum delay between consecutive API calls."""

    def __init__(self, seconds: float = 1.0) -> None:
        self.delay = seconds
        self._last_call: float = 0.0

    def wait(self) -> None:
        """Sleep if necessary to honour the rate limit."""
        elapsed = time.monotonic() - self._last_call
        remaining = self.delay - elapsed
        if remaining > 0:
            logger.debug("RateLimiter: sleeping %.2fs", remaining)
            time.sleep(remaining)
        self._last_call = time.monotonic()


# ─────────────────────────────────────────────────────────────────────────────
# File Lock context manager (Decoupled from Database)
# ─────────────────────────────────────────────────────────────────────────────

class FileLock:
    """
    Acquire a local file lock to prevent parallel pipeline instances.

    Usage:
        with FileLock("logs/pipeline.lock") as lock:
            run_pipeline()
    """

    def __init__(self, lock_file: str = "logs/pipeline.lock") -> None:
        self.lock_file = Path(lock_file)
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> "FileLock":
        if self.lock_file.exists():
            raise RuntimeError(
                f"Pipeline ya en ejecución. Lockfile existe: {self.lock_file}"
            )
        self.lock_file.touch()
        logger.info("File lock '%s' acquired", self.lock_file)
        return self

    def __exit__(self, *_: Any) -> None:
        if self.lock_file.exists():
            self.lock_file.unlink()
            logger.info("File lock '%s' released", self.lock_file)


# ─────────────────────────────────────────────────────────────────────────────
# Retry with exponential backoff (used inside extractor)
# ─────────────────────────────────────────────────────────────────────────────

def retry_with_backoff(
    func: Any,
    max_retries: int = 3,
    initial_backoff: float = 2.0,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """
    Call func(*args, **kwargs) up to max_retries times on failure.
    Backoff doubles each attempt: initial, initial*2, initial*4, …

    Raises the last exception if all retries are exhausted.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            wait = initial_backoff * (2 ** attempt)
            logger.warning(
                "Retry %d/%d after error: %s. Waiting %.1fs…",
                attempt + 1,
                max_retries,
                exc,
                wait,
            )
            time.sleep(wait)
    raise last_exc  # type: ignore[misc]
