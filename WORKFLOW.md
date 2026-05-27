# Edge AI Farm — Start-Up Workflow

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

## Checking System Status

```bash
# All services
sudo systemctl status edge-ai-api edge-ai-dashboard edge-ai-logger

# Is the API responding?
curl -s http://localhost:8000 | python3 -m json.tool

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

## Service Recovery

```bash
# Restart a single service
sudo systemctl restart edge-ai-api

# Restart all services (in order)
sudo systemctl restart edge-ai-api
sleep 3
sudo systemctl restart edge-ai-dashboard

# View crash logs
sudo journalctl -u edge-ai-api -p err --no-pager -n 30
```

## Database Notes

- **Single writer:** `data_logger.py` is the only process that writes to `farm_data.db`
- **FastAPI reads only:** The API opens read-only connections (SQLite handles concurrent reads safely)
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
3. **Check the badge** — "Live Data" (green) confirms the full pipeline is running
4. **Switch contexts** — if hardware isn't connected, the badge shows "Simulated" — explain the fallback as a designed feature

## Related Files

| File | Purpose |
|---|---|
| `~/fyp-project/web/edge-ai-api/data_logger.py` | Arduino serial → SQLite writer |
| `~/fyp-project/web/edge-ai-api/seed_data.py` | Synthetic data generator |
| `~/fyp-project/web/edge-ai-api/deploy/edge-ai-api.service` | Systemd service definition (API) |
| `~/fyp-project/web/edge-ai-app/deploy/edge-ai-dashboard.service` | Systemd service definition (UI) |
| `~/fyp-project/web/edge-ai-api/deploy/edge-ai-logger.service` | Systemd service definition (logger) |
