# Edge AI Farm — Operational Runbook

## System Architecture

```
Arduino Uno ──USB──→ Raspberry Pi 4 ──HDMI──→ Monitor (GUI via RealVNC)
                        │
                        ├── systemd: edge-ai-logger.service
                        │   data_logger.py → farm_data.db (writes)
                        │
                        ├── systemd: edge-ai-api.service
                        │   uvicorn → FastAPI → farm_data.db (reads)
                        │   http://localhost:8000
                        │
                        └── systemd: edge-ai-dashboard.service
                            npm start → Next.js (production build)
                            http://localhost:3000
```

## What Happens on Boot

When the Raspberry Pi powers on, three systemd services start automatically:

| Order | Service | What it does | Depends on |
|---|---|---|---|
| 1 | `edge-ai-api` | Starts FastAPI backend on port 8000 | Network up + `farm_data.db` |
| 2 | `edge-ai-dashboard` | Starts Next.js dashboard on port 3000 | Network up |
| 3 | `edge-ai-logger` | Starts Arduino serial reader + DB writer | Network up + Arduino on USB |

**Boot timeline:** All three services start within ~30 seconds of power-on. The dashboard uses a **synthetic data fallback** — if the API isn't ready yet, the dashboard shows simulated data and automatically switches to live once the API responds.

## Service Management

### Status — check if running

```bash
# Single service
sudo systemctl status edge-ai-api

# All three at once
sudo systemctl status edge-ai-api edge-ai-dashboard edge-ai-logger
```

### Stop — graceful shutdown (SIGTERM)

```bash
# One service
sudo systemctl stop edge-ai-logger

# All at once
sudo systemctl stop edge-ai-api edge-ai-dashboard edge-ai-logger
```

### Start — bring back up

```bash
# One service
sudo systemctl start edge-ai-dashboard

# All at once
sudo systemctl start edge-ai-api edge-ai-dashboard edge-ai-logger
```

### Restart — stop then start

```bash
sudo systemctl restart edge-ai-api
```

### Kill — force stop a stuck service (SIGKILL)

Use this when `stop` doesn't work (process ignores SIGTERM):

```bash
sudo systemctl kill -s SIGKILL edge-ai-api

# Shorthand
sudo systemctl kill -s KILL edge-ai-logger
```

After killing, start the service normally:

```bash
sudo systemctl start edge-ai-api
```

### Disable — prevent auto-start on boot

```bash
# Won't start on next reboot (but still running now)
sudo systemctl disable edge-ai-logger

# Stop now AND prevent on boot
sudo systemctl disable --now edge-ai-logger
```

### Enable — restore auto-start on boot

```bash
sudo systemctl enable --now edge-ai-logger
```

## Checking System Status

```bash
# All services
sudo systemctl status edge-ai-api edge-ai-dashboard edge-ai-logger

# Is the API responding?
curl -s http://localhost:8000 | python3 -m json.tool

# Full pipeline status (source of truth for the badge)
curl -s http://localhost:8000/api/v1/system/status | python3 -m json.tool
# Returns: { database_connected, logger_active, last_reading, total_readings }

# Is data flowing?
curl -s http://localhost:8000/api/v1/sensors/latest | python3 -m json.tool

# How many readings in the database?
sqlite3 ~/fyp-project/web/edge-ai-api/farm_data.db "SELECT COUNT(*) FROM sensor_logs;"

# Live log stream (API)
sudo journalctl -u edge-ai-api -f

# Live log stream (data logger)
sudo journalctl -u edge-ai-logger -f
```

## Dashboard Data Source Indicator

The header of the dashboard shows a badge indicating the current data pipeline status:

| Badge | Color | Meaning |
|---|---|---|
| **● Simulated** | Amber | API unreachable — showing client-generated synthetic data |
| **● Database** | Blue | API reachable — displaying data from the database, but no Arduino logger heartbeat detected (seeded data or logger not running) |
| **● Live Data** | Green | Full pipeline active — API reachable + Arduino logger writing to the database in the last 6 minutes |

The dashboard polls the API and system status every 10 seconds. The badge updates automatically when the pipeline state changes.

## Troubleshooting

### View crash logs

```bash
# Last 30 lines of errors
sudo journalctl -u edge-ai-api -p err --no-pager -n 30

# Full log for a specific service
sudo journalctl -u edge-ai-logger --no-pager -n 50

# Live tail (follow new logs)
sudo journalctl -u edge-ai-api -f
```

