#!/usr/bin/env python3
"""
generate_data.py — Physics-based soil moisture data generator (v2)

CAUSALLY-CORRECT: Generates pure exponential-decay moisture data WITHOUT
simulated irrigation events in the training target. The model learns to
predict NATURAL moisture trajectory, enabling proper irrigation decisions
by comparing predicted vs threshold POST-inference.

Uses restart segments across the 0-100% range to maintain data diversity
while keeping every segment as clean exponential decay.

Zones retain different drying biases for diversity:
  Zone 1 (Control):  neutral bias
  Zone 2 (Stress):   negative bias (dries harder)
  Zone 3 (AI):       neutral bias

Usage:
    python3 models/rf/generate_data.py --samples 2000 --segments 5 \
      --interval 5 --output data.csv
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

# Zone-specific dry bias (applied after decay for diversity)
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

    Higher VPD = faster drying.
    """
    vpd = calc_vpd(temp, humidity)
    k = BASE_K * (0.5 + 0.5 * vpd / 1.5)
    return min(k, 0.35)  # cap to prevent unrealistically fast drying


def moisture_decay(m_current, k, dt_hours):
    """Apply exponential decay formula for one time step.

    M(t+dt) = M_residual + (M_current - M_residual) * e^(-k * dt)
    """
    return M_RESIDUAL + (m_current - M_RESIDUAL) * np.exp(-k * dt_hours)


# ─── Decay Segment Generator ──────────────────────────────────────

def generate_decay_segment(
    start_moisture, n_steps, interval_minutes, zone, rng, segment_id, offset=0,
):
    """Generate a single pure exponential decay segment.

    NO irrigation events — moisture follows exponential decay modulated
    by diurnal temperature and humidity cycles.

    Parameters
    ----------
    start_moisture : float
        Initial moisture percentage for this segment
    n_steps : int
        Number of time steps in this segment
    interval_minutes : int
        Time between samples in minutes
    zone : int
        Irrigation zone (1, 2, or 3)
    rng : np.random.Generator
        Random number generator
    segment_id : int
        Unique segment identifier for grouping
    offset : int
        Global step offset for unique step numbering

    Returns
    -------
    pd.DataFrame
    """
    dt_hours = interval_minutes / 60.0
    start_hour = 12.0
    dry_bias = ZONE_DRY_BIAS[zone]

    rows = []
    m = start_moisture
    dsw = 0.0

    for step in range(n_steps):
        current_hour = (start_hour + step * dt_hours) % 24

        # Environmental conditions
        temp = diurnal_temp(current_hour)
        hum = diurnal_humidity(current_hour, temp)

        # Pure exponential decay — NO irrigation intervention
        k = drying_rate(temp, hum)
        m = moisture_decay(m, k, dt_hours)
        dsw += dt_hours / 24.0

        # Apply zone-specific dry bias
        biased_m = m + dry_bias * 0.01

        # Add sensor noise
        noisy_m = biased_m + rng.normal(0, NOISE_STD)
        noisy_m = np.clip(noisy_m, 0, 100)

        rows.append({
            "step": offset + step,
            "hour": round(current_hour, 2),
            "temp_c": round(temp, 2),
            "humidity_pct": round(hum, 2),
            "vpd_kpa": round(calc_vpd(temp, hum), 3),
            "zone": zone,
            "moisture_pct": round(noisy_m, 1),
            "days_since_watered": round(dsw, 4),
            "watering_event": 0,          # always 0 — no irrigation in data
            "segment_id": segment_id,      # for correct lag feature grouping
        })

    return pd.DataFrame(rows)


# ─── Main Generator ───────────────────────────────────────────────

