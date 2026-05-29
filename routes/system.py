"""System status endpoint — reports pipeline health for the dashboard."""

import os
import glob
import subprocess
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
        (six_min_ago.strftime("%Y-%m-%d %H:%M:%S"),),
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


def _service_running(name):
    """Check if a systemd service is active."""
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "--quiet", name],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _find_arduino_port():
    """Return the first matching Arduino port or None."""
    for pattern in ["/dev/ttyACM*", "/dev/ttyUSB*"]:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None


def _db_file_size(db_path):
    """Return database file size in MB."""
    try:
        return round(os.path.getsize(db_path) / (1024 * 1024), 2)
    except Exception:
        return None


@router.get("/system/health")
def get_system_health(db: Connection = Depends(get_db)):
    """Full hardware and system health check for the dashboard.

    Returns status for every layer: Arduino, sensors, services, database.
    """
    now = datetime.utcnow()

    # ── Arduino ──
    arduino_port = _find_arduino_port()

    # ── Latest sensor reading ──
    latest = db.execute(
        "SELECT * FROM latest_reading ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()

    last_age = None
    sensors = {
        "temperature": {"status": "unknown"},
        "humidity": {"status": "unknown"},
        "moisture_z1": {"status": "unknown"},
        "moisture_z2": {"status": "unknown"},
        "moisture_z3": {"status": "unknown"},
    }

    if latest:
        try:
            ts = datetime.strptime(latest["timestamp"], "%Y-%m-%d %H:%M:%S")
            last_age = int((now - ts).total_seconds())
        except (ValueError, TypeError):
            last_age = None

        # Sensor sanity checks
        t = latest["temperature_c"]
        h = latest["humidity_perc"]
        m1 = latest["moisture_zone_1"]
        m2 = latest["moisture_zone_2"]
        m3 = latest["moisture_zone_3"]

        sensors["temperature"] = {
            "status": "ok" if t is not None and 15 <= t <= 40 else "error",
            "value": t,
        }
        sensors["humidity"] = {
            "status": "ok" if h is not None and 0 <= h <= 100 else "error",
            "value": round(h, 1) if h is not None else None,
        }
        sensors["moisture_z1"] = {
            "status": "ok" if m1 is not None and 0 <= m1 <= 100 else "error",
            "value": m1,
        }
        sensors["moisture_z2"] = {
            "status": "ok" if m2 is not None and 0 <= m2 <= 100 else "error",
            "value": m2,
        }
        sensors["moisture_z3"] = {
            "status": "ok" if m3 is not None and 0 <= m3 <= 100 else "error",
            "value": m3,
        }

    # Mark inactive zones where value is 0 (no sensor connected)
    for z in ["moisture_z2", "moisture_z3"]:
        if sensors[z].get("value") == 0 and sensors[z]["status"] == "ok":
            sensors[z]["status"] = "inactive"
            sensors[z]["note"] = "no sensor connected"

    # ── Services ──
    services = {
        "api": {"running": _service_running("edge-ai-api.service")},
        "logger": {"running": _service_running("edge-ai-logger.service")},
        "dashboard": {"running": _service_running("edge-ai-dashboard.service")},
    }

    # ── Database ──
    from config import settings
    db_path = settings.database_url.replace("sqlite:///", "")
    db_size = _db_file_size(db_path)
    count_row = db.execute("SELECT COUNT(*) AS cnt FROM sensor_logs").fetchone()

    # ── Heartbeat ──
    six_min_ago = now - timedelta(minutes=6)
    hb = db.execute(
        "SELECT timestamp FROM system_log "
        "WHERE event = 'logger_heartbeat' AND timestamp >= ? "
        "ORDER BY timestamp DESC LIMIT 1",
        (six_min_ago.strftime("%Y-%m-%d %H:%M:%S"),),
    ).fetchone()

    # ── Overall status ──
    all_ok = (
        arduino_port is not None
        and last_age is not None and last_age < 300
        and sensors["temperature"]["status"] == "ok"
        and services["api"]["running"]
    )
    arduino_ok = arduino_port is not None and last_age is not None and last_age < 300

    overall = "healthy" if all_ok else "degraded" if arduino_ok else "offline"

    return {
        "status": overall,
        "arduino": {
            "detected": arduino_port is not None,
            "port": arduino_port,
            "connected": arduino_port is not None and last_age is not None,
            "last_reading_seconds_ago": last_age,
        },
        "sensors": sensors,
        "services": services,
        "database": {
            "size_mb": db_size,
            "total_readings": count_row["cnt"] if count_row else 0,
            "last_write_seconds_ago": last_age,
        },
        "heartbeat": {
            "logger_active": hb is not None,
            "last_heartbeat": hb["timestamp"] if hb else None,
        },
    }
