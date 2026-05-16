"""
src/logger_config.py — Centralised logging configuration for the
seismic ETL pipeline.

Format: [2025-05-12 14:32:01] [INFO] [extractor] Message
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path


_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False  # guard against double-init


def setup_logging(
    log_dir: str = "logs",
    log_file: str = "pipeline.log",
    level: str = "INFO",
    max_bytes: int = 10_485_760,   # 10 MB
    backup_count: int = 5,
    verbose: bool = False,
) -> None:
    """
    Configure root logger with two handlers:
    - StreamHandler  → console (stdout)
    - RotatingFileHandler → logs/<log_file>

    Call once from main.py before any module imports a logger.
    """
    global _configured
    if _configured:
        return

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_dir) / log_file

    effective_level = logging.DEBUG if verbose else getattr(logging, level.upper(), logging.INFO)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # ── Console handler ──────────────────────────────────────────────────
    console_handler = logging.StreamHandler()
    console_handler.setLevel(effective_level)
    console_handler.setFormatter(formatter)

    # ── Rotating file handler ─────────────────────────────────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)   # always write DEBUG to file
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)           # root captures everything; handlers filter
    root.addHandler(console_handler)
    root.addHandler(file_handler)

    # Silence noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger.  Call after setup_logging()."""
    return logging.getLogger(name)
