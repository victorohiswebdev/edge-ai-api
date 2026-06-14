# Random Forest — Predictive Irrigation Model

Soil moisture forecasting using scikit-learn Random Forest regression.

## Pipeline

```bash
# 1. Generate synthetic training data
python3 models/rf/generate_data.py --samples 15000 --interval 5

# 2. Train + validate + export model
python3 models/rf/train.py --trees 100 --depth 12
```

## Output

All artifacts go to `models/rf/`:

| File | Description |
|------|-------------|
| `rf_model.pkl` | Trained model (joblib) |
| `rf_model_metrics.json` | Validation metrics + feature importance |
| `training_data.csv` | Raw synthetic sensor time series |
| `training_data_features.csv` | Feature matrix with lagged targets |

## Inference

```python
from models.rf.model import IrrigationModel

model = IrrigationModel("models/rf/rf_model.pkl")
prediction = model.predict(
    moisture_t_1=34.2, moisture_t_2=35.1, moisture_t_3=36.0,
    temp_c=30.2, humidity_pct=66.5, vpd_kpa=1.2,
    hour=14, days_since_watered=2.3, zone=1,
)
# → predicted moisture % (e.g., 31.8)
```

## Constraints

- **50 trees / depth 10** on Pi (lightweight inference, ~4.3 MB)
- **100 trees / depth 12+** on PC for development
- All training runs on your PC — Pi only runs inference
