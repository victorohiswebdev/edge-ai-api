"""SQLite database setup and session dependency.

Uses the same schema as the Pi's data_logger.py so both can share
the same farm_data.db file (FastAPI reads, data_logger writes).
"""

import sqlite3
from config import settings


def get_db():
    """FastAPI dependency — yields a row-factory connection."""
    conn = sqlite3.connect(settings.database_url, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create tables on first run. Called once at app startup."""
    conn = sqlite3.connect(settings.database_url)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sensor_logs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
            moisture_zone_1 INTEGER,
            moisture_zone_2 INTEGER,
            moisture_zone_3 INTEGER,
            temperature_c   REAL,
            humidity_perc   REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event           TEXT NOT NULL,
            value           TEXT,
            timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS latest_reading (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
            moisture_zone_1 INTEGER,
            moisture_zone_2 INTEGER,
            moisture_zone_3 INTEGER,
            temperature_c   REAL,
            humidity_perc   REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pump_commands (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            zone        INTEGER NOT NULL CHECK(zone BETWEEN 1 AND 3),
            command     TEXT NOT NULL CHECK(command IN ('ON', 'OFF')),
            status      TEXT DEFAULT 'pending'
                        CHECK(status IN ('pending','sent','acknowledged','failed')),
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pump_status (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            pump_1      TEXT DEFAULT 'OFF' CHECK(pump_1 IN ('ON','OFF')),
            pump_2      TEXT DEFAULT 'OFF' CHECK(pump_2 IN ('ON','OFF')),
            pump_3      TEXT DEFAULT 'OFF' CHECK(pump_3 IN ('ON','OFF')),
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS irrigation_decisions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp           DATETIME DEFAULT CURRENT_TIMESTAMP,
            zone                INTEGER NOT NULL CHECK(zone BETWEEN 1 AND 3),
            current_moisture    REAL,
            predicted_moisture  REAL,
            threshold           REAL DEFAULT 35.0,
            should_irrigate     INTEGER,
            reason              TEXT,
            days_since_watered  REAL,
            temperature_c       REAL,
            humidity_pct        REAL,
            vpd_kpa             REAL,
            hour                INTEGER
        )
    """)
    conn.commit()
    conn.close()
