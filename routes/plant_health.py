"""
routes/plant_health.py — CNN plant health classification endpoints.

Provides on-demand and automated plant health classification using
the trained MobileNetV2 TFLite model. Designed to work with the
existing camera capture pipeline.

Endpoints:
  GET  /api/v1/plant-health/latest       — Latest classification result
  GET  /api/v1/plant-health/classify     — Classify a specific capture by filename
  GET  /api/v1/plant-health/history      — Classification history
  GET  /api/v1/plant-health/status       — Model metadata + readiness
"""

import os
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlite3 import Connection

from database import get_db
from schemas import PlantHealthResult, PlantHealthStatus
from models.cnn.model import get_classifier, TFLiteClassifier

logger = logging.getLogger(__name__)

router = APIRouter()

CAPTURE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "captures")


def _get_classifier_safe() -> TFLiteClassifier | None:
    """Try to load the classifier, return None on failure."""
    try:
        return get_classifier()
    except (RuntimeError, FileNotFoundError) as e:
        logger.warning(f"TFLite classifier not available: {e}")
        return None


def _store_classification(cursor, conn, image_path: str, result: dict):
    """Store a classification result in the plant_health_log table."""
    cursor.execute("""
        INSERT INTO plant_health_log
        (image_path, classification, confidence, probabilities)
        VALUES (?, ?, ?, ?)
    """, (
        image_path,
        result["class"],
        result["confidence"],
        __import__("json").dumps(result["probabilities"]),
    ))
    conn.commit()


@router.get("/plant-health/classify")
def classify_capture(
    capture: str = Query(..., description="Filename of the capture to classify (e.g. snapshot_20260615_120000.jpg)"),
    db: Connection = Depends(get_db),
):
    """Classify a previously captured image by filename.

    Runs TFLite inference on the specified capture file and stores
    the result in the plant_health_log table.
    """
    classifier = _get_classifier_safe()
    if classifier is None:
        raise HTTPException(
            status_code=503,
            detail="TFLite classifier not available. Ensure TensorFlow or tflite-runtime is installed.",
        )

    # Validate path
    safe_path = os.path.normpath(os.path.join(CAPTURE_DIR, capture))
    if not safe_path.startswith(CAPTURE_DIR):
        raise HTTPException(400, "Invalid capture filename")
    if not os.path.exists(safe_path):
        raise HTTPException(404, f"Capture not found: {capture}")

    try:
        result = classifier.predict(safe_path)
    except Exception as e:
        raise HTTPException(500, f"Classification failed: {e}")

    # Store in DB
    cursor = db.cursor()
    _store_classification(cursor, db, safe_path, result)

    return {
        "status": "ok",
        "capture": capture,
        "classification": result["class"],
        "confidence": result["confidence"],
        "probabilities": result["probabilities"],
    }


@router.get("/plant-health/latest")
def latest_classification(db: Connection = Depends(get_db)):
    """Return the most recent classification result."""
    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM plant_health_log ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(404, "No classification results yet")

    import json
    return PlantHealthResult(
        id=row["id"],
        timestamp=row["timestamp"],
        image_path=row["image_path"],
        classification=row["classification"],
        confidence=row["confidence"],
        probabilities=json.loads(row["probabilities"]) if row["probabilities"] else {},
    )


@router.get("/plant-health/history")
def classification_history(
    limit: int = Query(10, ge=1, le=100),
    db: Connection = Depends(get_db),
):
    """Return recent classification results."""
    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM plant_health_log ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = cursor.fetchall()

    import json
    results = []
    for row in rows:
        results.append(PlantHealthResult(
            id=row["id"],
            timestamp=row["timestamp"],
            image_path=row["image_path"],
            classification=row["classification"],
            confidence=row["confidence"],
            probabilities=json.loads(row["probabilities"]) if row["probabilities"] else {},
        ))

    return {"results": results, "count": len(results)}


@router.get("/plant-health/status")
def plant_health_status():
    """Return TFLite model metadata and readiness."""
    classifier = _get_classifier_safe()
    if classifier is None:
        return PlantHealthStatus(
            model_loaded=False,
            model_name="none",
            classes=[],
            message="TFLite runtime not available or no model found",
        )

    info = classifier.get_model_info()
    return PlantHealthStatus(
        model_loaded=True,
        model_name=os.path.basename(classifier.model_path) if classifier.model_path else "unknown",
        classes=["healthy", "stressed", "wilted"],
        input_shape=str(info.get("input_shape", [])),
    )
