# Edge AI Farm API

FastAPI backend for the **Integrated Edge AI Smart Farming Dashboard** — serves live and historical sensor data from the Arduino + Raspberry Pi pipeline to the Next.js frontend. Includes AI-powered predictive irrigation (Random Forest) and plant health classification (CNN via TensorFlow Lite).

## Quick Start

```bash
# 1. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# On Pi (arm64) — use tflite-runtime instead of full TensorFlow
pip install tflite-runtime

# 3. Copy environment config
cp .env.example .env

# 4. Train/generate ML models (one-time, on any machine)
python models/rf/train.py        # Random Forest — ~30s, outputs models/rf/rf_model_pi.pkl
python models/cnn/train.py       # CNN MobileNetV2 — ~3hrs CPU, outputs models/cnn/models/*.tflite

# 5. Run the server
uvicorn main:app --reload
```

Then visit:
- **API docs (Swagger):** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health check:** [http://localhost:8000/](http://localhost:8000/)

## API Endpoints

### Sensor Data
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/sensors/latest` | Most recent sensor reading |
| `GET` | `/api/v1/sensors/live` | Real-time reading (updated every 2s) |
| `GET` | `/api/v1/sensors/history` | Historical readings (`?hours=24&limit=200`) |
| `GET` | `/api/v1/sensors/summary` | Averaged stats (`?hours=24`) |
| `POST` | `/api/v1/sensors/ingest` | Push a new sensor reading |

### System & Health
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/system/status` | Pipeline health — logger heartbeat, DB status |
| `GET` | `/api/v1/system/health` | Full hardware/software health check |

### Pump Control
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/pumps/command` | Enqueue a pump ON/OFF command |
| `GET` | `/api/v1/pumps/status` | Current pump state |
| `POST` | `/api/v1/pumps/emergency-stop` | Emergency all-off (bypasses queue) |

### Camera
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/camera/snapshot` | Capture a photo with Pi Camera V2 (returns JPEG) |
| `GET` | `/api/v1/camera/captures` | List recent captures (`?limit=10`) |
| `GET` | `/api/v1/camera/captures/{filename}` | Serve a specific captured image |

### AI — Predictive Irrigation (Random Forest)
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/irrigation/predict` | Run RF inference on current sensor data for all 3 zones |
| `GET` | `/api/v1/irrigation/status` | RF model metadata + readiness |
| `GET` | `/api/v1/irrigation/integrated-decision` | RF + CNN combined decision with override logic |

### AI — Plant Health (CNN / TFLite)
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/plant-health/latest` | Most recent classification result |
| `GET` | `/api/v1/plant-health/classify` | Classify a specific capture by filename (`?capture=`) |
| `GET` | `/api/v1/plant-health/history` | Classification history (`?limit=10`) |
| `GET` | `/api/v1/plant-health/status` | TFLite model metadata + readiness |

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
├── main.py              # App entry point + CORS + all routers
├── config.py            # Settings (DB path, CORS origins, etc.)
├── database.py          # SQLite connection + all 7 tables
├── schemas.py           # Pydantic request/response models (14 schemas)
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variable template
├── data_logger.py       # Arduino serial → SQLite writer (run on Pi)
├── camera_worker.py     # Pi Camera capture worker (subprocess for picamera2)
├── seed_data.py         # Generate 48h of sample data for development
├── RUNBOOK.md           # Boot sequence, service management, troubleshooting
├── routes/
│   ├── __init__.py
│   ├── sensors.py       # GET /live, /latest, /history, /summary
│   ├── ingest.py        # POST /ingest
│   ├── system.py        # GET /status, /health
│   ├── pumps.py         # POST /command, GET /status, POST /emergency-stop
│   ├── camera.py        # GET /snapshot, /captures — auto-classifies on capture
│   ├── irrigation.py    # GET /predict, /status, /integrated-decision
│   └── plant_health.py  # GET /latest, /classify, /history, /status
├── models/
│   ├── rf/
│   │   ├── model.py     # IrrigationModel — inference wrapper (joblib)
│   │   ├── train.py     # Train + validate + export RF
│   │   ├── test.py      # Standalone CLI inference for testing
│   │   └── generate_data.py  # Physics-based synthetic training data
│   └── cnn/
│       ├── model.py     # TFLiteClassifier — inference wrapper
│       ├── train.py     # MobileNetV2 transfer learning (3-class)
│       ├── test.py      # Standalone CLI inference for testing
│       └── models/      # Trained .tflite + .keras artifacts (gitignored)
├── deploy/
│   ├── edge-ai-api.service      # Systemd service file (API)
│   └── edge-ai-logger.service   # Systemd service file (data logger)
└── docs/
    └── FastAPI-Scaffold-Guide.md  # Full architecture guide
```

## Architecture

```
Arduino → Serial → Pi (data_logger.py) → SQLite → FastAPI → Next.js Dashboard
                       ↓                                   ↓
                  Serial commands                  pump_commands table
                       ↓                                   ↓
                    Arduino ← relays ← pumps       PumpControl (UI)

                   ┌──────────────┐
                   │ Pi Camera V2 │ → camera_worker.py → captures/
                   └──────┬───────┘         ↓
                          │          TFLite CNN classifier
                          │         (auto-classify on capture)
                          └────→ /irrigation/integrated-decision
                                  (RF + CNN override matrix)
```

The Pi's `data_logger.py` writes sensor readings to `farm_data.db` (every ~2s) and logs a heartbeat to `system_log`. FastAPI reads from the same database and serves it to the Next.js frontend via REST.

**AI models are loaded lazily** on first request — the API starts instantly even without models present. The `/irrigation/*` endpoints return 503 with a clear message if no model file is found. The `/plant-health/*` endpoints return 503 if TFLite runtime isn't installed.

**Pump control** uses a queue pattern to avoid serial contention: the dashboard posts commands to `pump_commands` table via the API, and `data_logger.py` polls this table after each sensor read cycle. Commands are sent to the Arduino as JSON over serial, and the Arduino's acknowledgment updates the `pump_status` table for dashboard polling.

**CNN override logic** (`/irrigation/integrated-decision`): When the plant health classifier detects Stress or Wilted, the final irrigation action is overridden — stressed plants can't uptake water effectively, so irrigation is suppressed. The decision matrix:
- RF: Irrigate + CNN: Healthy → Irrigate
- RF: Irrigate + CNN: Stressed/Wilted → **Don't irrigate** (overridden)
- RF: Don't Irrigate + CNN: Stressed/Wilted → Manual check alert

**Systemd services** (auto-start on boot):
- `edge-ai-api.service` — uvicorn on port 8000
- `edge-ai-dashboard.service` — Next.js on port 3000 (separate repo)
- `edge-ai-logger.service` — data_logger.py with Arduino on USB

See [`RUNBOOK.md`](RUNBOOK.md) for full setup, monitoring, and recovery procedures.

## RF Model — Predictive Irrigation

The Random Forest model predicts soil moisture N time-steps ahead using 9 features:
- 3 lagged moisture readings per zone (t-1, t-2, t-3)
- Temperature, humidity, VPD, hour, days since watered, zone ID

**Training:** `python models/rf/train.py` — generates synthetic training data using physics-based exponential decay (no irrigation events in target). This produces a "what happens if I do nothing?" prediction.

**Pi-optimised model:** 50 trees, max depth 10 — **R² 0.9186**, MAE 1.60%, **3.28 MB**.

**Test:** `python models/rf/test.py 34.2 35.1 36.0 30.2 66.5 14 2.3 1`

## CNN Model — Plant Health

MobileNetV2 transfer learning for 3-class plant health classification:
- **Classes:** Healthy, Stressed, Wilted
- **Test accuracy:** 93.25% (PlantVillage tomato subset)
- **TFLite size:** 4.61 MB (float16 quantised)
- **Pi inference time:** ~200ms on RPi 4

**⚠️ Do NOT double-normalise:** The TFLite model has Rescaling baked into the graph. Feed raw [0, 255] float32 pixel values.

**Training:** `python models/cnn/train.py` — requires TensorFlow, ~3 hours on CPU.

**Test:** `python models/cnn/test.py captures/snapshot.jpg`

## Database Tables

| Table | Purpose |
|-------|---------|
| `sensor_logs` | Historical readings (auto-pruned to 50k rows) |
| `latest_reading` | Current snapshot (updated every ~2s) |
| `system_log` | Logger heartbeats + events |
| `pump_commands` | Command queue (pending → sent → acknowledged/failed) |
| `pump_status` | Current ON/OFF state of each pump |
| `irrigation_decisions` | AI irrigation prediction history |
| `plant_health_log` | CNN classification results |

## BME280 Status

The BME280 environmental sensor is **optional**. When absent, `temperature_c` and `humidity_perc` return as `null` in the API responses. The dashboard handles this gracefully. However, the AI irrigation endpoints require temperature + humidity for VPD calculation and will return 400 if unavailable.

## Pi Camera Module

The system supports a **Pi Camera Module V2** (OV5647 sensor) connected via CSI interface. Camera capture runs on Debian 12 Bookworm using the **libcamera + picamera2** stack.

**Capture flow:** Dashboard → `GET /api/v1/camera/snapshot` → `camera_worker.py` (subprocess) → picamera2 → JPEG saved to `captures/` → returned as image response. After capture, the image is automatically classified by the CNN model and the result is stored in `plant_health_log`.

The camera worker runs as a separate subprocess to avoid picamera2 library conflicts with uvicorn's async event loop.

**Required packages (Pi only):**
```bash
sudo apt install -y libcamera-tools python3-picamera2
```
