# FastAPI Backend — Edge AI Dashboard Scaffolding

> **Repo:** `edge-ai-api`
> **Purpose:** REST API that bridges the Arduino sensor data (via the Pi) to the Next.js dashboard. Runs locally on the Raspberry Pi alongside the data logger.

---

## Project Structure

```
edge-ai-api/
├── main.py                    # FastAPI app entry point + CORS setup
├── database.py                # SQLite connection, table schemas
├── schemas.py                 # Pydantic models (request/response validation)
├── dependencies.py            # Reusable deps (DB session, config)
├── config.py                  # Settings (DB path, CORS origins, etc.)
├── routes/
│   ├── __init__.py            # Empty — makes routes a package
│   ├── sensors.py             # GET endpoints for sensor data
│   └── ingest.py              # POST endpoint for data ingestion
├── requirements.txt           # Python dependencies
└── .env                       # Local config (DB_PATH, PORT, etc.)
```

---

## File-by-File Breakdown

### `config.py` — Settings

Purpose: Single source of truth for all configuration. Uses Pydantic `BaseSettings` so values can come from `.env` or environment variables, with sensible defaults.

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str = "farm_data.db"        # Path to SQLite DB
    cors_origins: list[str] = ["http://localhost:3000"]  # Next.js dev server
    api_host: str = "0.0.0.0"                 # Listen on all interfaces
    api_port: int = 8000                      # FastAPI port

    class Config:
        env_file = ".env"

settings = Settings()  # Singleton
```

**Why:** Your Pi's IP changes when the phone hotspot changes. Hardcoding URLs is a trap. Keeping config in one place means you only change it once.

---

### `database.py` — Database Setup

Purpose: Creates/manages the SQLite connection. Uses the **same schema** as the existing `sensor_logs` table from `data_logger.py`.

```python
import sqlite3
from config import settings

def get_db():
    """Yield a database connection — FastAPI dependency injection style."""
    conn = sqlite3.connect(settings.database_url)
    conn.row_factory = sqlite3.Row           # Returns dict-like rows
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    """Create tables if they don't exist. Call once on startup."""
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
```

**Key design choices:**
- `row_factory = sqlite3.Row` — turns raw tuples into dict-like objects so JSON serialization works automatically
- `init_db()` runs at startup — if the DB doesn't exist, it's created
- Connection lifecycle is managed by FastAPI's dependency injection — one connection per request, closed automatically

**⚠️ One DB, two writers:** Your `data_logger.py` also writes to `farm_data.db`. SQLite handles concurrent reads fine, but concurrent writes can cause `database is locked` errors. Two solutions:
1. **Simpler:** Let `data_logger.py` own the DB writes. The FastAPI only reads from the DB (dashboard queries). No write conflicts.
2. **More flexible:** FastAPI writes too, using WAL mode (`PRAGMA journal_mode=WAL;`) which supports concurrent reads + writes.

**Recommendation:** Start with option 1 (read-only API). Add writes to the API later if needed.

---

### `schemas.py` — Pydantic Models

Purpose: Define the shape of data coming in and going out. Pydantic automatically validates types, generates OpenAPI docs, and converts to/from JSON.

```python
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

# ─── Response Models (what the API returns) ───

class SensorReading(BaseModel):
    """A single row from sensor_logs, as returned to the dashboard."""
    id: int
    timestamp: datetime
    moisture_zone_1: Optional[int] = None
    moisture_zone_2: Optional[int] = None
    moisture_zone_3: Optional[int] = None
    temperature_c: Optional[float] = None       # None when BME280 absent
    humidity_perc: Optional[float] = None       # None when BME280 absent

    class Config:
        from_attributes = True  # Enables ORM-like mode for sqlite3.Row

class LatestReading(BaseModel):
    """Most recent sensor snapshot — used for the live dashboard view."""
    moisture_zone_1: Optional[int]
    moisture_zone_2: Optional[int]
    moisture_zone_3: Optional[int]
    temperature_c: Optional[float]
    humidity_perc: Optional[float]
    timestamp: datetime

