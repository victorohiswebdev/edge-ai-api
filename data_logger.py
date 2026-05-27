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
SERIAL_TIMEOUT = 2          # seconds before giving up on serial read
LOG_INTERVAL = 300           # seconds between database writes (5 min default)
DEFAULT_PORT = None          # None = auto-detect


# ─── Database Setup ───────────────────────────────────────────────────────────

def setup_database(db_path):
    """Create the sensor_logs table if it doesn't exist.
    
    The table is structured for time-series analysis — timestamps are
    auto-generated, and the columns map 1:1 to the Arduino JSON keys.
    This makes it trivial to export for Random Forest training later.
    
    Temperature and humidity are nullable — they'll be NULL when the
    BME280 sensor is disconnected.
    """
    conn = sqlite3.connect(db_path)
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


# ─── Main Loop ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="FYP Sensor Data Logger")
    parser.add_argument("--port", help=f"Serial port (default: auto-detect)")
    parser.add_argument("--interval", type=int, default=LOG_INTERVAL,
                        help=f"Seconds between DB writes (default: {LOG_INTERVAL})")
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
        arduino.flush()
        print(f"✅ Connected to Arduino on {port} @ {SERIAL_BAUD} baud")
    except Exception as e:
        print(f"❌ Failed to open {port}: {e}")
        print("   Check: Is the Arduino plugged in? Do you have permission?")
        print("   Fix:   sudo usermod -a -G dialout $USER  (then log out/in)")
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
            if arduino.in_waiting > 0:
                # Read one line from Arduino
                raw_line = arduino.readline()
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
                
                # Batch-write to database every LOG_INTERVAL seconds
                if time.time() - last_log_time >= log_interval:
                    cursor.execute("""
                        INSERT INTO sensor_logs 
                        (moisture_zone_1, moisture_zone_2, moisture_zone_3,
                         temperature_c, humidity_perc)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        data.get("moisture_zone_1"),
                        data.get("moisture_zone_2"),
                        data.get("moisture_zone_3"),
                        data.get("temperature_c"),   # None → SQL NULL
                        data.get("humidity_perc"),   # None → SQL NULL
                    ))
                    conn.commit()
                    log_count += 1
                    last_log_time = time.time()
                    
                    sensor_status = "BME: OK" if temp is not None else "BME: absent"
                    print(f"  💾 Wrote to database (log #{log_count}, {sensor_status})")
            
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
