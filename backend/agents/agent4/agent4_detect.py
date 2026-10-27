#!/usr/bin/env python3
"""
Agent 4b — Detect every mailbox from the packaged RF-DETR model
================================================================
Runs AFTER Agent 4a Assign.

For each image:

  1. Run the single-class mailbox detector (or load --detections-json).
     Always use the enhanced image produced by Agent 4a.
  2. Keep every model box above the configured confidence threshold.
  3. Fuse views and produce vision_count + confidence without rejection.

No detection is rejected by frontage ownership, ray intersection, or estimated
physical size.

All code for this step lives in this folder so you can debug Detect alone.

Usage
-----
    # Geometry path with synthetic / external boxes
    python agent4_detect.py \\
        --assign-json ../output/agent4_assign.json \\
        --out-dir ../output \\
        --detections-json path/to/boxes.json

    # Demo mode: invent boxes from assign column ranges (no ML model)
    python agent4_detect.py \\
        --assign-json ../output/agent4_assign.json \\
        --out-dir ../output \\
        --demo-boxes
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from .agent4_assign import (
        bearing_deg,
        classify_s,
        col_to_bearing,
        frontage_length,
        ray_hit_frontage,
    )
except ImportError:
    from agent4_assign import (
        bearing_deg,
        classify_s,
        col_to_bearing,
        frontage_length,
        ray_hit_frontage,
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agent4_detect")

MAILBOX_BUNDLE_ROOT = (
    Path(__file__).resolve().parents[3] / "rfdetr_large_mailbox_bundle"
)
MAILBOX_BUNDLE_MANIFEST = MAILBOX_BUNDLE_ROOT / "model_manifest.json"
DEFAULT_RFDETR_LARGE_CHECKPOINT = (
    MAILBOX_BUNDLE_ROOT / "weights" / "last_ema.pth"
)
# Agent 5A candidate policy: only mailbox detections strictly above 70%.
# Callers may request a higher threshold, but never a lower one.
DEFAULT_SCORE_THRESHOLD = 0.70


# ─────────────────────────────────────────────
#  Data types
# ─────────────────────────────────────────────

@dataclass
class Box:
    x1: float
    y1: float
    x2: float
    y2: float
    score: float = 1.0
    label: str = "mailbox"

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0

    @property
    def width_px(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height_px(self) -> float:
        return max(0.0, self.y2 - self.y1)


def accepted_mailbox(box: Box, threshold: float = DEFAULT_SCORE_THRESHOLD) -> bool:
    """Return True only for mailbox-class boxes strictly above the threshold."""
    effective_threshold = max(float(threshold), DEFAULT_SCORE_THRESHOLD)
    return str(box.label).strip().casefold() == "mailbox" and box.score > effective_threshold


def plausible(
    box: Box,
    t: float,
    f: float,
    H: int,
    pitch_deg: float,
    cam_h: float = 2.5,
    wr: Tuple[float, float] = (0.1, 0.6),
    hr: Tuple[float, float] = (0.5, 2.0),
) -> Tuple[bool, float, float]:
    width_m = box.width_px * t / f
    elev = math.radians(pitch_deg) + math.atan((H / 2.0 - box.cy) / f)
    height_m = cam_h + t * math.tan(elev)
    ok = wr[0] <= width_m <= wr[1] and hr[0] <= height_m <= hr[1]
    return ok, width_m, height_m


def build_banks(
    points: Sequence[Tuple[float, float]], max_gap: float = 1.5
) -> List[List[Tuple[float, float]]]:
    banks: List[List[Tuple[float, float]]] = []
    for p in points:
        for bank in banks:
            if any(math.dist(p, q) <= max_gap for q in bank):
                bank.append(p)
                break
        else:
            banks.append([p])
    return banks


def focal_px(W: int, fov: float) -> float:
    return (W / 2.0) / math.tan(math.radians(fov / 2.0))


# ─────────────────────────────────────────────
#  Detector backends
# ─────────────────────────────────────────────

class RFDetrMailboxBundleDetector:
    """Lazy, reusable detector backed by rfdetr_large_mailbox_bundle."""

    def __init__(self) -> None:
        # Production is deliberately locked to the user's portable bundle.
        self.checkpoint = DEFAULT_RFDETR_LARGE_CHECKPOINT.resolve()
        self._model: Any = None
        self.class_names: List[str] = []

    def _load(self) -> None:
        if self._model is not None:
            return
        if not self.checkpoint.is_file():
            raise FileNotFoundError(
                f"RF-DETR Large checkpoint not found: {self.checkpoint}"
            )
        if not MAILBOX_BUNDLE_MANIFEST.is_file():
            raise FileNotFoundError(
                f"RF-DETR mailbox manifest not found: {MAILBOX_BUNDLE_MANIFEST}"
            )
        manifest = json.loads(MAILBOX_BUNDLE_MANIFEST.read_text(encoding="utf-8"))
        if manifest.get("model") != "RFDETRLarge" or manifest.get("classes") != ["mailbox"]:
            raise RuntimeError("Invalid RF-DETR mailbox bundle manifest")
        try:
            from rfdetr import RFDETRLarge
        except ImportError as exc:
            raise RuntimeError(
                "RF-DETR is required for mailbox bundle detection. "
                "Install backend requirements and restart the application."
            ) from exc

        logger.info("Loading RF-DETR mailbox bundle: %s", self.checkpoint)
        # Match rfdetr_large_mailbox_bundle/predict.py exactly.
        self._model = RFDETRLarge(pretrain_weights=str(self.checkpoint))
        self.class_names = list(
            getattr(self._model.model, "class_names", None) or ["mailbox"]
        )
        logger.info("RF-DETR Large ready (classes=%s)", self.class_names)

    def detect(self, image_path: Path, threshold: float) -> List[Box]:
        """Run inference and normalize RF-DETR output to Agent 4 ``Box`` objects."""
        self._load()
        threshold = max(float(threshold), DEFAULT_SCORE_THRESHOLD)
        if not image_path.is_file():
            raise FileNotFoundError(f"Detection image not found: {image_path}")

        import cv2

        image = cv2.imread(str(image_path))
        if image is None:
            raise RuntimeError(f"RF-DETR could not read image: {image_path}")
        detections = self._model.predict(image, threshold=threshold)

        xyxy = getattr(detections, "xyxy", None)
        if xyxy is None:
            return []
        confidences = getattr(detections, "confidence", None)
        class_ids = getattr(detections, "class_id", None)
        boxes: List[Box] = []
        for index, coords in enumerate(xyxy):
            class_id = int(class_ids[index]) if class_ids is not None else 0
            label = (
                self.class_names[class_id]
                if 0 <= class_id < len(self.class_names)
                else f"class_{class_id}"
            )
            score = float(confidences[index]) if confidences is not None else 0.0
            box = Box(
                float(coords[0]),
                float(coords[1]),
                float(coords[2]),
                float(coords[3]),
                score,
                label,
            )
            if accepted_mailbox(box, threshold):
                box.label = "mailbox"
                boxes.append(box)
        logger.info(
            "RF-DETR detected %d mailbox(es) in %s at threshold %.2f",
            len(boxes),
            image_path.name,
            threshold,
        )
        return boxes

    def close(self) -> None:
        """Release RF-DETR before Agent 5B loads Florence-2 on the GPU."""
        model = self._model
        self._model = None
        if model is not None:
            del model
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


def load_detections_json(path: Path) -> Dict[str, List[Box]]:
    """
    Expected format:
      {
        "<site_id>|<pano_id>": [
          {"x1":..,"y1":..,"x2":..,"y2":..,"score":0.9}, ...
        ],
        ...
      }
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out: Dict[str, List[Box]] = {}
    for key, boxes in raw.items():
        out[key] = [
            Box(
                float(b["x1"]), float(b["y1"]), float(b["x2"]), float(b["y2"]),
                float(b.get("score", 0.0)),
                str(b.get("label", "mailbox")),
            )
            for b in boxes
        ]
    return out


