#!/usr/bin/env python3
"""
train.py — Train + validate + export the Random Forest irrigation model

Run this AFTER generate_data.py has created training_data_features.csv.

Usage:
    python3 models/rf/train.py [--data training_data_features.csv]
                               [--output rf_model.pkl]
                               [--trees 100] [--depth 12]
"""

import argparse
import os
import sys
import json
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.inspection import permutation_importance
import joblib


def train_model(
    data_path="/models/rf/training_data_features.csv",
    model_output="/models/rf/rf_model.pkl",
    n_estimators=100,
    max_depth=12,
    test_size=0.2,
    seed=42,
):
    """Full training pipeline: load → split → train → validate → export."""
    print("=" * 60)
    print("🌲 RANDOM FOREST — Predictive Irrigation Model")
    print("=" * 60)

    # ── Load data ──
    print(f"\n📂 Loading data from {data_path}...")
    df = pd.read_csv(data_path)
    print(f"   → {len(df)} samples, {df.shape[1]} columns")

    # Separate features and target
    target_col = "target"
    y = df[target_col]
    X = df.drop(columns=[target_col])

    print(f"   → Features: {list(X.columns)}")
    print(f"   → Target: moisture_N_steps_ahead (mean={y.mean():.1f}%, std={y.std():.1f}%)")

    # ── Train/test split ──
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=seed
    )
    print(f"\n📊 Split: {len(X_train)} train / {len(X_test)} test")

    # ── Train ──
    print(f"\n🌲 Training RF (trees={n_estimators}, depth={max_depth})...")
    model = RandomForestRegressor(
        n_estimators=n_estimators,
        max_depth=max_depth,
        min_samples_split=5,
        min_samples_leaf=2,
        random_state=seed,
        n_jobs=-1,          # use all CPU cores
        verbose=1,
    )
    model.fit(X_train, y_train)
    print("   ✅ Training complete")

    # ── Evaluate ──
    print("\n📈 Validation Results:")
    print("-" * 40)

    for name, X_eval, y_eval in [("Train", X_train, y_train), ("Test", X_test, y_test)]:
        y_pred = model.predict(X_eval)
        mae = mean_absolute_error(y_eval, y_pred)
        rmse = np.sqrt(mean_squared_error(y_eval, y_pred))
        r2 = r2_score(y_eval, y_pred)

        print(f"\n  {name} Set:")
        print(f"    MAE:  {mae:.2f}%  (mean absolute error)")
        print(f"    RMSE: {rmse:.2f}%  (root mean squared error)")
        print(f"    R²:   {r2:.4f}  (r-squared — 1.0 = perfect)")

    # ── Feature importance ──
    print("\n🔍 Feature Importance:")
    print("-" * 40)
    importances = sorted(
        zip(X.columns, model.feature_importances_),
        key=lambda x: x[1], reverse=True,
    )
    for feat, imp in importances:
        bar = "█" * int(imp * 40)
        print(f"  {feat:25s} {imp:.3f}  {bar}")

    # ── Permutation importance (more robust) ──
    print("\n🔄 Permutation Importance (on test set):")
    print("-" * 40)
    perm_result = permutation_importance(
        model, X_test, y_test, n_repeats=10, random_state=seed, n_jobs=-1
    )
    perm_importances = sorted(
        zip(X.columns, perm_result.importances_mean, perm_result.importances_std),
        key=lambda x: x[1], reverse=True,
    )
    for feat, mean_imp, std_imp in perm_importances:
        bar = "█" * int(mean_imp * 20)
        print(f"  {feat:25s} {mean_imp:.3f} ± {std_imp:.4f}  {bar}")

    # ── Save model ──
    os.makedirs(os.path.dirname(model_output) or ".", exist_ok=True)
    joblib.dump(model, model_output)
    print(f"\n💾 Model saved to {model_output}")
    print(f"   File size: {os.path.getsize(model_output) / 1024:.1f} KB")

    # ── Save metrics ──
    metrics = {
        "n_samples": len(df),
        "n_features": X.shape[1],
        "n_estimators": n_estimators,
        "max_depth": max_depth,
        "test_mae": round(mean_absolute_error(y_test, model.predict(X_test)), 3),
        "test_rmse": round(np.sqrt(mean_squared_error(y_test, model.predict(X_test))), 3),
        "test_r2": round(r2_score(y_test, model.predict(X_test)), 4),
        "feature_importance": {
            feat: round(imp, 4) for feat, imp in importances
        },
    }
    metrics_path = model_output.replace(".pkl", "_metrics.json")
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"   Metrics saved to {metrics_path}")

    print("\n✅ Training complete!")
    return model, metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train RF irrigation model")
    parser.add_argument("--data", default="/models/rf/training_data_features.csv",
                        help="Path to feature CSV")
    parser.add_argument("--output", default="/models/rf/rf_model.pkl",
                        help="Output model path")
    parser.add_argument("--trees", type=int, default=100,
                        help="Number of trees")
    parser.add_argument("--depth", type=int, default=12,
                        help="Max tree depth")
    args = parser.parse_args()

    train_model(
        data_path=args.data,
        model_output=args.output,
        n_estimators=args.trees,
        max_depth=args.depth,
    )
