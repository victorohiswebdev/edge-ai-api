#!/usr/bin/env python3
"""
train.py — Train 3-class plant health CNN (Healthy / Stressed / Wilted)

Uses MobileNetV2 transfer learning → TFLite export for Raspberry Pi 4.

Usage:
    # Train from PlantVillage dataset folder
    python3 models/cnn/train.py --data_dir models/cnn/dataset

    # Custom params
    python3 models/cnn/train.py --data_dir models/cnn/dataset \\
                                --output_dir models/cnn/models \\
                                --epochs 60 --batch_size 32 --img_size 224

Expected dataset structure under --data_dir:
    data_dir/
        healthy/   ← *.JPG
        stressed/  ← *.JPG
        wilted/    ← *.JPG
"""

import argparse
import json
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")  # non-interactive backend for headless training
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore", category=UserWarning, module="keras")

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"  # suppress TF info/warnings

# ── Imports that may fail if TF isn't installed ──────────────────────────
try:
    import tensorflow as tf
    from tensorflow import keras
    from keras import layers, models, mixed_precision
    from keras.applications import MobileNetV2
    from keras.callbacks import (
        EarlyStopping,
        ModelCheckpoint,
        ReduceLROnPlateau,
        TensorBoard,
    )
    from sklearn.metrics import (
        classification_report,
        confusion_matrix,
        ConfusionMatrixDisplay,
    )
except ImportError as e:
    print(f"❌ Missing dependency: {e}")
    print("   Run: pip install -r models/cnn/requirements.txt")
    sys.exit(1)

# ── Constants ────────────────────────────────────────────────────────────
CLASSES = ["healthy", "stressed", "wilted"]
CLASS_TO_IDX = {c: i for i, c in enumerate(CLASSES)}
SEED = 42
AUTOTUNE = tf.data.AUTOTUNE

tf.random.set_seed(SEED)
np.random.seed(SEED)


# ── Argument parser ──────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="Train 3-class plant health CNN")
    p.add_argument("--data_dir", default="models/cnn/dataset",
                    help="Path to dataset folder with healthy/stressed/wilted subdirs")
    p.add_argument("--output_dir", default="models/cnn/models",
                    help="Where to save model files, logs, and plots")
    p.add_argument("--epochs", type=int, default=50,
                    help="Max training epochs (default: 50)")
    p.add_argument("--batch_size", type=int, default=32,
                    help="Batch size (default: 32)")
    p.add_argument("--img_size", type=int, default=224,
                    help="Target image size in pixels (default: 224 — MobileNetV2 input)")
    p.add_argument("--learning_rate", type=float, default=1e-4,
                    help="Initial learning rate (default: 0.0001)")
    p.add_argument("--dropout", type=float, default=0.3,
                    help="Dropout rate in classifier head (default: 0.3)")
    p.add_argument("--finetune_layers", type=int, default=0,
                    help="Unfreeze last N layers of base model for fine-tuning (0 = frozen)")
    p.add_argument("--finetune_lr", type=float, default=1e-5,
                    help="Learning rate during fine-tuning phase (default: 1e-5)")
    p.add_argument("--val_split", type=float, default=0.15,
                    help="Validation split ratio (default: 0.15)")
    p.add_argument("--test_split", type=float, default=0.15,
                    help="Test split ratio (default: 0.15)")
    p.add_argument("--seed", type=int, default=SEED,
                    help=f"Random seed (default: {SEED})")
    return p.parse_args()


