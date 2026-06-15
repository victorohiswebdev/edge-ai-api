"""
routes/irrigation.py — RF Predictive Irrigation endpoints.

Provides on-demand moisture prediction using the trained Random Forest model.
The IrrigationModel is loaded lazily on first request and cached.

Endpoints:
  GET /api/v1/irrigation/predict  — Run inference on latest sensor data
  GET /api/v1/irrigation/status   — Model metadata + health
"""

import os
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlite3 import Connection

from database import get_db
from schemas import IrrigationPrediction, IrrigationStatus
from models.rf.model import IrrigationModel

# ─── CNN Override Constants ─────────────────────────────────────────

# Decision matrix for RF + CNN combined irrigation logic:
#   RF says          CNN says     | Final action
#   ─────────────────────────────────────────────
#   Irrigate         Healthy      | Irrigate (no override)
#   Don't irrigate   Healthy      | Don't irrigate (no override)
#   Irrigate         Stressed     | DON'T irrigate (plant can't uptake)
#   Don't irrigate   Stressed     | Alert: manual check
#   Irrigate         Wilted       | DON'T irrigate
#   Don't irrigate   Wilted       | Alert: manual check

FINAL_IRRIGATE = "irrigate"
FINAL_DONT = "dont_irrigate"
FINAL_ALERT = "manual_check"

RF_YES = True
RF_NO = False


def _apply_override(rf_should_irrigate: bool, cnn_class: str | None) -> tuple:
    """Apply the decision matrix. Returns (final_action, reason, was_overridden)."""
    if cnn_class is None or cnn_class == "healthy":
        # No override — RF decision stands
        if rf_should_irrigate:
            return (FINAL_IRRIGATE, "Predicted moisture below threshold, plant healthy", False)
        return (FINAL_DONT, "Moisture stable, plant healthy", False)

    if cnn_class == "stressed":
        if rf_should_irrigate:
            return (FINAL_DONT, f"🔴 OVERRIDE: RF says irrigate but plant is stressed — cannot uptake water effectively", True)
        return (FINAL_ALERT, f"⚠️ Plant stressed — manual inspection recommended", True)

    if cnn_class == "wilted":
        if rf_should_irrigate:
            return (FINAL_DONT, f"🔴 OVERRIDE: RF says irrigate but plant is wilted — cannot uptake water", True)
        return (FINAL_ALERT, f"⚠️ Plant wilted — manual inspection recommended", True)

    # Fallback
    return (FINAL_IRRIGATE if rf_should_irrigate else FINAL_DONT, "RF decision (CNN class unknown)", False)

logger = logging.getLogger(__name__)

router = APIRouter()

# ─── Model loading (lazy singleton) ───────────────────────────────

_model: IrrigationModel | None = None
_model_path: str | None = None


def get_model() -> IrrigationModel:
    """Load and cache the IrrigationModel on first call."""
    global _model, _model_path

    if _model is not None:
        return _model

    # Auto-detect the best model file
    models_dir = os.path.join(os.path.dirname(__file__), "..", "models", "rf")
    candidates = []

    # Prefer Pi-optimised model, fall back to any .pkl
    for pattern in ("rf_model_pi.pkl", "rf_model.pkl", "*.pkl"):
        import glob
        matches = glob.glob(os.path.join(models_dir, pattern))
        # glob doesn't expand * in string pattern directly — use pathlib
        from pathlib import Path
        p = Path(models_dir)
        if "*" in pattern:
            matches = sorted(p.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)
        else:
            match_file = p / pattern
            if match_file.exists():
                matches = [match_file]
            else:
                matches = []

        if matches:
            model_path = str(matches[0])
            break
    else:
        raise RuntimeError(
            f"No RF model (.pkl) found in {models_dir}. "
            "Run models/rf/train.py first."
        )

    logger.info(f"Loading IrrigationModel from {model_path}")
    _model = IrrigationModel(model_path)
    _model_path = model_path
    return _model


# ─── Helpers ───────────────────────────────────────────────────────

def calc_vpd(temp_c: float, humidity_pct: float) -> float:
    """Calculate Vapour Pressure Deficit in kPa."""
    import numpy as np
    es = 0.61078 * np.exp((17.27 * temp_c) / (temp_c + 237.3))
    vpd = (1 - humidity_pct / 100) * es
    return round(max(vpd, 0.05), 3)


