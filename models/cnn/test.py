#!/usr/bin/env python3
"""
test.py — Classify a single plant image using the trained TFLite model.

Usage:
    python3 models/cnn/test.py snapshot.jpg
    python3 models/cnn/test.py path/to/image.jpg --model models/cnn/models/plant_health_mobilenetv2_20260614_080008.tflite

Output:
    🌱 Plant Health Classification
    ─────────────────────────────
    Image:       snapshot.jpg
    Prediction:  healthy  (94.3%)
    Confidence:
       healthy    0.943  ★
       stressed   0.041
       wilted     0.016

Preprocessing:
  - Resize to 224×224 (LANCZOS)
  - Convert to float32 (raw [0, 255] — model handles normalization internally)
  - Batch dimension added
"""

import argparse
import sys
import numpy as np
from pathlib import Path

CLASSES = ["healthy", "stressed", "wilted"]
IMG_SIZE = 224

# ── Try loading TFLite runtime ──────────────────────────────────────────
try:
    import tensorflow as tf
    _HAS_TF = True
except ImportError:
    try:
        import tflite_runtime.interpreter as tflite
        _HAS_TF = False
    except ImportError:
        print("❌ No inference runtime found.")
        print("   Install: pip install tensorflow  (or tflite-runtime on Pi)")
        sys.exit(1)

# ── Try loading PIL ─────────────────────────────────────────────────────
try:
    from PIL import Image
except ImportError:
    print("❌ Missing PIL. Run: pip install pillow")
    sys.exit(1)


def load_tflite_model(model_path: str):
    """Load a TFLite model, return (interpreter, input_details, output_details)."""
    path = Path(model_path)
    if not path.exists():
        print(f"❌ Model not found: {path.resolve()}")
        print(f"   Available models in {path.parent}:" if path.parent.exists() else "")
        if path.parent.exists():
            for f in sorted(path.parent.glob("*.tflite")):
                print(f"     {f.name}")
        sys.exit(1)

    if _HAS_TF:
        interpreter = tf.lite.Interpreter(model_path=str(path))
    else:
        interpreter = tflite.Interpreter(model_path=str(path))

    interpreter.allocate_tensors()
    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()

    # Validate input shape
    expected_shape = (1, IMG_SIZE, IMG_SIZE, 3)
    actual_shape = input_details[0]["shape"]
    if list(actual_shape) != list(expected_shape):
        print(f"⚠️  Model input shape {list(actual_shape)} != expected {list(expected_shape)}")
        print(f"   Will resize to 224×224 anyway; results may be unexpected.")

    return interpreter, input_details, output_details


def load_and_preprocess(image_path: str) -> np.ndarray:
    """Load image, resize to 224×224, normalize to [-1, 1].

    Must match the pipeline in train.py:
        Rescaling(scale=1.0/127.5, offset=-1)  →  pixel / 127.5 - 1
    """
    path = Path(image_path)
    if not path.exists():
        print(f"❌ Image not found: {path.resolve()}")
        sys.exit(1)

    # Load and convert to RGB
    img = Image.open(path).convert("RGB")

    # Resize maintaining the training dimensions
    img = img.resize((IMG_SIZE, IMG_SIZE), Image.LANCZOS)

    # Convert to numpy float32 — keep raw [0, 255] range
    # The TFLite model has Rescaling(scale=1/127.5, offset=-1) built in
    arr = np.array(img, dtype=np.float32)

    # Add batch dimension: (224, 224, 3) → (1, 224, 224, 3)
    arr = np.expand_dims(arr, axis=0)

    return arr


def predict(interpreter, input_details, output_details, image_array: np.ndarray) -> np.ndarray:
    """Run inference, return softmax probabilities shape (1, 3)."""
    interpreter.set_tensor(input_details[0]["index"], image_array)
    interpreter.invoke()
    output = interpreter.get_tensor(output_details[0]["index"])
    return output


def format_output(image_path: str, probs: np.ndarray):
    """Print a clean classification result."""
    probs = probs.flatten()
    pred_idx = int(np.argmax(probs))
    pred_class = CLASSES[pred_idx]
    pred_conf = probs[pred_idx]

    print()
    print("🌱 Plant Health Classification")
    print("─" * 35)
    print(f"  Image:       {Path(image_path).name}")
    print(f"  Prediction:  {pred_class}  ({pred_conf*100:.1f}%)")
    print()
    print("  Confidence:")
    for i, cls in enumerate(CLASSES):
        marker = "★" if i == pred_idx else " "
        bar_len = int(probs[i] * 20)
        bar = "█" * bar_len
        print(f"    {cls:12s}  {probs[i]:.3f}  {bar}{marker}")
    print()

    return pred_class, pred_conf


def main():
    parser = argparse.ArgumentParser(
        description="Classify a plant image using the trained CNN (TFLite)."
    )
    parser.add_argument("image", help="Path to an image file (jpg/png)")
    parser.add_argument(
        "--model", "-m",
        default=None,
        help="Path to .tflite model file (auto-detects latest if not specified)",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Only output the class name (for scripting)",
    )
    args = parser.parse_args()

    # ── Auto-detect model if not specified ──
    model_path = args.model
    if model_path is None:
        models_dir = Path(__file__).resolve().parent / "models"
        tflite_files = sorted(models_dir.glob("*.tflite"))
        if not tflite_files:
            print(f"❌ No .tflite models found in {models_dir}/")
            print("   Specify one with --model /path/to/model.tflite")
            sys.exit(1)
        model_path = str(tflite_files[-1])  # latest by name (timestamp suffix)
        if not args.quiet:
            print(f"   Using model: {Path(model_path).name}")

    # ── Load model ──
    interpreter, input_details, output_details = load_tflite_model(model_path)

    # ── Load and preprocess image ──
    image_array = load_and_preprocess(args.image)

    # ── Run inference ──
    probs = predict(interpreter, input_details, output_details, image_array)

    # ── Output ──
    if args.quiet:
        pred_idx = int(np.argmax(probs))
        print(CLASSES[pred_idx])
    else:
        format_output(args.image, probs)


if __name__ == "__main__":
    main()
