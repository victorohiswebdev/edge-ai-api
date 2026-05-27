"""System status endpoint — reports pipeline health for the dashboard."""

from fastapi import APIRouter, Depends
from sqlite3 import Connection
from datetime import datetime, timedelta
from database import get_db

router = APIRouter()


@router.get("/system/status")
def get_system_status(db: Connection = Depends(get_db)):
    """Return the overall system health for the dashboard data source indicator.

    Returns:
      - database_connected: always true if this responds
      - logger_active: true if data_logger.py heartbeat within last 6 minutes
      - last_reading: timestamp of the most recent sensor_logs entry
      - total_readings: row count in sensor_logs
    """
    now = datetime.utcnow()

    # ── Logger heartbeat check ──
    six_min_ago = now - timedelta(minutes=6)
    last_heartbeat = db.execute(
        "SELECT timestamp FROM system_log "
        "WHERE event = 'logger_heartbeat' AND timestamp >= ? "
        "ORDER BY timestamp DESC LIMIT 1",
        (six_min_ago.isoformat(),),
    ).fetchone()

    logger_active = last_heartbeat is not None

    # ── Latest reading ──
    last_reading_row = db.execute(
        "SELECT timestamp FROM sensor_logs ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()

    # ── Total count ──
    count_row = db.execute(
        "SELECT COUNT(*) AS cnt FROM sensor_logs"
    ).fetchone()

    return {
        "database_connected": True,
        "logger_active": logger_active,
        "last_reading": last_reading_row["timestamp"] if last_reading_row else None,
        "total_readings": count_row["cnt"],
    }
