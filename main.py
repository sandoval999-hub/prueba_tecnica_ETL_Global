"""
main.py — CLI entry point for the Atlas RA Seismic ETL Pipeline.

Usage examples:
  python main.py --mode daily
  python main.py --mode alert
  python main.py --mode historical --start-date 2024-01-01 --end-date 2024-06-30
  python main.py --mode daily --region japan
  python main.py --mode daily --dry-run
  python main.py --mode daily --verbose
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import List, Optional

from src.logger_config import get_logger, setup_logging
from src.models import PipelineRun, RegionConfig
from src.utils import (
    CircuitBreaker,
    CircuitBreakerOpen,
    MySQLAdvisoryLock,
    get_db_url,
    load_config,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Atlas RA Seismic ETL Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--mode",
        choices=["daily", "alert", "historical"],
        required=True,
        help="Pipeline execution mode.",
    )
    parser.add_argument(
        "--start-date",
        metavar="YYYY-MM-DD",
        help="Start date for historical mode (inclusive).",
    )
    parser.add_argument(
        "--end-date",
        metavar="YYYY-MM-DD",
        help="End date for historical mode (inclusive).",
    )
    parser.add_argument(
        "--region",
        metavar="REGION_ID",
        help="Process only this region (e.g. japan). Default: all regions.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and transform but do NOT load to MySQL.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging to console.",
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to config.yaml (default: config/config.yaml).",
    )
    parser.add_argument(
        "--reset-circuit-breaker",
        action="store_true",
        help="Reset an open circuit breaker before running.",
    )
    return parser.parse_args()


def build_regions(cfg: dict, region_filter: Optional[str]) -> List[RegionConfig]:
    """Build RegionConfig list from config, optionally filtering to one region."""
    regions_cfg = cfg.get("regions", {})
    regions = [
        RegionConfig(
            region_id=rid,
            display_name=data.get("display_name", rid),
            min_lat=data["min_lat"],
            max_lat=data["max_lat"],
            min_lon=data["min_lon"],
            max_lon=data["max_lon"],
        )
        for rid, data in regions_cfg.items()
    ]

    if region_filter:
        regions = [r for r in regions if r.region_id == region_filter]
        if not regions:
            raise ValueError(
                f"Region '{region_filter}' not found in config. "
                f"Valid regions: {list(regions_cfg.keys())}"
            )

    return regions


def run_pipeline(args: argparse.Namespace) -> int:
    """
    Main pipeline orchestration.
    Returns exit code (0 = success, 1 = error).
    """
    # ── Setup ─────────────────────────────────────────────────────────────────
    cfg = load_config(args.config)

    log_cfg = cfg.get("logging", {})
    setup_logging(
        log_dir=log_cfg.get("log_dir", "logs"),
        log_file=log_cfg.get("log_file", "pipeline.log"),
        level=log_cfg.get("level", "INFO"),
        max_bytes=int(log_cfg.get("max_bytes", 10_485_760)),
        backup_count=int(log_cfg.get("backup_count", 5)),
        verbose=args.verbose,
    )

    logger = get_logger("main")
    logger.info("=" * 60)
    logger.info("Atlas RA Seismic ETL Pipeline starting | mode=%s dry_run=%s", args.mode, args.dry_run)

    # ── Imports after logging is configured ───────────────────────────────────
    from src.aggregator import DailyAggregator
    from src.extractor import USGSExtractor
    from src.loader import SeismicLoader
    from src.reporter import generate_report
    from src.transformer import SeismicTransformer

    # ── Circuit Breaker ───────────────────────────────────────────────────────
    cb_cfg = cfg.get("circuit_breaker", {})
    circuit_breaker = CircuitBreaker(
        state_file=cb_cfg.get("state_file", "logs/circuit_breaker_state.json"),
        failure_threshold=int(cb_cfg.get("failure_threshold", 3)),
    )

    if args.reset_circuit_breaker:
        circuit_breaker.reset()
        logger.info("Circuit breaker manually reset to CLOSED.")

    try:
        circuit_breaker.check()
    except CircuitBreakerOpen as exc:
        logger.error(str(exc))
        return 1

    logger.info("Circuit breaker state: %s", circuit_breaker.state)

    # ── Validate mode-specific args ───────────────────────────────────────────
    if args.mode == "historical":
        if not args.start_date or not args.end_date:
            logger.error("--start-date and --end-date are required for historical mode.")
            return 1
        try:
            start_dt = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end_dt = datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            logger.error("Invalid date format: %s", exc)
            return 1
        if start_dt > end_dt:
            logger.error("--start-date must be before --end-date.")
            return 1
    else:
        start_dt = end_dt = None

    # ── Build regions ─────────────────────────────────────────────────────────
    try:
        regions = build_regions(cfg, args.region)
    except ValueError as exc:
        logger.error(str(exc))
        return 1

    logger.info("Regions to process: %s", [r.region_id for r in regions])

    # ── Initialise components ─────────────────────────────────────────────────
    extractor = USGSExtractor(cfg, circuit_breaker)
    transformer = SeismicTransformer(cfg, regions)

    pipeline_cfg = cfg.get("pipeline", {})

    # ── Extract ───────────────────────────────────────────────────────────────
    logger.info("--- EXTRACT ---")
    raw_features = []

    if args.mode == "daily":
        raw_features = extractor.extract_daily(
            regions=regions,
            lookback_hours=int(pipeline_cfg.get("daily_lookback_hours", 24)),
            min_magnitude=float(pipeline_cfg.get("daily_min_magnitude", 1.0)),
        )
    elif args.mode == "alert":
        raw_features = extractor.extract_alert(
            lookback_hours=int(pipeline_cfg.get("alert_lookback_hours", 1)),
            min_magnitude=float(pipeline_cfg.get("alert_min_magnitude", 4.5)),
        )
    elif args.mode == "historical":
        raw_features = extractor.extract_historical(
            regions=regions,
            start_date=start_dt,
            end_date=end_dt,
            min_magnitude=float(pipeline_cfg.get("historical_min_magnitude", 2.5)),
        )

    logger.info("Extract phase complete | %d raw features", len(raw_features))

    # ── Transform ─────────────────────────────────────────────────────────────
    logger.info("--- TRANSFORM ---")
    events, quality_entries, quarantine_records = transformer.transform(
        raw_features, pipeline_mode=args.mode
    )
    logger.info(
        "Transform complete | valid=%d discarded=%d quarantined=%d",
        len(events), len(quality_entries), len(quarantine_records),
    )

    # ── Dry run: stop here ────────────────────────────────────────────────────
    if args.dry_run:
        logger.info("DRY RUN mode — skipping load. %d events would be inserted.", len(events))
        logger.info("Pipeline complete (dry run).")
        return 0

    # ── Load ──────────────────────────────────────────────────────────────────
    logger.info("--- LOAD ---")
    db_url = get_db_url(cfg)
    loader = SeismicLoader(db_url, cfg)

    run = PipelineRun(
        run_id=None,
        mode=args.mode,
        start_time=datetime.utcnow(),
        end_time=None,
        status="running",
        regions_processed=len(regions),
        events_extracted=len(raw_features),
        events_loaded=0,
        events_discarded=len(quality_entries),
        events_quarantined=len(quarantine_records),
        circuit_breaker_state=circuit_breaker.state,
        error_message=None,
    )

    exit_code = 0
    run_id: Optional[int] = None

    try:
        # Advisory lock ensures only one pipeline instance runs at a time
        with MySQLAdvisoryLock(loader.engine) as _lock:
            run_id = loader.log_run_start(run)
            loaded = loader.load_events(events, run_id=run_id)
            loader.load_quality_entries(quality_entries, run_id, args.mode)
            loader.load_quarantine(quarantine_records)
            tsunami_count = loader.load_tsunami_alerts(events, run_id)

            # Recalculate daily stats for affected dates
            affected_dates = list({e.event_time.strftime("%Y-%m-%d") for e in events})
            aggregator = DailyAggregator(loader.engine, regions)
            aggregator.recalculate(affected_dates=affected_dates if affected_dates else None)

            run.end_time = datetime.utcnow()
            run.status = "success"
            run.events_loaded = loaded
            run.circuit_breaker_state = circuit_breaker.state
            loader.log_run_end(run_id, run)

        logger.info(
            "Pipeline SUCCESS | loaded=%d tsunami_alerts=%d discarded=%d quarantined=%d",
            loaded, tsunami_count, len(quality_entries), len(quarantine_records),
        )

    except RuntimeError as exc:
        # Advisory lock not available
        logger.error(str(exc))
        return 1

    except Exception as exc:
        logger.error("Pipeline FAILED: %s", exc, exc_info=True)
        run.end_time = datetime.utcnow()
        run.status = "error"
        run.error_message = str(exc)
        run.circuit_breaker_state = circuit_breaker.state
        if run_id:
            loader.log_run_end(run_id, run)
        exit_code = 1

    finally:
        loader.dispose()

    # ── Report ────────────────────────────────────────────────────────────────
    if exit_code == 0 and events:
        report_meta = {
            "mode": args.mode,
            "run_id": run_id,
            "start_date": args.start_date,
            "end_date": args.end_date,
        }
        generate_report(events, quality_entries, quarantine_records, report_meta, cfg)

    logger.info("=" * 60)
    return exit_code


if __name__ == "__main__":
    sys.exit(run_pipeline(parse_args()))
