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
    conn.commit()
    conn.close()
