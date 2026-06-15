#!/usr/bin/env python3
"""
data_logger.py — Pi ↔ Arduino Serial → SQLite Logger

Reads JSON sensor data from Arduino over USB Serial, logs to SQLite3
using a batch-write strategy (write every N reads) to extend SD card life.

v2.0 — Handles missing BME280 gracefully. Temperature/humidity fields
        can be null without crashing.

Usage:
  python3 data_logger.py                    # Auto-detect port
  python3 data_logger.py --port /dev/ttyACM0  # Manual port
  python3 data_logger.py --interval 300      # Log every 300 seconds (5 min)
"""

import serial
import serial.tools.list_ports
import json
import sqlite3
import time
import sys
import argparse
from datetime import datetime


# ─── Config ───────────────────────────────────────────────────────────────────
DB_PATH = "farm_data.db"
SERIAL_BAUD = 9600
SERIAL_TIMEOUT = 5           # seconds before giving up on serial read
LOG_INTERVAL = 300           # seconds between heartbeat log messages
MAX_SENSOR_ROWS = 50000      # cap sensor_logs to prevent unbounded growth
CLEANUP_INTERVAL = 500       # check row count every N reads
DEFAULT_PORT = None          # None = auto-detect


# ─── Database Setup ───────────────────────────────────────────────────────────