### Run a service manually (for debugging)

Stop the service, then run it in your terminal to see stdout/stderr directly:

```bash
sudo systemctl stop edge-ai-api

source ~/fyp-project/web/edge-ai-api/venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

When done, Ctrl+C and restart the service:

```bash
sudo systemctl start edge-ai-api
```

### Common failure: SQLite threading

If you see `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread` in the API logs, the database connection is crossing thread boundaries. Fix:

```bash
cd ~/fyp-project/web/edge-ai-api && git pull
sudo systemctl restart edge-ai-api
```

The `database.py` file includes `check_same_thread=False` to resolve this.

### Common failure: logger keeps restarting (Arduino not found)

The logger service will exit with code 1 if no Arduino is detected. This is expected when the hardware isn't connected. Once plugged in, the auto-detect picks it up on the next restart cycle (every 10 seconds).

To verify the port exists:

```bash
ls /dev/ttyA* /dev/ttyU*
```

Expected output when Arduino is connected: `/dev/ttyACM0` or `/dev/ttyUSB0`.

### Common failure: permission denied on serial port

```bash
sudo usermod -a -G dialout $USER
# Log out and back in (or reboot) for this to take effect
```

## Database Notes

Two tables in `farm_data.db`:

| Table | Purpose | Written by |
|---|---|---|
| `sensor_logs` | Sensor readings (moisture, temp, humidity) | `data_logger.py` (every 300s) |
| `system_log` | Logger heartbeat + system events | `data_logger.py` (on each write cycle) |

Rules:
- **Single writer:** `data_logger.py` is the only process that writes to the database
- **FastAPI reads only:** The API opens read-only connections (SQLite handles concurrent reads safely)
- **Logger heartbeat:** The badge shows "Live Data" when a `logger_heartbeat` event exists in `system_log` within the last 6 minutes
- **Re-seeding:** If the database is empty or corrupted, re-seed with sample data:

```bash
cd ~/fyp-project/web/edge-ai-api
source venv/bin/activate
python seed_data.py
```

- **Reset:** Delete the database file to start fresh:

```bash
cd ~/fyp-project/web/edge-ai-api
rm farm_data.db
sudo systemctl restart edge-ai-api  # recreates table on next startup
python seed_data.py                  # optional: seed sample data
```

## Connecting the Arduino

1. Plug Arduino into Pi via USB
2. Check the port:
   ```bash
   ls /dev/ttyA* /dev/ttyU*
   ```
3. Start the data logger:
   ```bash
   cd ~/fyp-project/web/edge-ai-api
   source venv/bin/activate
   python data_logger.py --port /dev/ttyACM0
   ```
4. Or let systemd manage it (if `edge-ai-logger` service is configured):
   ```bash
   sudo systemctl start edge-ai-logger
   ```

> **Permission note:** If you get `Permission denied` on the serial port, add your user to the `dialout` group:
> ```bash
> sudo usermod -a -G dialout $USER
> # Log out and back in (or reboot) for this to take effect
> ```

## Defense Presentation Mode

For the project defense, the workflow is:

1. **Power on the Pi** — services auto-start
2. **Open RealVNC** — see the dashboard on browser at `localhost:3000`
3. **Check the badge** — use it to explain each pipeline state:
   - **Simulated** (amber) → "No hardware connected, dashboard still works — designed for reliability"
   - **Database** (blue) → "Seeded test data or logger momentarily offline — pipeline intact"
   - **Live Data** (green) → "Full physical setup — Arduino streaming, sensor data flowing"
4. **Switch contexts** — demonstrate the fallback works by unplugging and reconnecting the Arduino; watch the badge transition

## Related Files

| File | Purpose |
|---|---|
| `~/fyp-project/web/edge-ai-api/data_logger.py` | Arduino serial → SQLite writer |
| `~/fyp-project/web/edge-ai-api/seed_data.py` | Synthetic data generator |
| `~/fyp-project/web/edge-ai-api/deploy/edge-ai-api.service` | Systemd service definition (API) |
| `~/fyp-project/web/edge-ai-app/deploy/edge-ai-dashboard.service` | Systemd service definition (UI) |
| `~/fyp-project/web/edge-ai-api/deploy/edge-ai-logger.service` | Systemd service definition (logger) |
