#!/usr/bin/env python3
"""
test.py — Run RF inference from the command line with sensor values.

Usage:
    python3 models/rf/test.py \\
        --moisture_t_1 34.2 --moisture_t_2 35.1 --moisture_t_3 36.0 \\
        --temp_c 30.2 --humidity_pct 66.5 --hour 14 \\
        --days_since_watered 2.3 --zone 1

    # Short form (positional)
    python3 models/rf/test.py 34.2 35.1 36.0 30.2 66.5 14 2.3 1

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
      hour               14.00
      days_since_watered  2.30
      zone                1

    Predicted moisture:  31.8%
    Should irrigate?     YES
    Reason:              Predicted moisture (31.8%) will drop below threshold (35.0%)
"""

import argparse
import sys
import numpy as np
from pathlib import Path

FEATURE_NAMES = [
    "moisture_t_1", "moisture_t_2", "moisture_t_3",
    "temp_c", "humidity_pct",
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
    # Look for .pkl files in models/rf/ (relative to repo root) and script dir
    candidates = list(script_dir.glob("rf_model*.pkl"))
    if not candidates:
        # Try repo root path
        repo_models = script_dir / "models"
        if repo_models.exists():
            candidates = list(repo_models.glob("rf_model*.pkl"))
    if not candidates:
        candidates = list(script_dir.glob("*.pkl"))
    if not candidates:
        print(f"❌ No .pkl model found in {script_dir}/")
        print("   Specify one with --model /path/to/model.pkl")
        sys.exit(1)
    return str(sorted(candidates)[-1])  # latest by name


def load_model(model_path: str):
    """Load a joblib RF model."""
    path = Path(model_path)
    if not path.exists():
        print(f"❌ Model not found: {path.resolve()}")
        sys.exit(1)

    model = joblib.load(path)
    return model


def predict(model, features: dict) -> float:
    """Run prediction from a dict of feature values.

    Order must match: moisture_t_1, moisture_t_2, moisture_t_3,
    temp_c, humidity_pct, hour, days_since_watered, zone
    """
    # Note: vpd_kpa is NOT in our CLI — the model's feature set
    # includes it, but the Pi-optimised model was trained with/without.
    # We only pass the 9 features from the original model.
    # Actually — check what features the model expects.

    # Build feature vector in the right order
    feature_vector = np.array([[
        features["moisture_t_1"],
        features["moisture_t_2"],
        features["moisture_t_3"],
        features["temp_c"],
        features["humidity_pct"],
        features["hour"],
        features["days_since_watered"],
        features["zone"],
    ]])

    predicted = model.predict(feature_vector)
    return round(float(predicted[0]), 1)


def format_output(model_path: str, features: dict, predicted: float,
                  threshold: float = 35.0):
    """Print a clean inference result."""
    print()
    print("🌲 RF Irrigation Model — Inference")
    print("─" * 50)
    print(f"  Model:          {Path(model_path).name}")
    print()
    print("  Features:")
    for name in FEATURE_NAMES:
        if name in ("vpd_kpa",):
            continue
        val = features.get(name, 0)
        print(f"    {name:20s}  {val:>8.2f}")
    print()
    print(f"  Predicted moisture:  {predicted:.1f}%")

    # Decision logic (mirrors model.should_irrigate)
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

    # Named arguments
    parser.add_argument("--model", "-m", default=None,
                        help="Path to .pkl model file")
    parser.add_argument("--moisture_t_1", type=float, default=None)
    parser.add_argument("--moisture_t_2", type=float, default=None)
    parser.add_argument("--moisture_t_3", type=float, default=None)
    parser.add_argument("--temp_c", type=float, default=None)
    parser.add_argument("--humidity_pct", type=float, default=None)
    parser.add_argument("--hour", type=float, default=None)
    parser.add_argument("--days_since_watered", type=float, default=None)
    parser.add_argument("--zone", type=int, default=None)
    parser.add_argument("--threshold", type=float, default=35.0,
                        help="Irrigation threshold % (default: 35.0)")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Only output predicted moisture value")

    # Also accept short positional form
    parser.add_argument("pos_args", nargs="*", help=argparse.SUPPRESS)

    args = parser.parse_args()

    # ── Collect features ──
    features = {}
    named = {
        "moisture_t_1": args.moisture_t_1,
        "moisture_t_2": args.moisture_t_2,
        "moisture_t_3": args.moisture_t_3,
        "temp_c": args.temp_c,
        "humidity_pct": args.humidity_pct,
        "hour": args.hour,
        "days_since_watered": args.days_since_watered,
        "zone": args.zone,
    }

    # If positional args provided (short form)
    if args.pos_args and len(args.pos_args) >= 8:
        pos_values = list(map(float, args.pos_args[:8]))
        for i, name in enumerate(FEATURE_NAMES):
            features[name] = pos_values[i]
    else:
        # Use named args
        for name, val in named.items():
            if val is not None:
                features[name] = val

    # Validate
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
