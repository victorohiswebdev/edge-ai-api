"""GET endpoints — sensor data for the dashboard."""

from fastapi import APIRouter, Depends, Query
from sqlite3 import Connection
from datetime import datetime, timedelta
from database import get_db
from schemas import LatestReading, SensorSummary

router = APIRouter()


@router.get("/sensors/latest", response_model=LatestReading)
def get_latest(db: Connection = Depends(get_db)):
    """Return the most recent sensor reading for the live dashboard view."""
    row = db.execute(
        "SELECT * FROM sensor_logs ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()

    if not row:
        return {
            "moisture_zone_1": None,
            "moisture_zone_2": None,
            "moisture_zone_3": None,
            "temperature_c": None,
            "humidity_perc": None,
            "timestamp": datetime.utcnow(),
        }

    return {
        "moisture_zone_1": row["moisture_zone_1"],
        "moisture_zone_2": row["moisture_zone_2"],
        "moisture_zone_3": row["moisture_zone_3"],
        "temperature_c": row["temperature_c"],
        "humidity_perc": row["humidity_perc"],
        "timestamp": row["timestamp"],
    }


@router.get("/sensors/history")
def get_history(
    hours: int = Query(24, description="How far back to fetch (hours)"),
    limit: int = Query(200, description="Max number of readings"),
    db: Connection = Depends(get_db),
):
    """Return historical readings for time-series charts."""
    since = datetime.utcnow() - timedelta(hours=hours)
    rows = db.execute(
        """SELECT * FROM sensor_logs
           WHERE timestamp >= ?
           ORDER BY timestamp DESC
           LIMIT ?""",
        (since.isoformat(), limit),
    ).fetchall()

    return [dict(r) for r in rows]


@router.get("/sensors/summary", response_model=SensorSummary)
def get_summary(
    hours: int = Query(24, description="Time window for aggregation"),
    db: Connection = Depends(get_db),
):
    """Return averaged sensor values over a time window."""
    since = datetime.utcnow() - timedelta(hours=hours)

    row = db.execute(
        """SELECT
               AVG(moisture_zone_1) AS avg_m1,
               AVG(moisture_zone_2) AS avg_m2,
               AVG(moisture_zone_3) AS avg_m3,
               AVG(temperature_c)   AS avg_temp,
               AVG(humidity_perc)   AS avg_hum,
               COUNT(*)             AS count
           FROM sensor_logs
           WHERE timestamp >= ?""",
        (since.isoformat(),),
    ).fetchone()

    return {
        "avg_moisture_1": row["avg_m1"],
        "avg_moisture_2": row["avg_m2"],
        "avg_moisture_3": row["avg_m3"],
        "avg_temperature": row["avg_temp"],
        "avg_humidity": row["avg_hum"],
        "reading_count": row["count"],
        "from_timestamp": since,
        "to_timestamp": datetime.utcnow(),
    }