def get_latest_readings(cursor) -> dict:
    """Fetch the latest sensor data and last 3 moisture readings per zone."""
    # Latest reading
    cursor.execute(
        "SELECT * FROM latest_reading ORDER BY id DESC LIMIT 1"
    )
    latest = cursor.fetchone()
    if not latest:
        return None

    # Last 3 moisture readings per zone for lag features
    cursor.execute(
        "SELECT moisture_zone_1, moisture_zone_2, moisture_zone_3 "
        "FROM sensor_logs ORDER BY id DESC LIMIT 3"
    )
    recent = cursor.fetchall()

    result = {
        "moisture_zone_1": latest["moisture_zone_1"],
        "moisture_zone_2": latest["moisture_zone_2"],
        "moisture_zone_3": latest["moisture_zone_3"],
        "temperature_c": latest["temperature_c"],
        "humidity_perc": latest["humidity_perc"],
        "timestamp": latest["timestamp"],
    }

    # Build lag arrays: moisture_t_1 = latest, moisture_t_2 = prev, moisture_t_3 = prev2
    for z in (1, 2, 3):
        key = f"moisture_zone_{z}"
        vals = [r[key] for r in recent if r[key] is not None]
        # Pad with the latest if fewer than 3 readings available
        while len(vals) < 3:
            vals.append(vals[-1] if vals else result[key] or 0)
        result[f"moisture_t_{z}_1"] = vals[0]   # latest
        result[f"moisture_t_{z}_2"] = vals[1] if len(vals) > 1 else vals[0]
        result[f"moisture_t_{z}_3"] = vals[2] if len(vals) > 2 else vals[0]

    return result


def get_days_since_watered(cursor, zone: int) -> float:
    """Calculate days since last pump ON event for a zone."""
    cursor.execute(
        "SELECT created_at FROM pump_commands "
        "WHERE zone = ? AND command = 'ON' AND status = 'acknowledged' "
        "ORDER BY id DESC LIMIT 1",
        (zone,),
    )
    row = cursor.fetchone()
    if not row:
        return 99.0  # No watering ever recorded — assume very dry

    last_watered = datetime.fromisoformat(row["created_at"])
    delta = datetime.utcnow() - last_watered
    return round(delta.total_seconds() / 86400.0, 2)


# ─── Endpoints ─────────────────────────────────────────────────────

@router.get("/irrigation/predict")
def predict_irrigation(db: Connection = Depends(get_db)):
    """Run RF inference on the latest sensor data for all 3 zones.

    Returns predicted moisture, irrigation decision, and reasoning
    for each zone based on the current sensor state.
    """
    try:
        model = get_model()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    cursor = db.cursor()
    readings = get_latest_readings(cursor)
    if readings is None:
        raise HTTPException(
            status_code=404,
            detail="No sensor data available. Ensure data_logger is running.",
        )

    if readings["temperature_c"] is None:
        raise HTTPException(
            status_code=400,
            detail="Temperature/humidity data required for VPD calculation. "
                   "Check BME280 sensor.",
        )

    temp_c = float(readings["temperature_c"])
    humidity_pct = float(readings["humidity_perc"])
    vpd_kpa = calc_vpd(temp_c, humidity_pct)
    hour = datetime.now().hour

    results = []
    for zone in (1, 2, 3):
        moisture_t_1 = readings.get(f"moisture_t_{zone}_1", 0) or 0
        moisture_t_2 = readings.get(f"moisture_t_{zone}_2", 0) or 0
        moisture_t_3 = readings.get(f"moisture_t_{zone}_3", 0) or 0
        days_since_watered = get_days_since_watered(cursor, zone)

        predicted = model.predict(
            moisture_t_1=float(moisture_t_1),
            moisture_t_2=float(moisture_t_2),
            moisture_t_3=float(moisture_t_3),
            temp_c=temp_c,
            humidity_pct=humidity_pct,
            vpd_kpa=vpd_kpa,
            hour=float(hour),
            days_since_watered=days_since_watered,
            zone=zone,
        )

        should_water, reason = model.should_irrigate(
            moisture_current=float(moisture_t_1),
            predicted=predicted,
        )

        results.append({
            "zone": zone,
            "current_moisture": float(moisture_t_1),
            "predicted_moisture": predicted,
            "threshold": 35.0,
            "should_irrigate": should_water,
            "reason": reason,
            "days_since_watered": days_since_watered,
        })

    return {
        "status": "ok",
        "timestamp": readings["timestamp"],
        "environment": {
            "temperature_c": temp_c,
            "humidity_pct": humidity_pct,
            "vpd_kpa": vpd_kpa,
            "hour": hour,
        },
        "predictions": results,
    }


