#!/usr/bin/env python3
"""
test.py — Run RF inference from the command line with sensor values.

Model expects 9 features: moisture_t_1, moisture_t_2, moisture_t_3,
temp_c, humidity_pct, vpd_kpa, hour, days_since_watered, zone

Usage:
    # Named args (vpd_kpa optional — auto-calculated from temp + humidity)
    python3 models/rf/test.py \\
        --moisture_t_1 34.2 --moisture_t_2 35.1 --moisture_t_3 36.0 \\
        --temp_c 30.2 --humidity_pct 66.5 --hour 14 \\
        --days_since_watered 2.3 --zone 1

    # Positional shorthand (9 values)
    python3 models/rf/test.py \\
        34.2 35.1 36.0 30.2 66.5 1.23 14 2.3 1

    # Quiet mode (just predicted moisture %)
    python3 models/rf/test.py ... -q

Output:
    🌲 RF Irrigation Model — Inference
    ───────────────────────────────────────────────
    Model:          rf_model_pi.pkl
    Features:
      moisture_t_1       34.20
      moisture_t_2       35.10
      moisture_t_3       36.00
      temp_c             30.20
      humidity_pct       66.50
      vpd_kpa             1.23
      hour               14.00
      days_since_watered  2.30
      zone                1.00

    Predicted moisture:  31.8%
    Should irrigate?     YES
    Reason:              Predicted moisture (31.8%) will drop below threshold (35.0%)
"""

import argparse
import sys
import numpy as np
from pathlib import Path

# ── Model feature contract ─────────────────────────────────────────
# The RF was trained on these 9 features in this exact order
FEATURE_NAMES = [
    "moisture_t_1", "moisture_t_2", "moisture_t_3",
    "temp_c", "humidity_pct", "vpd_kpa",
    "hour", "days_since_watered", "zone",
]

# ── Try to load joblib ──────────────────────────────────────────────
try:
    import joblib
except ImportError:
    print("❌ Missing joblib. Run: pip install joblib")
    sys.exit(1)


def find_model() -> str:
    """Auto-detect the latest RF model in models/rf/."""
    script_dir = Path(__file__).resolve().parent
    candidates = list(script_dir.glob("rf_model*.pkl"))
    if not candidates:
        candidates = list(script_dir.glob("*.pkl"))
    if not candidates:
        print(f"❌ No .pkl model found in {script_dir}/")
        print("   Specify one with --model /path/to/model.pkl")
        sys.exit(1)
    return str(sorted(candidates)[-1])  # latest by name


def load_model(model_path: str):
    path = Path(model_path)
    if not path.exists():
        print(f"❌ Model not found: {path.resolve()}")
        sys.exit(1)
    return joblib.load(path)


def calc_vpd(temp_c: float, humidity_pct: float) -> float:
    """Calculate Vapour Pressure Deficit in kPa.

    Formula mirrors generate_data.py:calc_vpd().
    """
    es = 0.61078 * np.exp((17.27 * temp_c) / (temp_c + 237.3))
    vpd = (1 - humidity_pct / 100) * es
    return round(max(vpd, 0.05), 3)


def predict(model, features: dict) -> float:
    """Run prediction — builds 9-feature vector in model-trained order."""
    vector = np.array([[features[name] for name in FEATURE_NAMES]])
    predicted = model.predict(vector)
    return round(float(predicted[0]), 1)


def format_output(model_path: str, features: dict, predicted: float,
                  threshold: float = 35.0):
    """Print a clean inference result."""
    print()
    print("🌲 RF Irrigation Model — Inference")
    print("─" * 55)
    print(f"  Model:   {Path(model_path).name}")
    print()
    print("  Features:")
    for name in FEATURE_NAMES:
        val = features.get(name, 0)
        print(f"    {name:20s}  {val:>8.2f}")
    print()
    print(f"  Predicted moisture:  {predicted:.1f}%")

    # Decision logic (mirrors IrrigationModel.should_irrigate)
    moisture_current = features.get("moisture_t_1", 0)
    reasons = []
    if moisture_current < threshold:
        reasons.append(f"Current moisture ({moisture_current:.0f}%) below threshold ({threshold}%)")
    if predicted < threshold:
        reasons.append(f"Predicted moisture ({predicted:.0f}%) will drop below threshold ({threshold}%)")
    if predicted < moisture_current * 0.8:
        reasons.append(f"Sharp decline predicted ({moisture_current:.0f}% → {predicted:.0f}%)")

    if reasons:
        print(f"  Should irrigate?   YES")
        print(f"  Reason:            {reasons[0]}")
    else:
        print(f"  Should irrigate?   NO")
        print(f"  Reason:            Moisture stable, no action needed")
    print()

    return predicted


def main():
    parser = argparse.ArgumentParser(
        description="Run RF irrigation inference from sensor values."
    )

    parser.add_argument("--model", "-m", default=None,
                        help="Path to .pkl model file")
    parser.add_argument("--moisture_t_1", type=float, default=None)
    parser.add_argument("--moisture_t_2", type=float, default=None)
    parser.add_argument("--moisture_t_3", type=float, default=None)
    parser.add_argument("--temp_c", type=float, default=None)
    parser.add_argument("--humidity_pct", type=float, default=None)
    parser.add_argument("--vpd_kpa", type=float, default=None,
                        help="Vapour Pressure Deficit (auto-calculated from temp+humidity if omitted)")
    parser.add_argument("--hour", type=float, default=None)
    parser.add_argument("--days_since_watered", type=float, default=None)
    parser.add_argument("--zone", type=int, default=None)
    parser.add_argument("--threshold", type=float, default=35.0,
                        help="Irrigation threshold % (default: 35.0)")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Only output predicted moisture value")

    # Positional shorthand
    parser.add_argument("pos_args", nargs="*", help=argparse.SUPPRESS)

    args = parser.parse_args()

    # ── Collect features ──
    features = {}

    if args.pos_args and len(args.pos_args) >= 9:
        # Positional: 9 values in order
        pos_values = list(map(float, args.pos_args[:9]))
        for i, name in enumerate(FEATURE_NAMES):
            features[name] = pos_values[i]
    else:
        # Named args
        named = {
            "moisture_t_1": args.moisture_t_1,
            "moisture_t_2": args.moisture_t_2,
            "moisture_t_3": args.moisture_t_3,
            "temp_c": args.temp_c,
            "humidity_pct": args.humidity_pct,
            "vpd_kpa": args.vpd_kpa,
            "hour": args.hour,
            "days_since_watered": args.days_since_watered,
            "zone": args.zone,
        }
        for name, val in named.items():
            if val is not None:
                features[name] = val

    # ── Auto-calculate vpd_kpa if temp_c + humidity_pct provided ──
    if "vpd_kpa" not in features and "temp_c" in features and "humidity_pct" in features:
        features["vpd_kpa"] = calc_vpd(features["temp_c"], features["humidity_pct"])
        if not args.quiet:
            print(f"   vpd_kpa auto-calculated: {features['vpd_kpa']:.3f}")

    # ── Validate ──
    missing = [n for n in FEATURE_NAMES if n not in features]
    if missing:
        print(f"❌ Missing features: {', '.join(missing)}")
        print()
        parser.print_help()
        sys.exit(1)

    # ── Load model ──
    model_path = args.model if args.model else find_model()
    if not args.quiet:
        print(f"   Using model: {Path(model_path).name}")
    model = load_model(model_path)

    # ── Predict ──
    predicted = predict(model, features)

    # ── Output ──
    if args.quiet:
        print(predicted)
    else:
        format_output(model_path, features, predicted, args.threshold)


if __name__ == "__main__":
    main()
