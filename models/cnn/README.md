# Plant Health CNN — Training Pipeline

3-class image classifier (Healthy / Stressed / Wilted) for Pi Camera deployment.

## Setup (on your PC)

```bash
# 1. Install dependencies
pip install -r models/cnn/requirements.txt

# 2. Copy the PlantVillage dataset into place
#    (from the tar.gz on the Pi or wherever you have it stored)
cp /path/to/plantvillage_tomato.tar.gz models/cnn/
cd models/cnn && tar xzf plantvillage_tomato.tar.gz && mv plantvillage_tomato dataset && cd ../..

# 3. Train
python3 models/cnn/train.py
```

## Training

```bash
# Default (MobileNetV2, 50 epochs, 224px)
python3 models/cnn/train.py

# Custom parameters
python3 models/cnn/train.py --data_dir models/cnn/dataset \\
                            --output_dir models/cnn/models \\
                            --epochs 60 --batch_size 32 --img_size 224 \\
                            --learning_rate 1e-4 --dropout 0.3
```

## Output

All files go to `models/cnn/models/`:

| File | Description |
|------|-------------|
| `plant_health_mobilenetv2_TIMESTAMP.keras` | Full Keras model |
| `plant_health_mobilenetv2_TIMESTAMP.tflite` | Float16 quantized TFLite (for Pi) |
| `plant_health_mobilenetv2_TIMESTAMP_metrics.json` | Test metrics + classification report |
| `plant_health_mobilenetv2_TIMESTAMP_confusion_matrix.png` | Confusion matrix plot |
| `plant_health_mobilenetv2_TIMESTAMP_history.png` | Training curves |
| `plant_health_mobilenetv2_TIMESTAMP_best.keras` | Best checkpoint (by val_loss) |
| `logs/` | TensorBoard logs |

## Fine-tuning

To unfreeze the last N layers of MobileNetV2 for domain adaptation:

```bash
python3 models/cnn/train.py --finetune_layers 30 --finetune_lr 1e-5
```

This runs phase 1 (frozen base) first, then phase 2 (partial unfreeze).
