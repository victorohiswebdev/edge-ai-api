#!/usr/bin/env python3
"""
generate_data.py — Physics-based soil moisture data generator

Simulates realistic soil moisture dynamics for training the Random Forest
predictive irrigation model. Uses exponential decay modulated by
temperature and humidity (via Vapour Pressure Deficit).

Zones have different watering regimes:
  Zone 1 (Control):  fixed threshold at 40%
  Zone 2 (Stress):   deliberately under-watered (threshold at 20%)
  Zone 3 (AI):       variable threshold (simulates model-based decisions)

Usage:
    python3 models/rf/generate_data.py --samples 10000 --interval 5 --output data.csv
"""

import argparse
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# ─── Soil Physics Constants ───────────────────────────────────────

# Typical saturation/residual for clay-loam soil
M_SATURATED = 85    # moisture % right after watering
M_RESIDUAL = 3      # minimum moisture (bone dry)

# Base drying rate per hour (adjusted by VPD)
BASE_K = 0.12       # per hour

# Sensor noise (std dev)
NOISE_STD = 2.0     # percentage points

# Watering thresholds per zone
ZONE_THRESHOLDS = {
    1: 40,    # Control — standard
    2: 18,    # Stress — deliberately under-watered
    3: 35,    # AI-managed — moderate
}

# Watering amounts per zone (how much moisture jumps after irrigation)
ZONE_WATER_AMOUNT = {
    1: np.random.uniform,   # (45, 65)
    2: np.random.uniform,   # (35, 50) — less water for stress zone
    3: np.random.uniform,   # (40, 60)
}

# Target drift for stress zone (Z2 stays drier)
ZONE_DRY_BIAS = {1: 0, 2: -12, 3: 0}


# ─── Helpers ──────────────────────────────────────────────────────

def diurnal_temp(hour):
    """Generate realistic temperature for a given hour of day.

    Peak at 14:00 (~33°C), trough at 04:00 (~24°C).
    """
    base = 28.5
    amplitude = 5.5
    temp = base + amplitude * np.sin((hour - 8) * np.pi / 12)
    return temp


def diurnal_humidity(hour, temp):
    """Humidity inversely correlates with temperature.

    When temp peaks, humidity troughs (and vice versa).
    """
    base = 65
    amplitude = 15
    hum = base - amplitude * np.sin((hour - 8) * np.pi / 12)
    # Clamp to realistic range
    return np.clip(hum, 30, 95)


def calc_vpd(temp, humidity):
    """Calculate Vapour Pressure Deficit in kPa.

    VPD drives evaporation: higher VPD = faster drying.
    """
    es = 0.61078 * np.exp((17.27 * temp) / (temp + 237.3))
    vpd = (1 - humidity / 100) * es
    return max(vpd, 0.05)  # floor at 0.05


def drying_rate(temp, humidity):
    """Calculate the effective drying rate constant k.

    Higher temperature + lower humidity = faster drying.
    Models this via VPD modulation.
    """
    vpd = calc_vpd(temp, humidity)
    # Base rate scaled by VPD (typical VPD range: 0.2 - 3.0 kPa)
    k = BASE_K * (0.5 + 0.5 * vpd / 1.5)
    return min(k, 0.35)  # cap to prevent unrealistically fast drying


def moisture_decay(m_current, k, dt_hours):
    """Apply exponential decay formula for one time step.

    M(t+dt) = M_residual + (M_current - M_residual) * e^(-k * dt)
    """
    return M_RESIDUAL + (m_current - M_RESIDUAL) * np.exp(-k * dt_hours)


# ─── Main Generator ───────────────────────────────────────────────

def generate_series(
    n_samples=10000,
    interval_minutes=5,
    seed=42,
):
    """Generate a synthetic soil moisture time series.

    Parameters
    ----------
    n_samples : int
        Number of time steps to generate
    interval_minutes : int
        Time between samples in minutes
    seed : int
        Random seed for reproducibility

    Returns
    -------
    pd.DataFrame with columns:
        timestamp, temp, humidity, vpd, zone,
        moisture, days_since_watered, watering_event
    """
    rng = np.random.default_rng(seed)
    dt_hours = interval_minutes / 60.0

    rows = []
    # Start at a realistic hour (noon)
    start_hour = 12.0

    # Track state per zone
    moisture = {z: float(rng.integers(20, 50)) for z in (1, 2, 3)}
    days_since_watered = {z: float(rng.integers(1, 5)) for z in (1, 2, 3)}

    for step in range(n_samples):
        # Current time
        current_hour = (start_hour + step * dt_hours) % 24
        day = step * dt_hours / 24.0

        # Environmental conditions (same for all zones at this time)
        temp = diurnal_temp(current_hour)
        hum = diurnal_humidity(current_hour, temp)

        for zone in (1, 2, 3):
            m = moisture[zone]
            dsw = days_since_watered[zone]
            threshold = ZONE_THRESHOLDS[zone]
            dry_bias = ZONE_DRY_BIAS[zone]

            # Check if this zone should be watered (threshold-based)
            watering = False
            amount = 0
            if zone in (1, 2):
                # Fixed threshold irrigation
                if m < threshold + dry_bias:
                    watering = True
                    amount = rng.uniform(45, 65) if zone == 1 else rng.uniform(35, 50)
            else:
                # Zone 3: variable threshold with some randomness
                # (simulates what the AI model would decide)
                effective_threshold = threshold + rng.normal(0, 5)
                if m < effective_threshold:
                    watering = True
                    amount = rng.uniform(40, 60)

            if watering:
                m = min(m + amount, 100)
                dsw = 0.0
            else:
                # Apply exponential decay
                k = drying_rate(temp, hum)
                m = moisture_decay(m, k, dt_hours)
                dsw += dt_hours / 24.0

            # Add small daily thermal cycling effect
            thermal_cycle = 1.5 * np.sin((current_hour - 14) * np.pi / 12)
            m += thermal_cycle * 0.02  # very subtle effect

            # Add zone-specific dry bias (stress zone stays drier)
            m += dry_bias * 0.01

            # Add sensor noise
            noisy_m = m + rng.normal(0, NOISE_STD)
            noisy_m = np.clip(noisy_m, 0, 100)

            moisture[zone] = m
            days_since_watered[zone] = dsw

            rows.append({
                "step": step,
                "timestamp": (datetime(2026, 1, 1) + timedelta(hours=step * dt_hours)).isoformat(),
                "hour": round(current_hour, 2),
                "day": round(day, 4),
                "temp_c": round(temp, 2),
                "humidity_pct": round(hum, 2),
                "vpd_kpa": round(calc_vpd(temp, hum), 3),
                "zone": zone,
                "moisture_pct": round(noisy_m, 1),
                "days_since_watered": round(dsw, 4),
                "watering_event": 1 if watering else 0,
            })

    return pd.DataFrame(rows)