@router.get("/irrigation/status")
def irrigation_status():
    """Return model metadata and readiness."""
    try:
        model = get_model()
    except RuntimeError as e:
        return IrrigationStatus(
            model_loaded=False,
            model_name="none",
            features=[],
            n_features=0,
            message=str(e),
        )

    return IrrigationStatus(
        model_loaded=True,
        model_name=os.path.basename(_model_path) if _model_path else "unknown",
        features=model.feature_names,
        n_features=len(model.feature_names),
    )


@router.get("/irrigation/integrated-decision")
def integrated_irrigation_decision(db: Connection = Depends(get_db)):
    """Combined RF + CNN irrigation decision with override logic.

    1. Runs RF prediction on latest sensor data (same as /predict)
    2. Fetches latest CNN plant health classification
    3. Applies override decision matrix
    4. Returns final action per zone

    The CNN overrides the RF when plant stress/wilt is detected —
    stressed plants cannot uptake water effectively, so irrigation
    is suppressed until the plant recovers.
    """
    # ── Step 1: Get RF predictions ──
    try:
        model = get_model()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    cursor = db.cursor()
    readings = get_latest_readings(cursor)
    if readings is None:
        raise HTTPException(
            status_code=404,
            detail="No sensor data available. Ensure data_logger is running.",
        )

    if readings["temperature_c"] is None:
        raise HTTPException(
            status_code=400,
            detail="Temperature/humidity data required.",
        )

    temp_c = float(readings["temperature_c"])
    humidity_pct = float(readings["humidity_perc"])
    vpd_kpa = calc_vpd(temp_c, humidity_pct)
    hour = datetime.now().hour

    # ── Step 2: Get latest CNN classification ──
    latest_cnn = None
    try:
        cursor.execute(
            "SELECT classification, confidence FROM plant_health_log ORDER BY id DESC LIMIT 1"
        )
        cnn_row = cursor.fetchone()
        if cnn_row:
            latest_cnn = {
                "classification": cnn_row["classification"],
                "confidence": cnn_row["confidence"],
            }
    except Exception:
        pass  # Table may not exist yet

    # ── Step 3: Per-zone integrated decision ──
    results = []
    for zone in (1, 2, 3):
        moisture_t_1 = readings.get(f"moisture_t_{zone}_1", 0) or 0
        moisture_t_2 = readings.get(f"moisture_t_{zone}_2", 0) or 0
        moisture_t_3 = readings.get(f"moisture_t_{zone}_3", 0) or 0
        days_since_watered = get_days_since_watered(cursor, zone)

        predicted = model.predict(
            moisture_t_1=float(moisture_t_1),
            moisture_t_2=float(moisture_t_2),
            moisture_t_3=float(moisture_t_3),
            temp_c=temp_c,
            humidity_pct=humidity_pct,
            vpd_kpa=vpd_kpa,
            hour=float(hour),
            days_since_watered=days_since_watered,
            zone=zone,
        )

        should_water, rf_reason = model.should_irrigate(
            moisture_current=float(moisture_t_1),
            predicted=predicted,
        )

        # Apply CNN override
        cnn_class = latest_cnn["classification"] if latest_cnn else None
        final_action, final_reason, was_overridden = _apply_override(
            should_water, cnn_class
        )

        zone_result = {
            "zone": zone,
            "current_moisture": float(moisture_t_1),
            "predicted_moisture": predicted,
            "threshold": 35.0,
            "days_since_watered": days_since_watered,
            "rf_decision": {
                "should_irrigate": should_water,
                "reason": rf_reason,
            },
            "plant_health": latest_cnn or {"classification": None, "confidence": None, "available": False},
            "override_applied": was_overridden,
            "final_action": final_action,
            "final_reason": final_reason,
        }

        results.append(zone_result)

    return {
        "status": "ok",
        "timestamp": readings["timestamp"],
        "environment": {
            "temperature_c": temp_c,
            "humidity_pct": humidity_pct,
            "vpd_kpa": vpd_kpa,
            "hour": hour,
        },
        "integrated_decisions": results,
    }
