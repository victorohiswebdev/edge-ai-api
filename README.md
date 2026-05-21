# Edge AI Farm API

FastAPI backend for the **Integrated Edge AI Smart Farming Dashboard** — serves live and historical sensor data from the Arduino + Raspberry Pi pipeline to the Next.js frontend.

## Quick Start

```bash
# 1. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy environment config
cp .env.example .env

# 4. Run the server
uvicorn main:app --reload
```

Then visit:
- **API docs (Swagger):** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health check:** [http://localhost:8000/](http://localhost:8000/)

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Health check |
| `GET` | `/api/v1/sensors/latest` | Most recent sensor reading |
| `GET` | `/api/v1/sensors/history` | Historical readings (`?hours=24&limit=200`) |
| `GET` | `/api/v1/sensors/summary` | Averaged stats (`?hours=24`) |
| `POST` | `/api/v1/sensors/ingest` | Push a new sensor reading (optional) |

## Project Structure

```
edge-ai-api/
├── main.py              # App entry point + CORS
├── config.py            # Settings (DB path, CORS origins, etc.)
├── database.py          # SQLite connection + table init
├── schemas.py           # Pydantic request/response models
├── dependencies.py      # Reusable FastAPI dependencies
├── routes/
│   ├── __init__.py
│   ├── sensors.py       # GET endpoints for the dashboard
│   └── ingest.py        # POST endpoint for data ingestion
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variable template
└── docs/
    └── FastAPI-Scaffold-Guide.md  # Full architecture guide
```

## Architecture

```
Arduino → Serial → Pi (data_logger.py) → SQLite → FastAPI → Next.js Dashboard
```

The Pi's `data_logger.py` writes sensor readings to `farm_data.db`. FastAPI reads from the same database and serves it to the Next.js frontend via REST. The POST endpoint is optional — use it if you want the API to be the single point of data entry instead.

## BME280 Status

The BME280 environmental sensor is **optional**. When absent, `temperature_c` and `humidity_perc` return as `null` in the API responses. The dashboard handles this gracefully.
