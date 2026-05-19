from typing import List, Tuple, Any, Dict
from datetime import datetime, timezone


from dagster import asset, Config, AssetExecutionContext

from src.extractor import USGSExtractor
from src.transformer import SeismicTransformer
from src.loader import SeismicLoader
from src.aggregator import DailyAggregator
from src.reporter import generate_report
from src.utils import load_config, get_db_url, CircuitBreaker, FileLock
from src.models import SeismicEvent, QuarantineRecord, PipelineRun
from main import build_regions

class PipelineConfig(Config):
    mode: str = "daily"
    start_date: str | None = None
    end_date: str | None = None
    region: str | None = None
    config_path: str = "config/config.yaml"

@asset(description="Extrae los datos en crudo desde la API de USGS a la capa Bronze (Archivos JSON locales)")
def raw_usgs_events(context: AssetExecutionContext, config: PipelineConfig) -> List[str]:
    cfg = load_config(config.config_path)
    regions = build_regions(cfg, config.region)
    
    cb_cfg = cfg.get("circuit_breaker", {})
    circuit_breaker = CircuitBreaker(
        state_file=cb_cfg.get("state_file", "logs/circuit_breaker_state.json"),
        failure_threshold=int(cb_cfg.get("failure_threshold", 3)),
    )
    
    extractor = USGSExtractor(cfg, circuit_breaker)
    pipeline_cfg = cfg.get("pipeline", {})
    
    file_paths = []
    
    if config.mode == "daily":
        file_paths = extractor.extract_daily(
            regions=regions,
            lookback_hours=int(pipeline_cfg.get("daily_lookback_hours", 24)),
            min_magnitude=float(pipeline_cfg.get("daily_min_magnitude", 1.0)),
        )
    elif config.mode == "alert":
        file_paths = extractor.extract_alert(
            lookback_hours=int(pipeline_cfg.get("alert_lookback_hours", 1)),
            min_magnitude=float(pipeline_cfg.get("alert_min_magnitude", 4.5)),
        )
    elif config.mode == "historical":
        start_dt = datetime.strptime(config.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(config.end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        file_paths = extractor.extract_historical(
            regions=regions,
            start_date=start_dt,
            end_date=end_dt,
            min_magnitude=float(pipeline_cfg.get("historical_min_magnitude", 2.5)),
        )
        
    context.log.info(f"Extract phase complete | {len(file_paths)} files saved")
    return file_paths

@asset(description="Lee la capa Bronze, valida con Pydantic, enriquece y retorna Dataframes/Eventos válidos")
def transformed_events(context: AssetExecutionContext, config: PipelineConfig, raw_usgs_events: List[str]) -> Tuple[List[SeismicEvent], List[Dict[str, Any]], List[QuarantineRecord]]:
    cfg = load_config(config.config_path)
    regions = build_regions(cfg, config.region)
    
    transformer = SeismicTransformer(cfg, regions)
    events, quality_entries, quarantine_records = transformer.transform(
        raw_usgs_events, pipeline_mode=config.mode
    )
    
    context.log.info(f"Transform complete | valid={len(events)} discarded={len(quality_entries)} quarantined={len(quarantine_records)}")
    return events, quality_entries, quarantine_records

@asset(description="Carga los eventos transformados a la capa Silver/Gold en MySQL")
def loaded_events(context: AssetExecutionContext, config: PipelineConfig, transformed_events: Tuple[List[SeismicEvent], List[Dict[str, Any]], List[QuarantineRecord]]) -> int:
    events, quality_entries, quarantine_records = transformed_events
    cfg = load_config(config.config_path)
    regions = build_regions(cfg, config.region)
    db_url = get_db_url(cfg)
    loader = SeismicLoader(db_url, cfg)
    
    # We acquire the file lock around the DB load
    loaded = 0
    with FileLock("logs/pipeline.lock"):
        run = PipelineRun(
            run_id=None,
            mode=config.mode,
            start_time=datetime.utcnow(),
            end_time=None,
            status="running",
            regions_processed=len(regions),
            events_extracted=0, # not fully tracked here without raw paths length, could be fixed
            events_loaded=0,
            events_discarded=len(quality_entries),
            events_quarantined=len(quarantine_records),
            circuit_breaker_state="closed",
            error_message=None,
        )
        
        run_id = loader.log_run_start(run)
        loaded = loader.load_events(events, run_id=run_id)
        loader.load_quality_entries(quality_entries, run_id, config.mode)
        loader.load_quarantine(quarantine_records)
        tsunami_count = loader.load_tsunami_alerts(events, run_id)
        
        run.end_time = datetime.utcnow()
        run.status = "success"
        run.events_loaded = loaded
        loader.log_run_end(run_id, run)
        
    loader.dispose()
    
    context.log.info(f"Pipeline LOAD SUCCESS | loaded={loaded} tsunami_alerts={tsunami_count}")
    return loaded

@asset(description="Genera métricas de reportes y recalcula promedios")
def daily_aggregations(context: AssetExecutionContext, config: PipelineConfig, transformed_events: Tuple[List[SeismicEvent], List[Dict[str, Any]], List[QuarantineRecord]], loaded_events: int) -> str:
    events, quality_entries, quarantine_records = transformed_events
    cfg = load_config(config.config_path)
    regions = build_regions(cfg, config.region)
    db_url = get_db_url(cfg)
    
    loader = SeismicLoader(db_url, cfg)
    aggregator = DailyAggregator(loader.engine, regions)
    
    affected_dates = list({e.event_time.strftime("%Y-%m-%d") for e in events})
    aggregator.recalculate(affected_dates=affected_dates if affected_dates else None)
    loader.dispose()
    
    if events:
        report_meta = {
            "mode": config.mode,
            "run_id": 9999, # Dummy or fetched from loader
            "start_date": config.start_date,
            "end_date": config.end_date,
        }
        generate_report(events, quality_entries, quarantine_records, report_meta, cfg)
        
    return "Aggregations and report complete"