# ── Dataset loading & splitting ──────────────────────────────────────────
def load_dataset(data_dir: str, img_size: int, batch_size: int,
                 val_split: float, test_split: float, seed: int):
    """Load images from folder structure, split into train/val/test."""
    data_dir = Path(data_dir)
    if not data_dir.exists():
        print(f"❌ Dataset directory not found: {data_dir}")
        print("   Expected structure: {healthy/, stressed/, wilted/}")
        sys.exit(1)

    # Verify folder structure
    for cls in CLASSES:
        cls_dir = data_dir / cls
        if not cls_dir.exists():
            print(f"❌ Missing class folder: {cls_dir}")
            sys.exit(1)
        n = len(list(cls_dir.glob("*.JPG")) + list(cls_dir.glob("*.jpg")) +
                 list(cls_dir.glob("*.png")) + list(cls_dir.glob("*.jpeg")))
        print(f"   {cls}: {n} images")

    # Load full dataset using keras utility
    full_ds = keras.utils.image_dataset_from_directory(
        data_dir,
        labels="inferred",
        label_mode="categorical",
        class_names=CLASSES,
        color_mode="rgb",
        batch_size=None,  # no batching yet — we need to shuffle/split
        image_size=(img_size, img_size),
        shuffle=False,      # we'll shuffle manually after stratified split
        seed=seed,
    )

    # Collect all file paths for stratified splitting
    # Use the underlying file paths to do a stratified split
    file_paths = []
    labels = []
    for img, label in full_ds:
        file_paths.append(img)
        labels.append(label.numpy())

    file_paths = np.array(file_paths)
    labels = np.array(labels)
    n_total = len(file_paths)
    print(f"\n📊 Total samples: {n_total}")

    # Compute class distribution
    class_counts = labels.sum(axis=0)
    for i, cls in enumerate(CLASSES):
        print(f"   {cls}: {int(class_counts[i])} ({class_counts[i]/n_total*100:.1f}%)")

    # Stratified split: train / val / test
    from sklearn.model_selection import train_test_split

    # First split: separate test set
    train_val_idx, test_idx = train_test_split(
        np.arange(n_total),
        test_size=test_split,
        stratify=labels.argmax(axis=1),
        random_state=seed,
    )

    # Second split: separate train / val
    val_frac = val_split / (1 - test_split)  # adjusted fraction
    train_idx, val_idx = train_test_split(
        train_val_idx,
        test_size=val_frac,
        stratify=labels[train_val_idx].argmax(axis=1),
        random_state=seed,
    )

    print(f"\n📦 Split sizes:")
    print(f"   Train: {len(train_idx)} ({len(train_idx)/n_total*100:.1f}%)")
    print(f"   Val:   {len(val_idx)} ({len(val_idx)/n_total*100:.1f}%)")
    print(f"   Test:  {len(test_idx)} ({len(test_idx)/n_total*100:.1f}%)")

    def make_ds(indices, augment=False):
        """Build tf.data.Dataset from index array."""
        imgs = tf.gather(file_paths, indices)
        lbls = tf.gather(labels, indices)
        ds = tf.data.Dataset.from_tensor_slices((imgs, lbls))
        ds = ds.shuffle(len(indices), seed, reshuffle_each_iteration=True) if not augment else ds
        ds = ds.batch(batch_size)
        ds = ds.prefetch(AUTOTUNE)
        return ds

    train_ds = make_ds(train_idx)
    val_ds = make_ds(val_idx)
    test_ds = make_ds(test_idx)

    return train_ds, val_ds, test_ds, labels[train_idx], labels[val_idx], labels[test_idx]


# ── Data augmentation ────────────────────────────────────────────────────
def build_augmentation(img_size: int):
    """Return a Sequential model of on-device augmentation layers."""
    return keras.Sequential([
        layers.RandomFlip("horizontal", seed=SEED),
        layers.RandomRotation(0.15, seed=SEED),
        layers.RandomZoom(0.1, seed=SEED),
        layers.RandomBrightness(0.1, seed=SEED),
        layers.RandomContrast(0.1, seed=SEED),
    ], name="augmentation")