def demo_boxes_for_image(image_assign: Dict[str, Any]) -> List[Box]:
    """
    Synthetic boxes for debugging without a trained model:
      - 1 TARGET box near the middle of the frontage column range
      - 1 NEIGHBOUR box left of the buffer (should be rejected)
    """
    W = int(image_assign.get("image_width") or 640)
    H = int(image_assign.get("image_height") or 640)
    lo = float(image_assign.get("col_target_lo", W * 0.35))
    hi = float(image_assign.get("col_target_hi", W * 0.65))
    mid = (lo + hi) / 2.0
    bw, bh = 28.0, 40.0
    y = H * 0.55
    target = Box(mid - bw / 2, y - bh / 2, mid + bw / 2, y + bh / 2, 0.92)
    neighbour_cx = max(20.0, float(image_assign.get("col_buffer_lo", lo)) - 80.0)
    neighbour = Box(
        neighbour_cx - bw / 2, y - bh / 2, neighbour_cx + bw / 2, y + bh / 2, 0.88
    )
    return [neighbour, target]


def detect_boxes(
    image_assign: Dict[str, Any],
    site_id: str,
    *,
    detections_map: Optional[Dict[str, List[Box]]],
    demo_boxes: bool,
    score_threshold: float,
    detector: Optional[RFDetrMailboxBundleDetector] = None,
) -> List[Box]:
    score_threshold = max(float(score_threshold), DEFAULT_SCORE_THRESHOLD)
    key = f"{site_id}|{image_assign.get('pano_id')}"
    if detections_map is not None and key in detections_map:
        boxes = [b for b in detections_map[key] if accepted_mailbox(b, score_threshold)]
        return boxes

    if demo_boxes:
        return demo_boxes_for_image(image_assign)

    if detector is not None:
        raw_path = image_assign.get("enhanced_file_path") or image_assign.get("file_path")
        if not raw_path:
            logger.warning("No image path available for %s", key)
            return []
        return [
            box
            for box in detector.detect(Path(raw_path), score_threshold)
            if accepted_mailbox(box, score_threshold)
        ]

    # No detector wired — return empty (status will show DETECTOR_MISSING)
    logger.warning(
        "No detections for %s (provide --detections-json or --demo-boxes)", key
    )
    return []


