#!/usr/bin/env python3
"""
model.py — TFLite inference wrapper for plant health classification.

Reusable module that mirrors the preprocessing and inference logic from
test.py. Designed to be imported by both the FastAPI routes and the
standalone test script.

The TFLite model has Rescaling(scale=1/127.5, offset=-1) built in,
so raw [0, 255] float32 input is expected — do NOT double-normalize.

Usage:
    from models.cnn.model import TFLiteClassifier

    clf = TFLiteClassifier()
    clf.load("models/cnn/models/plant_health_mobilenetv2_20260614_080008.tflite")
    result = clf.predict("captures/snapshot_20260615_120000.jpg")
    # Returns: {"class": "healthy", "confidence": 0.94,
    #           "probabilities": {"healthy": 0.94, "stressed": 0.04, "wilted": 0.02}}
"""

import os
import sys
import json
import numpy as np
from pathlib import Path

CLASSES = ["healthy", "stressed", "wilted"]
IMG_SIZE = 224

# ── Try TFLite runtime ──────────────────────────────────────────────
try:
    import tensorflow as tf
    _interpreter_cls = tf.lite.Interpreter
    _HAS_TF = True
except ImportError:
    try:
        import tflite_runtime.interpreter as tflite
        _interpreter_cls = tflite.Interpreter
        _HAS_TF = False
    except ImportError:
        _interpreter_cls = None
        _HAS_TF = False

# ── PIL ──────────────────────────────────────────────────────────────
try:
    from PIL import Image
except ImportError:
    Image = None


class TFLiteClassifier:
    """Plant health classifier using a MobileNetV2 TFLite model."""

    def __init__(self):
        self.interpreter = None
        self.input_details = None
        self.output_details = None
        self.model_path = None
        self._input_index = None
        self._output_index = None

    def load(self, model_path: str) -> bool:
        """Load a TFLite model. Returns True on success."""
        if _interpreter_cls is None:
            raise RuntimeError(
                "No TFLite runtime found. Install: pip install tensorflow "
                "(or tflite-runtime on Pi)"
            )

        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"TFLite model not found: {path.resolve()}")

        self.interpreter = _interpreter_cls(model_path=str(path))
        self.interpreter.allocate_tensors()
        self.input_details = self.interpreter.get_input_details()
        self.output_details = self.interpreter.get_output_details()
        self.model_path = str(path)

        self._input_index = self.input_details[0]["index"]
        self._output_index = self.output_details[0]["index"]

        return True

    def is_loaded(self) -> bool:
        """Check if a model is loaded and ready."""
        return self.interpreter is not None

    def load_latest(self, models_dir: str = None) -> str:
        """Auto-detect and load the latest .tflite model from a directory.

        Returns the loaded model path.
        """
        if models_dir is None:
            models_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "models"
            )
        md = Path(models_dir)
        tflite_files = sorted(md.glob("*.tflite"))
        if not tflite_files:
            raise FileNotFoundError(
                f"No .tflite models found in {md}/"
            )
        model_path = str(tflite_files[-1])  # latest by name (timestamp suffix)
        self.load(model_path)
        return model_path

    def preprocess(self, image_path: str) -> np.ndarray:
        """Load image, resize to 224×224, return raw [0, 255] float32 array.

        Must match the pipeline in train.py:
          Rescaling(scale=1.0/127.5, offset=-1)
        The model handles normalization internally.
        """
        if Image is None:
            raise RuntimeError("PIL not available. Install: pip install pillow")

        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path.resolve()}")

        img = Image.open(path).convert("RGB")
        img = img.resize((IMG_SIZE, IMG_SIZE), Image.LANCZOS)
        arr = np.array(img, dtype=np.float32)
        arr = np.expand_dims(arr, axis=0)  # (1, 224, 224, 3)
        return arr

    def predict(self, image_path: str) -> dict:
        """Run inference on an image file.

        Returns:
            dict with keys: class, confidence, probabilities
        """
        if not self.is_loaded():
            raise RuntimeError("No model loaded. Call load() first.")

        image_array = self.preprocess(image_path)
        self.interpreter.set_tensor(self._input_index, image_array)
        self.interpreter.invoke()
        output = self.interpreter.get_tensor(self._output_index)

        probs = output.flatten()
        pred_idx = int(np.argmax(probs))

        return {
            "class": CLASSES[pred_idx],
            "confidence": round(float(probs[pred_idx]), 4),
            "probabilities": {
                CLASSES[i]: round(float(probs[i]), 4)
                for i in range(len(CLASSES))
            },
        }

    def get_model_info(self) -> dict:
        """Return model metadata."""
        if not self.is_loaded():
            return {"loaded": False}
        return {
            "loaded": True,
            "model": Path(self.model_path).name if self.model_path else None,
            "classes": CLASSES,
            "input_shape": list(self.input_details[0]["shape"]) if self.input_details else None,
            "input_dtype": str(self.input_details[0]["dtype"]) if self.input_details else None,
        }


# ─── Singleton for API use ──────────────────────────────────────────

_classifier: TFLiteClassifier | None = None


def get_classifier() -> TFLiteClassifier:
    """Get or create the singleton classifier instance.

    Auto-loads the latest .tflite model on first call.
    """
    global _classifier
    if _classifier is None:
        _classifier = TFLiteClassifier()
        try:
            _classifier.load_latest()
        except (FileNotFoundError, RuntimeError) as e:
            raise RuntimeError(f"Failed to load TFLite model: {e}")
    return _classifier


# ─── Quick test when run directly ──────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Test TFLite classifier")
    parser.add_argument("image", help="Path to image file")
    parser.add_argument("--model", "-m", default=None, help="TFLite model path")
    args = parser.parse_args()

    clf = TFLiteClassifier()
    if args.model:
        clf.load(args.model)
    else:
        path = clf.load_latest()
        print(f"   Using model: {Path(path).name}")

    result = clf.predict(args.image)
    print(f"\n🌱 Plant Health Classification")
    print(f"   Image:       {Path(args.image).name}")
    print(f"   Prediction:  {result['class']}  ({result['confidence']*100:.1f}%)")
    print(f"\n   Confidence:")
    for cls, prob in result["probabilities"].items():
        marker = "★" if cls == result["class"] else " "
        bar = "█" * int(prob * 20)
        print(f"     {cls:12s}  {prob:.3f}  {bar}{marker}")
