#!/usr/bin/env python3
"""
model.py — Lightweight inference module for Raspberry Pi deployment.

Loads the trained Random Forest model and provides a predict() function
that returns predicted moisture N steps ahead.

Usage on Pi:
    from models.rf.model import IrrigationModel

    model = IrrigationModel("models/rf/rf_model.pkl")
    prediction = model.predict(
        moisture_t_1=34.2, moisture_t_2=35.1, moisture_t_3=36.0,
        temp_c=30.2, humidity_pct=66.5, vpd_kpa=1.2,
        hour=14, days_since_watered=2.3, zone=1,
    )
    # Returns: predicted moisture in 3 time-steps (e.g., 31.8%)
"""

import os
import numpy as np
import joblib


class IrrigationModel:
    """Random Forest model wrapper for on-device inference."""

    def __init__(self, model_path: str):
        """Load model from joblib pickle file."""
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model not found: {model_path}")
        self.model = joblib.load(model_path)
        self.feature_names = [
            "moisture_t_1", "moisture_t_2", "moisture_t_3",
            "temp_c", "humidity_pct", "vpd_kpa",
            "hour", "days_since_watered", "zone",
        ]

    def predict(
        self,
        moisture_t_1: float,
        moisture_t_2: float,
        moisture_t_3: float,
        temp_c: float,
        humidity_pct: float,
        vpd_kpa: float,
        hour: float,
        days_since_watered: float,
        zone: int,
    ) -> float:
        """Predict moisture N steps ahead from current sensor readings.

        Parameters are named for clarity — each corresponds to the
        feature the model was trained on.
        """
        features = np.array([[
            moisture_t_1, moisture_t_2, moisture_t_3,
            temp_c, humidity_pct, vpd_kpa,
            hour, days_since_watered, zone,
        ]])
        pred = self.model.predict(features)
        return round(float(pred[0]), 1)

    def predict_batch(self, feature_matrix):
        """Predict for multiple samples at once.

        feature_matrix: list of dicts or array-like with columns
                        matching self.feature_names
        """
        return self.model.predict(feature_matrix)

    def should_irrigate(self, moisture_current: float, predicted: float,
                         threshold: float = 35.0) -> tuple:
        """Decision logic: should we water this zone?

        Returns (should_water, reason).
        """
        if moisture_current < threshold:
            return (True, f"Current moisture ({moisture_current:.0f}%) below threshold ({threshold}%)")

        if predicted < threshold:
            return (True, f"Predicted moisture ({predicted:.0f}%) will drop below threshold ({threshold}%)")

        if predicted < moisture_current * 0.8:
            return (True, f"Sharp decline predicted ({moisture_current:.0f}% → {predicted:.0f}%)")

        return (False, "Moisture stable, no action needed")

    def __repr__(self):
        n_estimators = getattr(self.model, "n_estimators", "?")
        return f"IrrigationModel(trees={n_estimators}, features=9)"


# ─── Quick test when run directly ────────────────────────────────

if __name__ == "__main__":
    import sys
    model_path = sys.argv[1] if len(sys.argv) > 1 else "models/rf/rf_model.pkl"

    print(f"🌲 Loading model from {model_path}...")
    model = IrrigationModel(model_path)
    print(f"   {model}")

    # Test prediction with realistic sensor values
    prediction = model.predict(
        moisture_t_1=42.0,
        moisture_t_2=43.5,
        moisture_t_3=45.0,
        temp_c=30.0,
        humidity_pct=65.0,
        vpd_kpa=1.3,
        hour=14,
        days_since_watered=1.5,
        zone=3,
    )
    print(f"\n📊 Test prediction: {prediction}%")

    # Test irrigation decision
    should_water, reason = model.should_irrigate(42.0, prediction)
    print(f"🚰 Irrigate? {'YES' if should_water else 'NO'} — {reason}")