# ── Model architecture ───────────────────────────────────────────────────
def build_model(img_size: int, dropout_rate: float, learning_rate: float):
    """Build MobileNetV2-based transfer learning model."""
    # Preprocessing: keras.applications.mobilenet_v2.preprocess_input scales
    # pixels to [-1, 1]. We add a Rescaling layer to handle it.
    preprocess = keras.Sequential([
        layers.Rescaling(scale=1.0 / 127.5, offset=-1),
    ], name="preprocessing")

    # Load MobileNetV2 without top, with ImageNet weights
    base = MobileNetV2(
        input_shape=(img_size, img_size, 3),
        include_top=False,
        weights="imagenet",
    )
    base.trainable = False  # freeze by default

    # Build classifier head
    inputs = keras.Input(shape=(img_size, img_size, 3))
    x = preprocess(inputs)
    x = base(x, training=False)
    x = layers.GlobalAveragePooling2D(name="pool")(x)
    x = layers.Dropout(dropout_rate, name="dropout")(x)
    x = layers.Dense(128, activation="relu", name="dense")(x)
    x = layers.Dropout(dropout_rate / 2, name="dropout2")(x)
    outputs = layers.Dense(len(CLASSES), activation="softmax", name="predictions")(x)

    model = keras.Model(inputs=inputs, outputs=outputs, name="plant_health_cnn")

    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=learning_rate),
        loss="categorical_crossentropy",
        metrics=["accuracy", keras.metrics.Precision(name="precision"),
                 keras.metrics.Recall(name="recall")],
    )

    print(model.summary())
    return model, base


# ── Class weights ────────────────────────────────────────────────────────
def compute_class_weights(train_labels: np.ndarray) -> dict:
    """Compute balanced class weights inversely proportional to class frequency."""
    total = len(train_labels)
    cls_counts = train_labels.sum(axis=0)
    weights = {}
    for i, cls in enumerate(CLASSES):
        weight = total / (len(CLASSES) * cls_counts[i])
        weights[i] = round(weight, 3)
        print(f"   ⚖️  {cls}: class_weight = {weights[i]:.3f}")
    return weights


# ── Callbacks ────────────────────────────────────────────────────────────
def build_callbacks(output_dir: Path, model_name: str):
    """Return list of training callbacks."""
    checkpoint_path = output_dir / f"{model_name}_checkpoint.weights.h5"
    best_path = output_dir / f"{model_name}_best.keras"
    log_dir = output_dir / "logs" / datetime.now().strftime("%Y%m%d_%H%M%S")

    return [
        ModelCheckpoint(str(checkpoint_path), save_best_only=True,
                         save_weights_only=True, monitor="val_loss", mode="min",
                         verbose=1),
        ModelCheckpoint(str(best_path), save_best_only=True,
                         save_weights_only=False, monitor="val_loss", mode="min",
                         verbose=1),
        EarlyStopping(monitor="val_loss", patience=10, restore_best_weights=True,
                       verbose=1, min_delta=1e-4),
        ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=5,
                          min_lr=1e-7, verbose=1),
        TensorBoard(log_dir=str(log_dir), histogram_freq=1, write_graph=False),
    ]


# ── Evaluation ───────────────────────────────────────────────────────────
def evaluate_model(model: keras.Model, test_ds: tf.data.Dataset,
                   output_dir: Path, model_name: str):
    """Run test evaluation, generate metrics + plots."""
    print("\n🔍 Evaluating on test set...")
    test_loss, test_acc, test_prec, test_recall = model.evaluate(test_ds, verbose=0)
    print(f"   Test loss:      {test_loss:.4f}")
    print(f"   Test accuracy:  {test_acc:.4f} ({test_acc*100:.2f}%)")
    print(f"   Test precision: {test_prec:.4f}")
    print(f"   Test recall:    {test_recall:.4f}")

    # Collect predictions for confusion matrix
    y_true = []
    y_pred = []
    for imgs, lbls in test_ds:
        preds = model.predict(imgs, verbose=0)
        y_true.extend(np.argmax(lbls.numpy(), axis=1))
        y_pred.extend(np.argmax(preds, axis=1))
    y_true = np.array(y_true)
    y_pred = np.array(y_pred)

    # Classification report
    report = classification_report(y_true, y_pred, target_names=CLASSES, output_dict=True)
    print("\n📋 Classification Report:")
    print(classification_report(y_true, y_pred, target_names=CLASSES))

    # Confusion matrix
    cm = confusion_matrix(y_true, y_pred)
    fig, ax = plt.subplots(figsize=(8, 6))
    disp = ConfusionMatrixDisplay(cm, display_labels=CLASSES)
    disp.plot(ax=ax, cmap="Blues", values_format="d")
    ax.set_title(f"{model_name} — Confusion Matrix")
    plt.tight_layout()
    cm_path = output_dir / f"{model_name}_confusion_matrix.png"
    fig.savefig(cm_path, dpi=200)
    plt.close(fig)
    print(f"   📊 Confusion matrix saved: {cm_path}")

    # Training history plot (if available)
    history_path = output_dir / f"{model_name}_history.png"
    if hasattr(model, "history") and model.history.history:
        plot_training_history(model.history.history, history_path, model_name)

    # Save metrics as JSON
    metrics = {
        "model": model_name,
        "test_loss": float(test_loss),
        "test_accuracy": float(test_acc),
        "test_precision": float(test_prec),
        "test_recall": float(test_recall),
        "classification_report": report,
        "confusion_matrix": cm.tolist(),
        "timestamp": datetime.now().isoformat(),
    }
    metrics_path = output_dir / f"{model_name}_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"   📄 Metrics saved: {metrics_path}")

    return metrics