# ─── Feature Engineering ──────────────────────────────────────────

def add_lagged_features(df, zone=1, lag_steps=(1, 2, 3), target_steps=3):
    """Add lagged moisture features and target column for a single zone.

    Parameters
    ----------
    df : pd.DataFrame
        Full dataset (all zones)
    zone : int
        Which zone to extract features for
    lag_steps : tuple
        Number of time steps back for lagged features
    target_steps : int
        How far ahead to predict

    Returns
    -------
    pd.DataFrame with lag features and target, rows with NaN removed
    """
    zdf = df[df["zone"] == zone].copy()
    zdf = zdf.sort_values("step")

    # Lagged moisture features
    for lag in lag_steps:
        zdf[f"moisture_t_{lag}"] = zdf["moisture_pct"].shift(lag)

    # Target: moisture N steps ahead
    zdf["target"] = zdf["moisture_pct"].shift(-target_steps)

    # Drop rows with NaN (from shifting)
    zdf = zdf.dropna().reset_index(drop=True)

    return zdf


def build_feature_matrix(df):
    """Convert zone-specific DataFrame to feature matrix + target.

    Features used for training:
      - moisture_t_1, moisture_t_2, moisture_t_3 (lagged moisture)
      - temp_c, humidity_pct, vpd_kpa (environmental)
      - hour (time-of-day encoding)
      - days_since_watered (drying duration)
      - zone (one-hot encoded)

    Returns
    -------
    X : pd.DataFrame, y : pd.Series
    """
    features = [
        "moisture_t_1", "moisture_t_2", "moisture_t_3",
        "temp_c", "humidity_pct", "vpd_kpa",
        "hour", "days_since_watered", "zone",
    ]
    X = df[features].copy()
    y = df["target"]

    return X, y


# ─── CLI ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate synthetic soil moisture data")
    parser.add_argument("--samples", type=int, default=15000,
                        help="Number of time steps per zone (default: 15000)")
    parser.add_argument("--interval", type=int, default=5,
                        help="Time step in minutes (default: 5)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed")
    parser.add_argument("--output", default="training_data.csv",
                        help="Output CSV path")
    args = parser.parse_args()

    print(f"🌱 Generating {args.samples} samples × 3 zones at {args.interval}min intervals...")
    df = generate_series(
        n_samples=args.samples,
        interval_minutes=args.interval,
        seed=args.seed,
    )
    print(f"   → {len(df)} rows generated")
    print(f"   → Zone range: {df['moisture_pct'].min():.0f}% – {df['moisture_pct'].max():.0f}%")
    print(f"   → Temp range: {df['temp_c'].min():.1f}°C – {df['temp_c'].max():.1f}°C")

    # Save raw data
    df.to_csv(args.output, index=False)
    print(f"   ✅ Saved to {args.output}")

    # Build feature matrix for training
    print("\n🔧 Building feature matrix with lag features...")
    all_X = []
    all_y = []

    for zone in (1, 2, 3):
        zdf = add_lagged_features(df, zone=zone, lag_steps=(1, 2, 3), target_steps=3)
        X_zone, y_zone = build_feature_matrix(zdf)
        all_X.append(X_zone)
        all_y.append(y_zone)
        print(f"   Zone {zone}: {len(X_zone)} training samples")

    X = pd.concat(all_X, ignore_index=True)
    y = pd.concat(all_y, ignore_index=True)

    # Save feature matrix
    feature_path = args.output.replace(".csv", "_features.csv")
    X["target"] = y
    X.to_csv(feature_path, index=False)
    print(f"   ✅ Feature matrix saved to {feature_path}")
    print(f"\n📊 Feature matrix shape: {X.shape}")
    print(f"   Features: {list(X.columns)}")
    print(f"   Target: moisture prediction ({len(y)} samples)")


if __name__ == "__main__":
    main()