def generate_series(
    n_samples_per_segment=2000,
    n_segments_per_zone=5,
    interval_minutes=5,
    seed=42,
):
    """Generate pure-decay synthetic soil moisture data.

    Uses multiple independent decay segments per zone, each starting
    at a random moisture level (30-90%), to cover the full moisture
    range without artificial irrigation events.

    Parameters
    ----------
    n_samples_per_segment : int
        Steps per decay segment
    n_segments_per_zone : int
        Number of independent decay runs per zone
    interval_minutes : int
        Time between samples in minutes
    seed : int
        Random seed for reproducibility

    Returns
    -------
    pd.DataFrame with columns:
        step, hour, temp_c, humidity_pct, vpd_kpa, zone,
        moisture_pct, days_since_watered, watering_event, segment_id
    """
    rng = np.random.default_rng(seed)
    all_segments = []
    step_offset = 0
    seg_counter = 0

    for zone in (1, 2, 3):
        for _ in range(n_segments_per_zone):
            # Random starting moisture between 30-90%
            start_m = rng.uniform(30, 90)

            seg = generate_decay_segment(
                start_moisture=start_m,
                n_steps=n_samples_per_segment,
                interval_minutes=interval_minutes,
                zone=zone,
                rng=rng,
                segment_id=seg_counter,
                offset=step_offset,
            )
            all_segments.append(seg)
            step_offset += n_samples_per_segment
            seg_counter += 1

    df = pd.concat(all_segments, ignore_index=True)
    return df


# ─── Feature Engineering ──────────────────────────────────────────

def add_lagged_features(df, zone=1, lag_steps=(1, 2, 3), target_steps=3):
    """Add lagged moisture features and target column for a single zone.

    Groups by segment_id to prevent artificial jumps at segment
    boundaries from contaminating lag/target features.

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
    zdf = zdf.sort_values(["segment_id", "step"])

    # Apply shift within each segment group to avoid cross-segment leakage
    for lag in lag_steps:
        zdf[f"moisture_t_{lag}"] = (
            zdf.groupby("segment_id")["moisture_pct"].shift(lag)
        )

    # Target: moisture N steps ahead (within same segment)
    zdf["target"] = (
        zdf.groupby("segment_id")["moisture_pct"].shift(-target_steps)
    )

    # Drop rows with NaN (from shifting at segment boundaries)
    zdf = zdf.dropna().reset_index(drop=True)

    return zdf


def build_feature_matrix(df):
    """Convert zone-specific DataFrame to feature matrix + target.

    Features used for training:
      - moisture_t_1, moisture_t_2, moisture_t_3 (lagged moisture)
      - temp_c, humidity_pct, vpd_kpa (environmental)
      - hour (time-of-day encoding)
      - days_since_watered (drying duration)
      - zone

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
    parser = argparse.ArgumentParser(
        description="Generate synthetic soil moisture data (v2 — causal decay)"
    )
    parser.add_argument("--samples", type=int, default=2000,
                        help="Steps per decay segment (default: 2000)")
    parser.add_argument("--segments", type=int, default=5,
                        help="Number of decay runs per zone (default: 5)")
    parser.add_argument("--interval", type=int, default=5,
                        help="Time step in minutes (default: 5)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed")
    parser.add_argument("--output", default="training_data.csv",
                        help="Output CSV path")
    args = parser.parse_args()

    total_steps = args.samples * args.segments * 3  # 3 zones
    print(f"🌱 Generating {total_steps} samples across {args.segments} segments × 3 zones...")
    print(f"   No irrigation events — pure exponential decay only")
    print(f"   Each segment starts at random moisture (30-90%)")
    df = generate_series(
        n_samples_per_segment=args.samples,
        n_segments_per_zone=args.segments,
        interval_minutes=args.interval,
        seed=args.seed,
    )
    print(f"   → {len(df)} rows generated")
    print(f"   → Moisture range: {df['moisture_pct'].min():.0f}% – {df['moisture_pct'].max():.0f}%")
    print(f"   → Temp range: {df['temp_c'].min():.1f}°C – {df['temp_c'].max():.1f}°C")

    # Save raw data
    df.to_csv(args.output, index=False)
    print(f"   ✅ Saved to {args.output}")

    # Build feature matrix for training
    print("\n🔧 Building feature matrix with lag features (grouped by segment)...")
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
    print(f"   Target: moisture prediction ({len(y)} samples, no irrigation bias)")


if __name__ == "__main__":
    main()