class SensorSummary(BaseModel):
    """Aggregated stats for charts — averages over a time window."""
    avg_moisture_1: Optional[float]
    avg_moisture_2: Optional[float]
    avg_moisture_3: Optional[float]
    avg_temperature: Optional[float]
    avg_humidity: Optional[float]
    reading_count: int
    from_timestamp: datetime
    to_timestamp: datetime

# ─── Request Models (what the API accepts) ───

class SensorDataIngest(BaseModel):
    """Payload for pushing data into the API (option 2 approach)."""
    moisture_zone_1: int
    moisture_zone_2: int
    moisture_zone_3: int
    temperature_c: Optional[float] = None
    humidity_perc: Optional[float] = None
    timestamp: Optional[datetime] = None  # If omitted, server uses NOW
```

**Why `Optional` for temperature/humidity?** Because the BME280 is optional. When absent, those fields are `null` / `None`. Pydantic handles this gracefully — the dashboard just shows "N/A".

---

### `dependencies.py` — Reusable Dependencies

Purpose: FastAPI dependency injection hooks. Keeps route handlers clean.

```python
from database import get_db

# get_db is already a dependency generator — we can just re-export
# or add more deps here later (auth, rate limiting, etc.)
```

For now, this file is minimal. It's a placeholder for future needs like:
- API key validation (so random devices can't push data)
- Rate limiting
- Query parameter helpers (date range parsing)

---

### `main.py` — App Entry Point

Purpose: Wires everything together.

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import init_db
from config import settings
from routes import sensors, ingest

app = FastAPI(
    title="Edge AI Farm API",
    description="Backend for the Integrated Edge AI Smart Farming Dashboard",
    version="1.0.0"
)

# ── CORS — Allows your Next.js dashboard to call the API ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Startup — Ensure DB exists ──
@app.on_event("startup")
def on_startup():
    init_db()

# ── Mount routes ──
app.include_router(sensors.router, prefix="/api/v1", tags=["Sensors"])
app.include_router(ingest.router, prefix="/api/v1", tags=["Ingest"])

# ── Root health check ──
@app.get("/")
def root():
    return {"status": "ok", "service": "Edge AI Farm API"}
```

**What each piece does:**
- **CORS middleware** — Your Next.js dashboard runs on `localhost:3000` and the API runs on `localhost:8000`. Browsers block cross-origin requests without CORS headers. This allows it.
- **`on_startup`** — Creates the `sensor_logs` table on first run so you never get a "table not found" error.
- **`include_router`** — Keeps routes organized in separate files. All sensor endpoints live under `/api/v1/`.

---

### `routes/sensors.py` — GET Endpoints (Dashboard Queries)

Purpose: Answer questions the dashboard UI asks.

```python
from fastapi import APIRouter, Depends, Query
from sqlite3 import Connection
from typing import Optional
from datetime import datetime, timedelta
from database import get_db
from schemas import SensorReading, LatestReading, SensorSummary

router = APIRouter()

@router.get("/sensors/latest", response_model=LatestReading)
def get_latest(db: Connection = Depends(get_db)):
    """Return the most recent sensor reading."""
    row = db.execute(
        "SELECT * FROM sensor_logs ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {"error": "No data yet"}  # Handle empty DB gracefully
    return {
        "moisture_zone_1": row["moisture_zone_1"],
        "moisture_zone_2": row["moisture_zone_2"],
        "moisture_zone_3": row["moisture_zone_3"],
        "temperature_c": row["temperature_c"],
        "humidity_perc": row["humidity_perc"],
        "timestamp": row["timestamp"],
    }

@router.get("/sensors/history", response_model=list[SensorReading])
def get_history(
    hours: int = Query(24, description="How far back to fetch"),
    limit: int = Query(100, description="Max number of readings"),
    db: Connection = Depends(get_db),
):
    """Return historical readings for charting."""
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
    """Return averages over a time window for the summary cards."""
    since = datetime.utcnow() - timedelta(hours=hours)
    row = db.execute(
        """SELECT 
               AVG(moisture_zone_1) as avg_m1,
               AVG(moisture_zone_2) as avg_m2,
               AVG(moisture_zone_3) as avg_m3,
               AVG(temperature_c) as avg_temp,
               AVG(humidity_perc) as avg_hum,
               COUNT(*) as count
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
```

**Three endpoints the dashboard needs:**

