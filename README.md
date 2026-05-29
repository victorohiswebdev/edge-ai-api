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
| `GET` | `/api/v1/sensors/live` | Real-time reading (updated every 2s) |
| `GET` | `/api/v1/sensors/history` | Historical readings (`?hours=24&limit=200`) |
| `GET` | `/api/v1/sensors/summary` | Averaged stats (`?hours=24`) |
| `GET` | `/api/v1/system/status` | Pipeline health — logger heartbeat, DB status |
| `GET` | `/api/v1/system/health` | Full hardware/software health check |
| `POST` | `/api/v1/pumps/command` | Enqueue a pump ON/OFF command |
| `GET` | `/api/v1/pumps/status` | Current pump state |
| `POST` | `/api/v1/pumps/emergency-stop` | Emergency all-off (bypasses queue) |
| `POST` | `/api/v1/sensors/ingest` | Push a new sensor reading (optional) |
| `GET` | `/api/v1/camera/snapshot` | Capture a photo with Pi Camera V2 (returns JPEG) |
| `GET` | `/api/v1/camera/captures` | List recent captures (`?limit=10`) |
| `GET` | `/api/v1/camera/captures/{filename}` | Serve a specific captured image |

### System Status Endpoint

The `/api/v1/system/status` endpoint is the source of truth for the dashboard's data source indicator:

```json
{
  "database_connected": true,
  "logger_active": true,
  "last_reading": "2026-05-27T15:10:30",
  "total_readings": 576
}
```

- `logger_active` is `true` when `data_logger.py` has written a heartbeat to the `system_log` table within the last 6 minutes
- The dashboard uses this to show **Simulated** (amber), **Database** (blue), or **Live Data** (green) in the header

## Project Structure

```
edge-ai-api/
├── main.py              # App entry point + CORS
├── config.py            # Settings (DB path, CORS origins, etc.)
├── database.py          # SQLite connection + table init
├── schemas.py           # Pydantic request/response models
├── dependencies.py      # Reusable FastAPI dependencies
├── data_logger.py       # Arduino serial → SQLite writer (run on Pi)
├── camera_worker.py     # Pi Camera capture worker (subprocess for picamera2)
├── seed_data.py         # Generate 48h of sample data for development
├── RUNBOOK.md           # Boot sequence, service management, data source states, troubleshooting
├── routes/
│   ├── __init__.py
│   ├── sensors.py       # GET endpoints for the dashboard
│   ├── ingest.py        # POST endpoint for data ingestion
│   ├── system.py        # GET /system/status + /system/health
│   ├── pumps.py         # POST /pumps/command + GET /pumps/status + POST /pumps/emergency-stop
│   └── camera.py        # GET /camera/snapshot + /camera/captures
├── deploy/
│   ├── edge-ai-api.service      # Systemd service file (API)
│   └── edge-ai-logger.service   # Systemd service file (data logger)
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variable template
└── docs/
    └── FastAPI-Scaffold-Guide.md  # Full architecture guide
```

## Architecture

```
Arduino → Serial → Pi (data_logger.py) → SQLite → FastAPI → Next.js Dashboard
                                           ↓                       ↓
                                    system_log table      PumpControl (UI)
                                    (logger heartbeat)        ↓ POST
                                    Camera (CSI)          pump_commands table
                                        ↓                       ↓ poll
                                    camera_worker.py       data_logger.py
                                        ↓                       ↓ Serial
                                    captures/               Arduino → relays → pumps
```

The Pi's `data_logger.py` writes sensor readings to `farm_data.db` (batch-write every 5 minutes) and logs a heartbeat to `system_log`. FastAPI reads from the same database and serves it to the Next.js frontend via REST.

**Pump control** uses a queue pattern to avoid serial contention: the dashboard posts commands to `pump_commands` table via the API, and `data_logger.py` polls this table after each sensor read cycle. Commands are sent to the Arduino as JSON over serial, and the Arduino's acknowledgment updates the `pump_status` table for dashboard polling.

**Systemd services** (auto-start on boot):
- `edge-ai-api.service` — uvicorn on port 8000
- `edge-ai-dashboard.service` — Next.js on port 3000 (separate repo)
- `edge-ai-logger.service` — data_logger.py with Arduino on USB

See [`RUNBOOK.md`](RUNBOOK.md) for full setup, monitoring, and recovery procedures.

## BME280 Status

The BME280 environmental sensor is **optional**. When absent, `temperature_c` and `humidity_perc` return as `null` in the API responses. The dashboard handles this gracefully.

## Pi Camera Module

The system supports a **Pi Camera Module V2** (OV5647 sensor) connected via CSI interface. Camera capture runs on Debian 12 Bookworm using the **libcamera + picamera2** stack.

**Capture flow:** Dashboard → `GET /api/v1/camera/snapshot` → `camera_worker.py` (subprocess) → picamera2 → JPEG saved to `captures/` → returned as image response.

The camera worker runs as a separate subprocess to avoid picamera2 library conflicts with uvicorn's async event loop. Captured images are stored in the `captures/` directory and can be browsed via `GET /api/v1/camera/captures`.

**Required packages (Pi only):**
```bash
sudo apt install -y libcamera-tools python3-picamera2
```
