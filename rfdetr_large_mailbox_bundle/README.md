# RF-DETR Large mailbox bundle

This folder is portable: copy the complete folder into another project and keep
its internal paths unchanged.

## Included files

- `weights/last_ema.pth`: default final-epoch EMA weight used for the preferred predictions.
- `weights/checkpoint_best_total.pth`: alternate best-score inference weight.
- `weights/checkpoint_39.ckpt`: full training state for resume/fine-tuning.
- `predict.py`: standalone folder/image inference script.
- `metadata/`: original training configuration and metrics.
- `model_manifest.json`: model classes, weight roles, sizes, and SHA-256 checksums.

The model has one class: `mailbox`.

## Setup in another project

Use that project's Python environment:

```powershell
python -m pip install -r .\rfdetr_large_mailbox_bundle\requirements-rfdetr-large.txt
```

For NVIDIA GPU inference, install the PyTorch build appropriate for the target
machine's CUDA setup before installing the requirements.

## Predict a folder

From the parent directory of this bundle:

```powershell
python .\rfdetr_large_mailbox_bundle\predict.py --source ".\images" --conf 0.7 --output-dir ".\outputs" --split-output
```

The predictor automatically uses `weights/last_ema.pth`.

## Use from another Python module

```python
from rfdetr import RFDETRLarge

model = RFDETRLarge(
    pretrain_weights="rfdetr_large_mailbox_bundle/weights/last_ema.pth"
)
detections = model.predict(image, threshold=0.7)
```

Use `weights/checkpoint_39.ckpt` only with RF-DETR's `resume=` training option.