def plot_training_history(history: dict, save_path: Path, model_name: str):
    """Plot loss and accuracy curves."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    epochs_range = range(1, len(history["loss"]) + 1)

    # Loss
    axes[0].plot(epochs_range, history["loss"], "b-", label="Train Loss")
    axes[0].plot(epochs_range, history["val_loss"], "r-", label="Val Loss")
    axes[0].set_title("Loss")
    axes[0].set_xlabel("Epochs")
    axes[0].set_ylabel("Loss")
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)

    # Accuracy
    axes[1].plot(epochs_range, history["accuracy"], "b-", label="Train Acc")
    axes[1].plot(epochs_range, history["val_accuracy"], "r-", label="Val Acc")
    axes[1].set_title("Accuracy")
    axes[1].set_xlabel("Epochs")
    axes[1].set_ylabel("Accuracy")
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)

    fig.suptitle(f"{model_name} — Training History", fontsize=14)
    plt.tight_layout()
    fig.savefig(save_path, dpi=200)
    plt.close(fig)
    print(f"   📈 History plot saved: {save_path}")


# ── TFLite export ────────────────────────────────────────────────────────
def export_tflite(model: keras.Model, output_dir: Path, model_name: str,
                  img_size: int):
    """Convert trained Keras model to float16-quantized TFLite."""
    print("\n🔄 Converting to TFLite (float16 quantization)...")
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.target_spec.supported_types = [tf.float16]

    # Representative dataset for quantization calibration
    def representative_dataset():
        for _ in range(100):
            yield [np.random.randn(1, img_size, img_size, 3).astype(np.float32)]

    converter.representative_dataset = representative_dataset

    tflite_model = converter.convert()
    tflite_path = output_dir / f"{model_name}.tflite"
    with open(tflite_path, "wb") as f:
        f.write(tflite_model)
    size_mb = len(tflite_model) / (1024 * 1024)
    print(f"   ✅ TFLite model saved: {tflite_path} ({size_mb:.2f} MB)")

    # Verify with TFLite interpreter
    interpreter = tf.lite.Interpreter(model_content=tflite_model)
    interpreter.allocate_tensors()
    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()
    print(f"   Input shape:  {input_details[0]['shape']}")
    print(f"   Output shape: {output_details[0]['shape']}")
    print(f"   Input dtype:  {input_details[0]['dtype']}")
    print(f"   Output dtype: {output_details[0]['dtype']}")

    # Quick smoke test
    dummy_input = np.random.randn(1, img_size, img_size, 3).astype(np.float32)
    interpreter.set_tensor(input_details[0]["index"], dummy_input)
    interpreter.invoke()
    output = interpreter.get_tensor(output_details[0]["index"])
    print(f"   ✅ Inference smoke test passed — output shape: {output.shape}")

    return tflite_path


# ── Main training loop ───────────────────────────────────────────────────
def main():
    args = parse_args()

    data_dir = Path(args.data_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_name = f"plant_health_mobilenetv2_{timestamp}"

    print("=" * 60)
    print("🌱 Plant Health CNN — Training Pipeline")
    print("=" * 60)
    print(f"\n📂 Data dir:     {data_dir}")
    print(f"📂 Output dir:   {output_dir}")
    print(f"📐 Image size:   {args.img_size}x{args.img_size}")
    print(f"📦 Batch size:   {args.batch_size}")
    print(f"🔁 Max epochs:   {args.epochs}")
    print(f"🎯 Split:        train={1-args.val_split-args.test_split:.0%} "
          f"val={args.val_split:.0%} test={args.test_split:.0%}")
    print()

    # ── 1. Load dataset ──
    print("📦 Loading dataset...")
    train_ds, val_ds, test_ds, train_labels, val_labels, test_labels = load_dataset(
        data_dir, args.img_size, args.batch_size,
        args.val_split, args.test_split, args.seed,
    )

    # ── 2. Compute class weights ──
    print("\n⚖️  Computing class weights...")
    class_weights = compute_class_weights(train_labels)

    # ── 3. Build model ──
    print("\n🏗️  Building MobileNetV2 model...")
    model, base_model = build_model(args.img_size, args.dropout, args.learning_rate)

    # ── 4. Apply augmentation to training set ──
    print("\n🔄 Applying data augmentation...")
    augmenter = build_augmentation(args.img_size)
    train_ds_aug = train_ds.map(
        lambda x, y: (augmenter(x, training=True), y),
        num_parallel_calls=AUTOTUNE,
    ).prefetch(AUTOTUNE)

    # ── 5. Train ──
    print(f"\n🚀 Training phase 1 (transfer learning — base frozen)...")
    callbacks = build_callbacks(output_dir, model_name)
    history_phase1 = model.fit(
        train_ds_aug,
        validation_data=val_ds,
        epochs=args.epochs,
        callbacks=callbacks,
        class_weight=class_weights,
        verbose=2,
    )

    # Store history for plotting
    model.history = history_phase1

    # ── 6. Fine-tuning phase (optional) ──
    if args.finetune_layers > 0:
        print(f"\n🔧 Fine-tuning phase 2 — unfreezing last {args.finetune_layers} layers...")
        base_model.trainable = True
        for layer in base_model.layers[:-args.finetune_layers]:
            layer.trainable = False

        model.compile(
            optimizer=keras.optimizers.Adam(learning_rate=args.finetune_lr),
            loss="categorical_crossentropy",
            metrics=["accuracy",
                     keras.metrics.Precision(name="precision"),
                     keras.metrics.Recall(name="recall")],
        )

        # Count trainable params
        trainable = sum(tf.size(w).numpy() for w in model.trainable_weights)
        print(f"   Trainable params after unfreeze: {trainable:,}")

        ft_callbacks = build_callbacks(output_dir, f"{model_name}_ft")
        ft_epochs = max(args.epochs // 2, 20)
        history_phase2 = model.fit(
            train_ds_aug,
            validation_data=val_ds,
            epochs=ft_epochs,
            initial_epoch=history_phase1.epoch[-1] + 1,
            callbacks=ft_callbacks,
            class_weight=class_weights,
            verbose=2,
        )
        # Merge histories
        for k in history_phase2.history:
            history_phase1.history[k] = (
                history_phase1.history.get(k, []) + history_phase2.history[k]
            )

    # ── 7. Evaluate ──
    evaluate_model(model, test_ds, output_dir, model_name)

    # ── 8. Export to TFLite ──
    tflite_path = export_tflite(model, output_dir, model_name, args.img_size)

    # ── 9. Save final Keras model ──
    keras_path = output_dir / f"{model_name}.keras"
    model.save(keras_path)
    print(f"\n💾 Final Keras model saved: {keras_path}")

    print("\n" + "=" * 60)
    print("✅ Training complete!")
    print(f"   Model:          {model_name}")
    print(f"   TFLite:         {tflite_path}")
    print(f"   Keras:          {keras_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
