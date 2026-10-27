# Agent 4 — Image Enhancement

Agent 4 upscales and enhances every Agent 3 image, then calculates frontage
geometry at the enhanced resolution. Detection is Agent 5 and ownership
rejection is isolated in Agent 6.

No neighbour-blurred image is created or used.

Production detection is locked to
`rfdetr_large_mailbox_bundle/weights/last_ema.pth`, the bundle's preferred
one-class `mailbox` checkpoint. Agent 5 accepts only the `mailbox` class with
confidence strictly greater than `0.70`. Scores through `0.80` require the
local Florence-2 Agent 5B verifier. Frontage ownership,
ray-intersection, and estimated-size filters do not reject detections. The model
is loaded once and reused for all enhanced images in the job. Demo boxes are available only
from the standalone debug CLI and are never selected by the application
pipeline.

## Enhancement modes

| Mode | Behaviour |
|---|---|
| `ai` (default) | Require Real-ESRGAN through Spandrel and PyTorch |
| `auto` | Use Real-ESRGAN when weights exist; otherwise use fast enhancement |
| `enhance` | Lanczos upscale, autocontrast, contrast, sharpness, and unsharp mask |

The Real-ESRGAN model is constructed once per pipeline job and reused for all
images. Enhancement uses ten workers by default. In AI mode, image loading,
preparation, and saving run concurrently while GPU inference is serialized
through the shared model to avoid multiplying VRAM use. Place the weights at:

```text
models/RealESRGAN_x4plus.pth
```

Real-ESRGAN natively produces 4× output. A different requested scale is resized
to the exact target dimensions after inference.

CUDA inference uses FP16 by default when the loaded Spandrel model supports it.
Set `MAILBOX_ENHANCEMENT_FP16=0` to force the slower FP32 path for debugging.

## Files

| File | Purpose |
|---|---|
| `image_enhancement.py` | Fast enhancement and reusable Real-ESRGAN processor |
| `agent4_assign.py` | Enhance images and calculate geometry columns |
| `agent4_detect.py` | Legacy detector implementation reused by Agent 5 |
| `agent4_run.py` | Legacy standalone enhancement/detection command |
| `test_agent4.py` | Unit tests |

## Example

```powershell
python agent4_run.py `
  --images-json ..\output\agent3_images.json `
  --site-json ..\output\agent2_site.json `
  --out-dir ..\output `
  --enhancement-mode ai `
  --enhancement-scale 2 `
  --enhancement-device auto `
  --enhancement-workers 10 `
  --demo-boxes `
  --skip-plausibility
```

Enhanced images are written to `output/enhanced/<site_id>/` and referenced by
`enhanced_file_path` in `agent4_assign.json`.
