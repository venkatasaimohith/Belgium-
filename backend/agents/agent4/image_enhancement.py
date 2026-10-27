"""Reusable Real-ESRGAN image enhancement for Agent 4 preprocessing.

The Spandrel/PyTorch model is loaded once per job and reused for every image.
Real-ESRGAN produces native 4x output; other requested scales are resized to
the exact target dimensions after inference.
"""

from __future__ import annotations

import argparse
import logging
import os
import threading
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


ROOT = Path(__file__).resolve().parent
DEFAULT_WEIGHTS = ROOT / "models" / "RealESRGAN_x4plus.pth"
CropBox = Tuple[float, float, float, float]
logger = logging.getLogger(__name__)


def parse_crop(value: Optional[str]) -> Optional[CropBox]:
    if value is None:
        return None
    try:
        parts = tuple(float(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("crop values must be numbers") from exc
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("crop must be x_min,y_min,x_max,y_max")
    x0, y0, x1, y1 = parts
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise argparse.ArgumentTypeError(
            "crop coordinates must satisfy 0 <= min < max <= 1"
        )
    return x0, y0, x1, y1


def crop_norm(image: Image.Image, box: CropBox) -> Image.Image:
    x0, y0, x1, y1 = box
    return image.crop(
        (
            round(x0 * image.width),
            round(y0 * image.height),
            round(x1 * image.width),
            round(y1 * image.height),
        )
    )


def enhance_upscale(image: Image.Image, scale: float) -> Image.Image:
    size = (
        max(1, round(image.width * scale)),
        max(1, round(image.height * scale)),
    )
    result = image.resize(size, Image.Resampling.LANCZOS)
    result = ImageOps.autocontrast(result, cutoff=0.5)
    result = ImageEnhance.Contrast(result).enhance(1.2)
    result = ImageEnhance.Sharpness(result).enhance(1.8)
    return result.filter(
        ImageFilter.UnsharpMask(radius=1.8, percent=160, threshold=2)
    )


class RealESRGAN:
    """One reusable Spandrel model for an entire pipeline job."""

    def __init__(self, weights: Path, device: str):
        try:
            import torch
            from spandrel import ImageModelDescriptor, ModelLoader
        except ImportError as exc:
            raise RuntimeError(
                "AI enhancement requires PyTorch, torchvision, and Spandrel"
            ) from exc
        weights = Path(weights)
        if not weights.is_file():
            raise FileNotFoundError(
                f"Real-ESRGAN weights not found: {weights}. "
                f"Place RealESRGAN_x4plus.pth in {weights.parent}."
            )

        self.torch = torch
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError(
                "GPU enhancement was requested but CUDA is unavailable. "
                "Install backend/requirements-cuda.txt in the project .venv "
                "and restart the backend."
            )
        self.device = torch.device(device)
        descriptor = ModelLoader().load_from_file(str(weights))
        if not isinstance(descriptor, ImageModelDescriptor):
            raise RuntimeError(
                f"Unsupported Spandrel model descriptor: {type(descriptor).__name__}"
            )
        self.model = descriptor.to(self.device).eval()
        half_enabled = os.environ.get("MAILBOX_ENHANCEMENT_FP16", "1").strip().lower()
        self.use_half = (
            self.device.type == "cuda"
            and half_enabled not in {"0", "false", "no", "off"}
            and bool(getattr(descriptor, "supports_half", True))
        )
        if self.use_half:
            self.model = self.model.half()
        self.input_dtype = torch.float16 if self.use_half else torch.float32
        # One model instance is shared by all Agent 4 workers.  Serialising the
        # accelerator section prevents concurrent workers from multiplying
        # Real-ESRGAN's already-large VRAM allocation.  Image decode, tensor
        # preparation and output encoding still overlap between workers.
        self._inference_lock = threading.Lock()
        if self.device.type == "cuda":
            torch.backends.cudnn.benchmark = True
            logger.info(
                "Real-ESRGAN using GPU: %s (PyTorch %s, CUDA %s, precision=%s)",
                torch.cuda.get_device_name(self.device), torch.__version__,
                torch.version.cuda, "FP16" if self.use_half else "FP32",
            )
        else:
            logger.warning(
                "Real-ESRGAN is running on CPU; 4x AI enhancement may take minutes per image."
            )

    def __call__(self, image: Image.Image, scale: float) -> Image.Image:
        import numpy as np

        array = np.asarray(image.convert("RGB"), dtype="float32") / 255.0
        tensor = (
            self.torch.from_numpy(array)
            .permute(2, 0, 1)
            .unsqueeze(0)
        )
        with self._inference_lock:
            tensor = tensor.to(self.device, dtype=self.input_dtype)
            with self.torch.inference_mode():
                output = self.model(tensor)
            output = (
                output.squeeze(0).clamp(0, 1).permute(1, 2, 0).float().cpu().numpy()
            )
        result = Image.fromarray(
            (output * 255.0 + 0.5).astype("uint8"), "RGB"
        )
        target = (
            max(1, round(image.width * scale)),
            max(1, round(image.height * scale)),
        )
        if result.size != target:
            result = result.resize(target, Image.Resampling.LANCZOS)
        return result


def save_image(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        image.save(path, quality=95, subsampling=0)
    else:
        image.save(path)


class ImageEnhancer:
    """Job-scoped processor that loads the optional AI model exactly once."""

    def __init__(
        self,
        *,
        mode: str = "ai",
        scale: float = 2.0,
        weights: Path = DEFAULT_WEIGHTS,
        device: str = "auto",
        crop: Optional[CropBox] = None,
    ) -> None:
        if mode not in {"auto", "enhance", "ai"}:
            raise ValueError("mode must be auto, enhance, or ai")
        if scale <= 0:
            raise ValueError("scale must be positive")
        self.scale = float(scale)
        self.crop = crop
        self.weights = Path(weights)
        self.requested_mode = mode
        self.mode = "ai" if mode == "auto" and self.weights.is_file() else mode
        if self.mode == "auto":
            self.mode = "enhance"
        self.processor = (
            RealESRGAN(self.weights, device) if self.mode == "ai" else None
        )
        self.device = str(self.processor.device) if self.processor is not None else "cpu"

    def process(self, source: Path, destination: Path) -> Tuple[int, int]:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
        if self.crop:
            image = crop_norm(image, self.crop)
        result = (
            self.processor(image, self.scale)
            if self.processor is not None
            else enhance_upscale(image, self.scale)
        )
        save_image(result, destination)
        return result.size
