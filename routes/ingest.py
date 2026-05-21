"""POST endpoint — optional data ingestion from the Pi."""

from fastapi import APIRouter, Depends
from sqlite3 import Connection
from datetime import datetime
from database import get_db
from schemas import SensorDataIngest

router = APIRouter()


@router.post("/sensors/ingest", status_code=201)
def ingest_data(data: SensorDataIngest, db: Connection = Depends(get_db)):
    """Receive a sensor reading from the Pi and store it in the database.

    This is optional — the Pi's data_logger.py can write directly to the
    same SQLite file. Use this endpoint if you want the API to be the
    single point of data entry.
    """
    ts = data.timestamp or datetime.utcnow()

    db.execute(
        """INSERT INTO sensor_logs
           (moisture_zone_1, moisture_zone_2, moisture_zone_3,
            temperature_c, humidity_perc, timestamp)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            data.moisture_zone_1,
            data.moisture_zone_2,
            data.moisture_zone_3,
            data.temperature_c,
            data.humidity_perc,
            ts.isoformat(),
        ),
    )
    db.commit()

    return {"status": "recorded", "timestamp": ts}