def setup_database(db_path):
    """Create the sensor_logs and latest_reading tables if they don't exist.
    
    The main sensor_logs table stores time-series data for historical analysis.
    The latest_reading table holds only the most recent sensor snapshot for
    the live dashboard (updated every read cycle).
    
    Temperature and humidity are nullable — they'll be NULL when the
    BME280 sensor is disconnected.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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
    return conn


# ─── Serial Port Detection ────────────────────────────────────────────────────

def find_arduino():
    """Auto-detect Arduino by scanning USB serial ports.
    
    Looks for common Arduino vendor IDs or port patterns.
    Returns the port name (e.g. '/dev/ttyACM0') or None.
    """
    # Common Arduino USB identifiers
    known_vids = ["2341", "2A03", "1A86", "10C4"]  # Arduino, CH340, CP210x
    
    ports = serial.tools.list_ports.comports()
    for port in ports:
        # Check by VID
        if port.vid and f"{port.vid:04X}" in known_vids:
            print(f"  → Found Arduino on {port.device} ({port.description})")
            return port.device
    
    # Fallback: check common Linux device names
    import glob
    for pattern in ["/dev/ttyACM*", "/dev/ttyUSB*", "/dev/ttyAMA*"]:
        matches = glob.glob(pattern)
        if matches:
            print(f"  → Found device on {matches[0]}")
            return matches[0]
    
    return None


# ─── Format a value for display ───────────────────────────────────────────────

def fmt_val(val, unit="", decimals=1):
    """Format a sensor value for display, handling None/null gracefully."""
    if val is None:
        return "N/A"
    if unit:
        return f"{val:.{decimals}f}{unit}"
    return f"{val:3d}%"


# ─── Pump Command Polling ──────────────────────────────────────────

def _poll_pump_commands(arduino, cursor, conn):
    """Check for pending pump commands in the queue and send them to Arduino.

    Called after every sensor read cycle. Grabs the oldest pending command,
    sends JSON to Arduino over serial, waits for ack, and updates the DB.
    """
    try:
        # Oldest pending command
        cursor.execute(
            "SELECT * FROM pump_commands WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
        )
        cmd = cursor.fetchone()
    except sqlite3.OperationalError:
        return  # Table may not exist yet on first run

    if not cmd:
        return

    zone = cmd["zone"]
    command = cmd["command"]
    cmd_id = cmd["id"]

    # Build the JSON command string
    cmd_key = f"pump_{zone}"
    cmd_str = json.dumps({cmd_key: command})
    print(f"  🔧 Sending pump command: {cmd_str}")

    # Mark as sent
    cursor.execute(
        "UPDATE pump_commands SET status = 'sent' WHERE id = ?",
        (cmd_id,),
    )
    conn.commit()

    try:
        # Send command to Arduino
        arduino.write((cmd_str + "\n").encode())
        time.sleep(0.15)  # Brief pause for Arduino to process before we read

        # Wait for ack — keep reading until we get one, skipping sensor data
        # Sensor JSON won't have pump_N keys; ack JSON will.
        ack_line = None
        for _ in range(8):  # Try up to 8 reads (~4s = 2 Arduino cycles)
            try:
                raw = arduino.readline()
                if raw:
                    line = raw.decode("utf-8").rstrip()
                    if not line:
                        time.sleep(0.5)
                        continue
                    # Check if this is ack (has pump_N field) or sensor data
                    try:
                        parsed = json.loads(line)
                        # If it has pump_1/2/3 keys, it's an ack
                        if any(k in parsed for k in ("pump_1", "pump_2", "pump_3")):
                            ack_line = line
                            break
                        # Otherwise it's sensor data — skip and keep reading
                    except json.JSONDecodeError:
                        pass  # garbled line, skip
                time.sleep(0.5)
            except Exception:
                break

        if ack_line:
            try:
                ack_data = json.loads(ack_line)
                # Validate that the pump state was applied
                if ack_data.get(cmd_key) == command:
                    cursor.execute(
                        "UPDATE pump_commands SET status = 'acknowledged' WHERE id = ?",
                        (cmd_id,),
                    )
                    print(f"  ✅ Pump {zone} {command} acknowledged")

                    # Update pump_status table
                    p1 = ack_data.get("pump_1", "OFF")
                    p2 = ack_data.get("pump_2", "OFF")
                    p3 = ack_data.get("pump_3", "OFF")
                    cursor.execute("DELETE FROM pump_status")
                    cursor.execute(
                        "INSERT INTO pump_status (pump_1, pump_2, pump_3) VALUES (?, ?, ?)",
                        (p1, p2, p3),
                    )
                    conn.commit()
                else:
                    cursor.execute(
                        "UPDATE pump_commands SET status = 'failed' WHERE id = ?",
                        (cmd_id,),
                    )
                    conn.commit()
                    print(f"  ⚠ Pump {zone} {command} ack mismatch: {ack_line[:60]}")
            except json.JSONDecodeError:
                cursor.execute(
                    "UPDATE pump_commands SET status = 'failed' WHERE id = ?",
                    (cmd_id,),
                )
                conn.commit()
                print(f"  ⚠ Pump cmd ack decode failed: {ack_line[:60]}")
        else:
            print(f"  ⚠ Pump {zone} {command} sent but no ack received")
    except serial.SerialException as e:
        cursor.execute(
            "UPDATE pump_commands SET status = 'failed' WHERE id = ?",
            (cmd_id,),
        )
        conn.commit()
        print(f"  ❌ Pump cmd serial error: {e}")


# ─── Main Loop ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="FYP Sensor Data Logger")
    parser.add_argument("--port", help=f"Serial port (default: auto-detect)")
    parser.add_argument("--interval", type=int, default=LOG_INTERVAL,
                        help=f"Seconds between heartbeat log messages (default: {LOG_INTERVAL})")
    parser.add_argument("--db", default=DB_PATH,
                        help=f"SQLite database path (default: {DB_PATH})")
    args = parser.parse_args()
    
    port = args.port or find_arduino()
    if not port:
        print("❌ Could not find Arduino. Specify port with --port")
        print("   Common ports: /dev/ttyACM0, /dev/ttyUSB0")
        sys.exit(1)
    
    log_interval = args.interval
    db_path = args.db
    
    # ── Connect to Arduino ──
    try:
        arduino = serial.Serial(port, SERIAL_BAUD, timeout=SERIAL_TIMEOUT)
        time.sleep(3)                     # Let Arduino boot after DTR reset
        # Drain startup messages (BME280 init, etc.)
        arduino.timeout = 0.5
        while arduino.readline():
            pass
        arduino.timeout = SERIAL_TIMEOUT
        print(f"✅ Connected to Arduino on {port} @ {SERIAL_BAUD} baud", flush=True)
    except Exception as e:
        print(f"❌ Failed to open {port}: {e}", flush=True)
        print("   Check: Is the Arduino plugged in? Do you have permission?", flush=True)
        print("   Fix:   sudo usermod -a -G dialout $USER  (then log out/in)", flush=True)
        sys.exit(1)
    
    # ── Setup database ──
    conn = setup_database(db_path)
    cursor = conn.cursor()
    print(f"✅ Database ready: {db_path}")
    print()
    print("📡 Listening for sensor data... (Ctrl+C to stop)")
    print("-" * 60)
    
    # ── Data logging loop ──
    last_log_time = 0
    read_count = 0
    log_count = 0
    
    try:
        while True:
            try:
                # Read one line from Arduino
                raw_line = arduino.readline()
            except serial.SerialException:
                # Arduino disconnected — try to reconnect
                print("  ⚠ Serial connection lost — reconnecting...")
                try:
                    arduino.close()
                except:
                    pass
                time.sleep(2)
                try:
                    # Re-scan for Arduino — it may have moved ports
                    new_port = find_arduino() or port
                    arduino = serial.Serial(new_port, SERIAL_BAUD, timeout=SERIAL_TIMEOUT)
                    print(f"  → Reconnected on {new_port}")
                    time.sleep(3)
                    arduino.timeout = 0.5
                    while arduino.readline():
                        pass
                    arduino.timeout = SERIAL_TIMEOUT
                    port = new_port
                    print(f"  ✅ Reconnected\n")
                    continue
                except Exception as e:
                    print(f"  ❌ Reconnect failed: {e}")
                    time.sleep(5)
                    continue
            if raw_line:
                try:
                    line = raw_line.decode("utf-8").rstrip()
                except UnicodeDecodeError:
                    continue  # Skip garbled bytes
                
                if not line:
                    continue
                
                # Parse JSON
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    print(f"  ⚠ Skipped malformed: {line[:50]}")
                    continue
                
                # Validate expected keys (moisture sensors are always present)
                if "moisture_zone_1" not in data:
                    print(f"  ⚠ Unexpected format: {line[:80]}")
                    continue
                
                read_count += 1
                
                # Print to console (with null-safe formatting)
                now = datetime.now().strftime("%H:%M:%S")
                m1, m2, m3 = data.get("moisture_zone_1"), data.get("moisture_zone_2"), data.get("moisture_zone_3")
                temp  = data.get("temperature_c")
                humid = data.get("humidity_perc")
                
                temp_str = fmt_val(temp, "°C")
                humid_str = fmt_val(humid, "%")
                
                print(f"  [{now}] Z1:{fmt_val(m1)}  Z2:{fmt_val(m2)}  Z3:{fmt_val(m3)}  "
                      f"T:{temp_str}  "
                      f"H:{humid_str}  "
                      f"(read #{read_count})")
                
                # Update latest_reading table every read (live dashboard)
                cursor.execute("DELETE FROM latest_reading")
                cursor.execute("""
                    INSERT INTO latest_reading
                    (moisture_zone_1, moisture_zone_2, moisture_zone_3,
                     temperature_c, humidity_perc)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    data.get("moisture_zone_1"),
                    data.get("moisture_zone_2"),
                    data.get("moisture_zone_3"),
                    data.get("temperature_c"),
                    data.get("humidity_perc"),
                ))

                # Write to sensor_logs every read (so the chart captures all spikes)
                cursor.execute("""
                    INSERT INTO sensor_logs 
                    (moisture_zone_1, moisture_zone_2, moisture_zone_3,
                     temperature_c, humidity_perc)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    data.get("moisture_zone_1"),
                    data.get("moisture_zone_2"),
                    data.get("moisture_zone_3"),
                    data.get("temperature_c"),
                    data.get("humidity_perc"),
                ))

                # Periodic cleanup — keep DB lean
                if read_count % CLEANUP_INTERVAL == 0:
                    cursor.execute(
                        "DELETE FROM sensor_logs WHERE id NOT IN "
                        "(SELECT id FROM sensor_logs ORDER BY timestamp DESC LIMIT ?)",
                        (MAX_SENSOR_ROWS,),
                    )
                    if cursor.rowcount:
                        print(f"  🧹 Pruned {cursor.rowcount} old rows (cap: {MAX_SENSOR_ROWS})")

                conn.commit()

                # Log heartbeat every LOG_INTERVAL seconds
                if time.time() - last_log_time >= log_interval:
                    log_count += 1
                    last_log_time = time.time()

                    # Record heartbeat in system_log
                    cursor.execute(
                        "INSERT INTO system_log (event, value) VALUES (?, ?)",
                        ("logger_heartbeat", "active"),
                    )
                    conn.commit()

                    sensor_status = "BME: OK" if temp is not None else "BME: absent"
                    print(f"  💾 Heartbeat logged (log #{log_count}, {sensor_status})")

            # ── Pump command polling ──
            _poll_pump_commands(arduino, cursor, conn)

            # Small sleep to prevent busy-waiting on CPU
            time.sleep(0.1)
    
    except KeyboardInterrupt:
        print()
        print("-" * 60)
        print(f"📊 Session summary:")
        print(f"   Total reads:     {read_count}")
        print(f"   DB writes:       {log_count}")
        print(f"   Database file:   {db_path}")
        print("👋 Goodbye!")
    
    finally:
        arduino.close()
        conn.close()


if __name__ == "__main__":
    main()
