"""
src/aggregator.py — Calculate and persist daily statistics per region.

`estadisticas_diarias` is fully recalculated after each pipeline run.
It aggregates from the `terremotos` table directly so that historical
backfills automatically update the statistics without duplicates.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import text

from src.logger_config import get_logger
from src.models import RegionConfig

logger = get_logger("aggregator")


class DailyAggregator:
    """
    Recalculates and replaces daily statistics in `estadisticas_diarias`.

    Parameters
    ----------
    engine : SQLAlchemy Engine
        Shared from SeismicLoader.engine.
    regions : List[RegionConfig]
        List of configured monitoring regions.
    """

    def __init__(self, engine: Any, regions: List[RegionConfig]) -> None:
        self.engine = engine
        self.regions = regions

    def recalculate(self, affected_dates: Optional[List[str]] = None) -> int:
        """
        Recalculate daily statistics.

        If affected_dates is provided (list of 'YYYY-MM-DD' strings), only
        recalculate for those dates. Otherwise recalculates everything
        (expensive — use only after a large historical backfill).

        Returns the number of (region, date) rows upserted.
        """
        region_ids = [r.region_id for r in self.regions] + ["global_other"]

        if affected_dates:
            date_filter = "AND DATE(t.event_time) IN :dates"
            params: Dict[str, Any] = {"dates": tuple(affected_dates)}
        else:
            date_filter = ""
            params = {}

        count = 0
        for region_id in region_ids:
            count += self._upsert_for_region(region_id, date_filter, params, affected_dates)

        logger.info("Daily stats recalculated | %d region-date rows upserted.", count)
        return count

    def _upsert_for_region(
        self,
        region_id: str,
        date_filter: str,
        params: Dict[str, Any],
        affected_dates: Optional[List[str]],
    ) -> int:
        """Compute aggregates for one region and upsert into estadisticas_diarias."""

        query_params = dict(params)
        query_params["region_id"] = region_id

        agg_sql = text(
            f"""
            SELECT
                region_id,
                DATE(event_time)                                        AS stat_date,
                COUNT(*)                                                AS total_events,
                MAX(magnitude)                                          AS max_magnitude,
                AVG(magnitude)                                          AS avg_magnitude,
                AVG(depth_km)                                           AS avg_depth_km,
                SUM(CASE WHEN magnitude_class = 'micro'    THEN 1 ELSE 0 END) AS count_micro,
                SUM(CASE WHEN magnitude_class = 'minor'    THEN 1 ELSE 0 END) AS count_minor,
                SUM(CASE WHEN magnitude_class = 'light'    THEN 1 ELSE 0 END) AS count_light,
                SUM(CASE WHEN magnitude_class = 'moderate' THEN 1 ELSE 0 END) AS count_moderate,
                SUM(CASE WHEN magnitude_class = 'strong'   THEN 1 ELSE 0 END) AS count_strong,
                SUM(CASE WHEN magnitude_class = 'major'    THEN 1 ELSE 0 END) AS count_major,
                SUM(CASE WHEN magnitude_class = 'great'    THEN 1 ELSE 0 END) AS count_great,
                MAX(risk_score)                                         AS max_risk_score
            FROM terremotos t
            WHERE region_id = :region_id
            {date_filter}
            GROUP BY region_id, DATE(event_time)
            """
        )

        upsert_sql = text(
            """
            INSERT INTO estadisticas_diarias (
                region_id, stat_date, total_events, max_magnitude, avg_magnitude,
                avg_depth_km,
                count_micro, count_minor, count_light, count_moderate,
                count_strong, count_major, count_great,
                max_risk_score
            ) VALUES (
                :region_id, :stat_date, :total_events, :max_magnitude, :avg_magnitude,
                :avg_depth_km,
                :count_micro, :count_minor, :count_light, :count_moderate,
                :count_strong, :count_major, :count_great,
                :max_risk_score
            )
            ON DUPLICATE KEY UPDATE
                total_events   = VALUES(total_events),
                max_magnitude  = VALUES(max_magnitude),
                avg_magnitude  = VALUES(avg_magnitude),
                avg_depth_km   = VALUES(avg_depth_km),
                count_micro    = VALUES(count_micro),
                count_minor    = VALUES(count_minor),
                count_light    = VALUES(count_light),
                count_moderate = VALUES(count_moderate),
                count_strong   = VALUES(count_strong),
                count_major    = VALUES(count_major),
                count_great    = VALUES(count_great),
                max_risk_score = VALUES(max_risk_score)
            """
        )

        with self.engine.begin() as conn:
            rows = conn.execute(agg_sql, query_params).fetchall()
            if not rows:
                return 0

            upsert_params = [
                {
                    "region_id": row[0],
                    "stat_date": row[1],
                    "total_events": int(row[2]),
                    "max_magnitude": float(row[3]) if row[3] else None,
                    "avg_magnitude": float(row[4]) if row[4] else None,
                    "avg_depth_km": float(row[5]) if row[5] else None,
                    "count_micro": int(row[6]),
                    "count_minor": int(row[7]),
                    "count_light": int(row[8]),
                    "count_moderate": int(row[9]),
                    "count_strong": int(row[10]),
                    "count_major": int(row[11]),
                    "count_great": int(row[12]),
                    "max_risk_score": float(row[13]) if row[13] else None,
                }
                for row in rows
            ]
            conn.execute(upsert_sql, upsert_params)
            return len(upsert_params)