| Endpoint | Purpose | Dashboard Component |
|---|---|---|
| `GET /api/v1/sensors/latest` | Current live values | Top cards / live display |
| `GET /api/v1/sensors/history?hours=24&limit=200` | Time-series data | Charts (line graphs) |
| `GET /api/v1/sensors/summary?hours=24` | Averages | Summary statistics |

---

### `routes/ingest.py` — POST Endpoint (Data Input)

Purpose: Receive sensor data. Two approaches:

**Option A (Recommended for now):** Pi's `data_logger.py` writes directly to SQLite. FastAPI only serves reads. No POST endpoint needed — simpler, one writer, no conflicts.

**Option B (Future):** POST endpoint for the Pi to push data to the API instead of writing to DB directly. Useful if you want the API to be the single point of data entry.

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlite3 import Connection
from database import get_db
from schemas import SensorDataIngest
from datetime import datetime

router = APIRouter()

@router.post("/sensors/ingest", status_code=201)
def ingest_data(data: SensorDataIngest, db: Connection = Depends(get_db)):
    """Receive a sensor reading and store it."""
    ts = data.timestamp or datetime.utcnow()
    db.execute(
        """INSERT INTO sensor_logs 
           (moisture_zone_1, moisture_zone_2, moisture_zone_3,
            temperature_c, humidity_perc, timestamp)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (data.moisture_zone_1, data.moisture_zone_2, data.moisture_zone_3,
         data.temperature_c, data.humidity_perc, ts.isoformat()),
    )
    db.commit()
    return {"status": "recorded", "timestamp": ts}
```

---

## Complete Reference: API Endpoints

| Method | Path | Description | Query Params |
|---|---|---|---|
| `GET` | `/` | Health check | — |
| `GET` | `/api/v1/sensors/latest` | Most recent reading | — |
| `GET` | `/api/v1/sensors/history` | Historical readings | `hours` (default 24), `limit` (default 100) |
| `GET` | `/api/v1/sensors/summary` | Aggregated averages | `hours` (default 24) |
| `POST` | `/api/v1/sensors/ingest` | Push new reading (future) | — |

---

## How It Connects to the Existing System

```
┌─────────────┐   Serial (9600)   ┌──────────────┐
│   Arduino   │ ─────────────────→ │  Pi (Raspberry Pi 4)  │
│ sensor_reader│   JSON every 2s   │                       │
│ .ino (v2.0) │                    │  data_logger.py ─→ farm_data.db  │
│             │ ←──────────────── │  (optional: POST to API)         │
│             │   {"pump":"ON"}   │                       │
└─────────────┘                    └──────────┬───────────┘
                                              │
                                    ┌─────────▼───────────┐
                                    │   FastAPI (port 8000) │
                                    │   GET /api/v1/*      │
                                    │   (reads farm_data.db)│
                                    └─────────┬───────────┘
                                              │ HTTP (localhost)
                                    ┌─────────▼───────────┐
                                    │  Next.js Dashboard  │
                                    │  (port 3000)        │
                                    └─────────────────────┘
```

---

## Setup Instructions

```bash
# 1. Create the project directory
mkdir edge-ai-api && cd edge-ai-api

# 2. Initialize Python virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install fastapi uvicorn pydantic-settings

# 4. Freeze requirements
pip freeze > requirements.txt

# 5. Create .env file
echo "DATABASE_URL=farm_data.db" > .env
echo "CORS_ORIGINS=['http://localhost:3000']" >> .env

# 6. Create all files listed above, then run:
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Then visit:
- **API docs (Swagger):** http://localhost:8000/docs
- **Health check:** http://localhost:8000/

---

## Next Steps After Scaffolding

1. **Verify it runs** — `uvicorn main:app --reload`, visit `/docs`
2. **Seed test data** — If no real data yet, run a quick script to insert sample rows into `farm_data.db` (I can provide one)
3. **Wire the Next.js dashboard** — Replace placeholder data in `edge-ai-app` with real `fetch()` calls to these endpoints
4. **Connect Pi data** — Ensure `data_logger.py` and FastAPI point to the **same** `farm_data.db` file on the Pi
5. **Live refresh** — Dashboard polls `/api/v1/sensors/latest` every 2 seconds for real-time display
