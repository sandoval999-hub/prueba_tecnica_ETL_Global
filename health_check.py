"""
health_check.py — Pipeline health verification script.

Checks:
  1. MySQL is reachable and seismic_db is accessible
  2. Last pipeline run was successful
  3. Circuit breaker is CLOSED
  4. Quarantine table does not have unreviewed records older than 24h

Usage:
  python health_check.py
  python health_check.py --verbose
  python health_check.py --reset-circuit-breaker

Exit codes:
  0 — All checks passed
  1 — One or more checks failed
"""

from __future__ import annotations

import argparse
import sys


from src.logger_config import get_logger, setup_logging
from src.utils import CircuitBreaker, get_db_url, load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seismic ETL Pipeline Health Check")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument(
        "--reset-circuit-breaker",
        action="store_true",
        help="Reset an open circuit breaker (use only after ops investigation).",
    )
    return parser.parse_args()


def check_database(db_url: str, logger) -> bool:
    """Verify MySQL connectivity and that seismic_db tables exist."""
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()")
            ).scalar()
        logger.info("✅ DB check passed — %d tables found in seismic_db", result)
        engine.dispose()
        return True
    except Exception as exc:
        logger.error("❌ DB check FAILED: %s", exc)
        return False


def check_last_run(db_url: str, logger) -> bool:
    """Verify the last pipeline run completed successfully."""
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT run_id, mode, end_time, status, error_message
                    FROM log_ejecuciones
                    ORDER BY run_id DESC
                    LIMIT 1
                    """
                )
            ).fetchone()

        if row is None:
            logger.warning("⚠️  No pipeline runs found — has the pipeline ever run?")
            engine.dispose()
            return True  # First run — not a failure

        run_id, mode, end_time, status, error_msg = row
        if status == "success":
            logger.info(
                "✅ Last run check passed — run_id=%d mode=%s ended=%s",
                run_id, mode, end_time,
            )
            engine.dispose()
            return True
        else:
            logger.error(
                "❌ Last run FAILED — run_id=%d status=%s error=%s",
                run_id, status, error_msg,
            )
            engine.dispose()
            return False
    except Exception as exc:
        logger.error("❌ Last run check FAILED: %s", exc)
        return False


def check_circuit_breaker(cfg: dict, logger, reset: bool = False) -> bool:
    """Verify the circuit breaker is CLOSED (API is reachable)."""
    cb_cfg = cfg.get("circuit_breaker", {})
    cb = CircuitBreaker(
        state_file=cb_cfg.get("state_file", "logs/circuit_breaker_state.json"),
        failure_threshold=int(cb_cfg.get("failure_threshold", 3)),
    )

    if reset:
        cb.reset()
        logger.info("🔄 Circuit breaker reset to CLOSED.")

    if cb.state == "closed":
        logger.info("✅ Circuit breaker is CLOSED — API USGS reachable")
        return True
    else:
        last_failure = cb._state.get("last_failure", "unknown")
        logger.error(
            "❌ Circuit breaker is %s — last failure: %s",
            cb.state, last_failure,
        )
        return False


def check_quarantine(db_url: str, logger) -> bool:
    """Warn if there are unreviewed quarantine records older than 24h."""
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            count = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM eventos_cuarentena
                    WHERE reviewed = 0
                      AND created_at < DATE_SUB(NOW(), INTERVAL 24 HOUR)
                    """
                )
            ).scalar()

        engine.dispose()

        if count == 0:
            logger.info("✅ Quarantine check passed — no stale unreviewed records")
            return True
        else:
            logger.warning(
                "⚠️  %d unreviewed quarantine record(s) older than 24h need DQ review.", count
            )
            return True  # Warning only — not a failure for health check

    except Exception as exc:
        logger.error("❌ Quarantine check FAILED: %s", exc)
        return False


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)
    log_cfg = cfg.get("logging", {})
    setup_logging(
        log_dir=log_cfg.get("log_dir", "logs"),
        log_file="health_check.log",
        level=log_cfg.get("level", "INFO"),
        verbose=args.verbose,
    )
    logger = get_logger("health_check")

    logger.info("=" * 50)
    logger.info("Atlas RA Seismic Pipeline — Health Check")
    logger.info("=" * 50)

    db_url = get_db_url(cfg)
    results = []

    results.append(("Database connectivity", check_database(db_url, logger)))
    results.append(("Last run status", check_last_run(db_url, logger)))
    results.append(
        ("Circuit breaker",
         check_circuit_breaker(cfg, logger, reset=args.reset_circuit_breaker))
    )
    results.append(("Quarantine records", check_quarantine(db_url, logger)))

    logger.info("=" * 50)
    all_ok = all(ok for _, ok in results)

    for name, ok in results:
        status = "✅ PASS" if ok else "❌ FAIL"
        logger.info("  %s — %s", status, name)

    if all_ok:
        logger.info("Overall: 🟢 HEALTHY")
    else:
        logger.error("Overall: 🔴 UNHEALTHY — check logs above")

    logger.info("=" * 50)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