# ─────────────────────────────────────────────
#  Per-detection assignment
# ─────────────────────────────────────────────

def ground_point_from_s(
    left: Sequence[float], right: Sequence[float], s: float
) -> Tuple[float, float]:
    L = frontage_length(left, right)
    if L <= 0:
        return float(left[0]), float(left[1])
    t = s / L
    return left[0] + (right[0] - left[0]) * t, left[1] + (right[1] - left[1]) * t


def save_detected_image(
    source_path: Path,
    detected_dir: Path,
    detections: Sequence[Dict[str, Any]],
) -> Path:
    """Save an annotated Agent 4 view under ``output/detected/<site>``."""
    from PIL import Image, ImageDraw

    colors = {
        "TARGET": (34, 197, 94),
        "AMBIGUOUS": (245, 158, 11),
        "NEIGHBOUR": (239, 68, 68),
        "REJECTED": (148, 163, 184),
    }
    detected_dir.mkdir(parents=True, exist_ok=True)
    output_path = detected_dir / f"{source_path.stem}_detected.jpg"
    with Image.open(source_path) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    for detection in detections:
        box = detection.get("box") or {}
        try:
            coords = tuple(
                float(box[key]) for key in ("x1", "y1", "x2", "y2")
            )
        except (KeyError, TypeError, ValueError):
            continue
        ownership = str(detection.get("ownership") or "REJECTED")
        color = colors.get(ownership, colors["REJECTED"])
        score = float(detection.get("score") or 0.0)
        label = f"{ownership} {score:.2f}"
        draw.rectangle(coords, outline=color, width=4)
        text_x = max(0.0, coords[0])
        text_y = max(0.0, coords[1] - 16.0)
        text_box = draw.textbbox((text_x, text_y), label)
        draw.rectangle(text_box, fill=color)
        draw.text((text_x, text_y), label, fill=(255, 255, 255))
    if not detections:
        draw.rectangle((8, 8, 176, 30), fill=(30, 41, 59))
        draw.text((13, 12), "No mailbox detected", fill=(255, 255, 255))
    image.save(output_path, format="JPEG", quality=92)
    return output_path.resolve()


