"""Seed the database with sample sensor readings for dashboard development.

Run this when there's no real hardware connected to see the API
returning realistic data:

    python seed_data.py

Adjust HOURS to control how far back the data goes.
"""

import sqlite3
import random
import math
from datetime import datetime, timedelta

DB_PATH = "farm_data.db"
HOURS = 48             # Generate 48 hours of history
INTERVAL_MIN = 5       # One reading every 5 minutes
BME280_PRESENT = True  # Set False to test null BME fields

# Realistic base values
BASE_TEMP = 28.0       # °C — Nigerian ambient
BASE_HUMID = 65.0      # %
BASE_MOISTURE = [55, 45, 60]  # Zone baselines (Zone 2 is drier = stress zone)


def seed():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Clear existing data
    cursor.execute("DELETE FROM sensor_logs")
    conn.commit()

    now = datetime.utcnow()
    total_points = (HOURS * 60) // INTERVAL_MIN
    diurnal_cycle = 0.0

    for i in range(total_points):
        # Timestamp going backward from now
        ts = now - timedelta(minutes=i * INTERVAL_MIN)

        # Simulate diurnal temperature cycle (hotter midday, cooler night)
        hour_angle = (ts.hour + ts.minute / 60) * 360 / 24
        diurnal_cycle = math.sin(math.radians(hour_angle - 3)) * 5  # peak ~3pm

        temp = round(BASE_TEMP + diurnal_cycle + random.uniform(-1.5, 1.5), 1)
        humid = round(BASE_HUMID - diurnal_cycle * 1.5 + random.uniform(-3, 3), 1)

        # Moisture drifts slowly + some noise
        m1 = max(0, min(100, round(BASE_MOISTURE[0] + random.uniform(-3, 3))))
        m2 = max(0, min(100, round(BASE_MOISTURE[1] + random.uniform(-2, 4))))
        m3 = max(0, min(100, round(BASE_MOISTURE[2] + random.uniform(-3, 3))))

        if BME280_PRESENT:
            cursor.execute(
                """INSERT INTO sensor_logs
                   (timestamp, moisture_zone_1, moisture_zone_2, moisture_zone_3,
                    temperature_c, humidity_perc)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ts.isoformat(), m1, m2, m3, temp, humid),
            )
        else:
            cursor.execute(
                """INSERT INTO sensor_logs
                   (timestamp, moisture_zone_1, moisture_zone_2, moisture_zone_3)
                   VALUES (?, ?, ?, ?)""",
                (ts.isoformat(), m1, m2, m3),
            )

    conn.commit()
    count = cursor.execute("SELECT COUNT(*) FROM sensor_logs").fetchone()[0]
    conn.close()
    print(f"✅ Seeded {count} readings ({HOURS}h × {INTERVAL_MIN}min intervals)")
    print(f"   BME280 present: {BME280_PRESENT}")
    print(f"   Date range: {now - timedelta(hours=HOURS)} → {now}")


if __name__ == "__main__":
    seed()