def process_image_detections(
    image_assign: Dict[str, Any],
    site_id: str,
    *,
    detections_map: Optional[Dict[str, List[Box]]],
    demo_boxes: bool,
    score_threshold: float,
    buffer_m: float,
    cam_h: float,
    skip_plausibility: bool,
    detector: Optional[RFDetrMailboxBundleDetector] = None,
    detected_dir: Optional[Path] = None,
    detected_files: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    boxes = detect_boxes(
        image_assign,
        site_id,
        detections_map=detections_map,
        demo_boxes=demo_boxes,
        score_threshold=score_threshold,
        detector=detector,
    )

    accepted: List[Dict[str, Any]] = []
    image_file = image_assign.get("enhanced_file_path") or image_assign.get("file_path")
    for i, box in enumerate(boxes):
        accepted.append(
            {
                "det_id": f"{image_assign.get('pano_id')}_{i}",
                "pano_id": image_assign.get("pano_id"),
                "box": asdict(box),
                # TARGET now means accepted model output. It is retained only
                # for compatibility with Agent 5 and exported result schemas.
                "ownership": "TARGET",
                "score": box.score,
                "image_file": image_file,
            }
        )
    if detected_dir is not None:
        raw_source = image_assign.get("enhanced_file_path") or image_assign.get("file_path")
        source_path = Path(raw_source) if raw_source else None
        if source_path is not None and source_path.is_file():
            detected_path = save_detected_image(
                source_path, detected_dir, accepted
            )
            detected_path_text = str(detected_path)
            for detection in accepted:
                detection["detected_file_path"] = detected_path_text
            if detected_files is not None:
                detected_files.append(detected_path_text)
        else:
            logger.warning("Cannot save detected image; source missing: %s", raw_source)
    return accepted


def fuse_site_detections(
    dets: List[Dict[str, Any]],
    *,
    bank_gap_m: float = 1.5,
) -> Dict[str, Any]:
    """Keep and count every detector output without geometry rejection."""
    # Every RF-DETR box is a mailbox result. Geometry ownership is no longer
    # allowed to discard or downgrade a model detection.
    target = [{**d, "ownership": "TARGET"} for d in dets]

    points = [
        (d["ground_xy"][0], d["ground_xy"][1])
        for d in target
        if d.get("ground_xy")
    ]
    banks = build_banks(points, max_gap=bank_gap_m)
    vision_count = len(target)

    panos_target = {d.get("pano_id") for d in target}
    if vision_count == 0:
        confidence = "Low"
        status = "OK"
    elif len(panos_target) >= 2:
        confidence = "High"
        status = "OK"
    else:
        confidence = "Medium"
        status = "OK"

    return {
        "vision_count": vision_count,
        "confidence": confidence,
        "status": status,
        "flags": [],
        "bank_count": len(banks),
        "banks": [
            {"size": len(b), "centroid_xy": [
                round(sum(p[0] for p in b) / len(b), 3),
                round(sum(p[1] for p in b) / len(b), 3),
            ]}
            for b in banks
        ],
        "mailboxes": target,
        "ambiguous": [],
        "neighbours_rejected": [],
        "all_detections": target,
    }


def process_site(
    site_assign: Dict[str, Any],
    *,
    detections_map: Optional[Dict[str, List[Box]]],
    demo_boxes: bool,
    score_threshold: float,
    buffer_m: float,
    cam_h: float,
    bank_gap_m: float,
    skip_plausibility: bool,
    detector: Optional[RFDetrMailboxBundleDetector] = None,
    detected_root: Optional[Path] = None,
) -> Dict[str, Any]:
    site_id = site_assign["site_id"]
    out: Dict[str, Any] = {
        "site_id": site_id,
        "postcode": site_assign.get("postcode"),
        "street_norm": site_assign.get("street_norm"),
        "hnr_base": site_assign.get("hnr_base"),
        "x": site_assign.get("x"),
        "y": site_assign.get("y"),
        "parcel_id": site_assign.get("parcel_id"),
        "flags": list(site_assign.get("flags") or []),
        "status": site_assign.get("status", "OK"),
        "vision_count": None,
        "confidence": None,
        "mailboxes": [],
        "detected_images": [],
    }

    if site_assign.get("status") == "NO_STREETVIEW":
        out["status"] = "NO_STREETVIEW"
        out["flags"] = sorted(set(out["flags"] + ["NO_STREETVIEW", "NO_IMAGERY"]))
        out["vision_count"] = None
        return out

    all_dets: List[Dict[str, Any]] = []
    detected_files: List[str] = []
    for img in site_assign.get("images") or []:
        all_dets.extend(
            process_image_detections(
                img,
                site_id,
                detections_map=detections_map,
                demo_boxes=demo_boxes,
                score_threshold=score_threshold,
                buffer_m=buffer_m,
                cam_h=cam_h,
                skip_plausibility=skip_plausibility,
                detector=detector,
                detected_dir=(detected_root / site_id) if detected_root else None,
                detected_files=detected_files,
            )
        )
    out["detected_images"] = detected_files

    if not demo_boxes and detections_map is None and detector is None and not all_dets:
        out["status"] = "DETECTOR_MISSING"
        out["flags"] = sorted(set(out["flags"] + ["DETECTOR_MISSING"]))
        out["vision_count"] = None
        return out

    fused = fuse_site_detections(all_dets, bank_gap_m=bank_gap_m)
    out.update(fused)
    out["flags"] = sorted(set(out["flags"] + fused.get("flags", [])))
    return out


def run(
    assign_json: Path,
    out_dir: Path,
    *,
    detections_json: Optional[Path] = None,
    demo_boxes: bool = False,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    buffer_m: float = 1.0,
    cam_h: float = 2.5,
    bank_gap_m: float = 1.5,
    skip_plausibility: bool = False,
    max_sites: Optional[int] = None,
    output_filename: str = "agent4_detect.json",
) -> List[Dict[str, Any]]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    detected_root = out_dir / "detected"
    assign_data = json.loads(Path(assign_json).read_text(encoding="utf-8"))
    if max_sites is not None:
        assign_data = assign_data[:max_sites]

    detections_map = (
        load_detections_json(detections_json) if detections_json else None
    )
    detector = (
        RFDetrMailboxBundleDetector()
        if not demo_boxes and detections_map is None
        else None
    )

    results: List[Dict[str, Any]] = []
    total = len(assign_data)
    for i, site in enumerate(assign_data):
        res = process_site(
            site,
            detections_map=detections_map,
            demo_boxes=demo_boxes,
            score_threshold=score_threshold,
            buffer_m=buffer_m,
            cam_h=cam_h,
            bank_gap_m=bank_gap_m,
            skip_plausibility=skip_plausibility,
            detector=detector,
            detected_root=detected_root,
        )
        results.append(res)
        print(
            f"  [{i+1:>5}/{total}] {res['site_id']}  status={res['status']:16}  "
            f"vision_count={res.get('vision_count')}  conf={res.get('confidence')}"
        )

    out_path = out_dir / output_filename
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    ok = sum(1 for r in results if r["status"] == "OK")
    print("\n" + "=" * 60)
    print("  Agent 4b Detect complete")
    print(f"  Sites processed   : {total}")
    print(f"  OK                : {ok}")
    print(f"  Detect JSON       : {out_path}")
    print(f"  Detected images   : {detected_root}")
    if detector is not None:
        print("  Detector          : RF-DETR Large mailbox bundle")
        print(f"  Checkpoint        : {detector.checkpoint}")
    print("=" * 60 + "\n")
    return results


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Agent 4b - Keep every mailbox returned by the RF-DETR bundle.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--assign-json", required=True, help="agent4_assign.json")
    p.add_argument("--out-dir", "-o", default=".")
    p.add_argument(
        "--detections-json", default=None,
        help="External detector boxes keyed by site_id|pano_id.",
    )
    p.add_argument(
        "--demo-boxes", action="store_true",
        help="Invent TARGET+NEIGHBOUR boxes from assign column ranges (debug).",
    )
    p.add_argument("--score-threshold", type=float, default=DEFAULT_SCORE_THRESHOLD)
    p.add_argument("--buffer-m", type=float, default=1.0)
    p.add_argument("--cam-h", type=float, default=2.5)
    p.add_argument("--bank-gap-m", type=float, default=1.5)
    p.add_argument(
        "--skip-plausibility", action="store_true",
        help="Skip width/height plausibility (useful with demo boxes).",
    )
    p.add_argument("--max-sites", type=int, default=None)
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    run(
        assign_json=Path(args.assign_json).resolve(),
        out_dir=Path(args.out_dir).resolve(),
        detections_json=Path(args.detections_json).resolve() if args.detections_json else None,
        demo_boxes=args.demo_boxes,
        score_threshold=args.score_threshold,
        buffer_m=args.buffer_m,
        cam_h=args.cam_h,
        bank_gap_m=args.bank_gap_m,
        skip_plausibility=args.skip_plausibility,
        max_sites=args.max_sites,
    )


if __name__ == "__main__":
    main()
