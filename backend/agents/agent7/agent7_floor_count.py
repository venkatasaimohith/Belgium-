"""Agent 7 — estimate above-ground floor count from the target facade.

This agent consumes prior outputs without modifying them. It crops the target
house from the enhanced FOV-90 image, detects windows/doors/dormers with a
locally cached Grounding DINO model, groups windows into horizontal rows, and
marks weak or obstructed estimates for manual review.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from pathlib import Path
from statistics import median
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw


AGENT6_DIR = Path(__file__).resolve().parents[1] / "agent6"
if str(AGENT6_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT6_DIR))

from agent6_reject import detect_parcel_guided_boundaries  # noqa: E402


logger = logging.getLogger("agent7_floor_count")
MODEL_ID = "IDEA-Research/grounding-dino-base"
CANDIDATE_LABELS = ["window", "front door", "garage door", "dormer window"]

_DETECTOR: Any = None
_DETECTOR_ERROR: Optional[str] = None


def _load_detector() -> Any:
    global _DETECTOR, _DETECTOR_ERROR
    if _DETECTOR is not None:
        return _DETECTOR
    if _DETECTOR_ERROR:
        return None
    try:
        import torch
        from transformers import pipeline

        allow_download = os.environ.get("MAILBOX_AGENT7_ALLOW_MODEL_DOWNLOAD", "0") == "1"
        _DETECTOR = pipeline(
            "zero-shot-object-detection",
            model=MODEL_ID,
            device=0 if torch.cuda.is_available() else -1,
            local_files_only=not allow_download,
        )
        return _DETECTOR
    except Exception as exc:
        _DETECTOR_ERROR = str(exc)
        logger.warning("Agent 7 floor detector unavailable: %s", exc)
        return None


def _label_kind(label: Any) -> str:
    text = str(label or "").lower()
    if "dormer" in text:
        return "dormer"
    if "garage" in text:
        return "garage"
    if "door" in text or "entrance" in text:
        return "door"
    return "window"


def _box_values(prediction: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    box = prediction.get("box") or {}
    try:
        return (
            float(box.get("xmin") if box.get("xmin") is not None else box.get("x1")),
            float(box.get("ymin") if box.get("ymin") is not None else box.get("y1")),
            float(box.get("xmax") if box.get("xmax") is not None else box.get("x2")),
            float(box.get("ymax") if box.get("ymax") is not None else box.get("y2")),
        )
    except (TypeError, ValueError):
        return None


def _iou(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ax1, ay1, ax2, ay2 = a["box"]
    bx1, by1, bx2, by2 = b["box"]
    intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0.0, min(ay2, by2) - max(ay1, by1)
    )
    if intersection <= 0.0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    return intersection / max(area_a + area_b - intersection, 1e-6)


def _detect_architecture(crop: Image.Image) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    detector = _load_detector()
    if detector is None:
        return [], _DETECTOR_ERROR or "Grounding DINO is unavailable"
    try:
        predictions = detector(crop, candidate_labels=CANDIDATE_LABELS, threshold=0.24)
    except Exception as exc:
        return [], str(exc)
    objects: List[Dict[str, Any]] = []
    crop_area = float(max(1, crop.width * crop.height))
    for prediction in predictions:
        coords = _box_values(prediction)
        if coords is None:
            continue
        x1, y1, x2, y2 = coords
        width, height = x2 - x1, y2 - y1
        area_ratio = width * height / crop_area
        if width <= 2.0 or height <= 2.0 or area_ratio < 0.00025 or area_ratio > 0.35:
            continue
        kind = _label_kind(prediction.get("label"))
        if kind in ("window", "dormer") and not 0.20 <= width / max(height, 1.0) <= 5.0:
            continue
        objects.append(
            {
                "kind": kind,
                "score": float(prediction.get("score") or 0.0),
                "box": [x1, y1, x2, y2],
                "cx": (x1 + x2) / 2.0,
                "cy": (y1 + y2) / 2.0,
                "height": height,
            }
        )
    kept: List[Dict[str, Any]] = []
    for item in sorted(objects, key=lambda value: value["score"], reverse=True):
        if any(item["kind"] == other["kind"] and _iou(item, other) >= 0.55 for other in kept):
            continue
        kept.append(item)
    return kept, None


def estimate_floor_rows(
    objects: Sequence[Dict[str, Any]], crop_height: float
) -> Dict[str, Any]:
    """Group window/dormer centres into storey rows and include door-only ground floors."""
    windows = [item for item in objects if item.get("kind") in ("window", "dormer")]
    doors = [item for item in objects if item.get("kind") in ("door", "garage")]
    if not windows:
        return {
            "floor_count": 1 if doors else None,
            "rows": [],
            "attic_detected": False,
            "reason": "door_only_ground_floor" if doors else "no_windows_or_doors_detected",
        }
    heights = [max(1.0, float(item.get("height") or 1.0)) for item in windows]
    tolerance = max(float(crop_height) * 0.055, median(heights) * 0.70)
    rows: List[List[Dict[str, Any]]] = []
    for item in sorted(windows, key=lambda value: float(value["cy"])):
        if not rows:
            rows.append([item])
            continue
        row_center = sum(float(value["cy"]) for value in rows[-1]) / len(rows[-1])
        if abs(float(item["cy"]) - row_center) <= tolerance:
            rows[-1].append(item)
        else:
            rows.append([item])
    count = len(rows)
    lowest_window_y = sum(float(value["cy"]) for value in rows[-1]) / len(rows[-1])
    door_only_ground = bool(doors) and max(float(item["box"][3]) for item in doors) > (
        lowest_window_y + float(crop_height) * 0.12
    )
    if door_only_ground:
        count += 1
    return {
        "floor_count": min(10, max(1, count)),
        "rows": [
            {
                "center_y": round(sum(float(item["cy"]) for item in row) / len(row), 2),
                "detections": len(row),
                "kinds": sorted({str(item["kind"]) for item in row}),
            }
            for row in rows
        ],
        "attic_detected": any(item.get("kind") == "dormer" for item in windows),
        "door_only_ground_added": door_only_ground,
        "reason": "window_rows",
    }


def _choose_image(site_assign: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    images = [image for image in site_assign.get("images") or [] if image.get("status") == "OK"]
    if not images:
        return None
    return min(images, key=lambda image: (abs(float(image.get("fov") or 999.0) - 90.0), float(image.get("fov") or 999.0)))


def _target_boundaries(
    rejected_site: Dict[str, Any], parcel_site: Dict[str, Any], image: Dict[str, Any]
) -> Dict[str, Any]:
    detection_fov = rejected_site.get("detection_fov")
    if detection_fov is not None and math.isclose(float(detection_fov), float(image.get("fov") or 0.0), abs_tol=0.1):
        records = rejected_site.get("ownership_geometry") or []
        if len(records) == 1 and (records[0].get("boundaries") or {}).get("house_bbox"):
            return records[0]["boundaries"]
    return detect_parcel_guided_boundaries(parcel_site, image)


def _crop_box(boundaries: Dict[str, Any], image: Image.Image) -> Optional[Tuple[int, int, int, int]]:
    box = boundaries.get("house_bbox") or {}
    try:
        x1, y1, x2, y2 = (float(box[key]) for key in ("x1", "y1", "x2", "y2"))
    except (KeyError, TypeError, ValueError):
        return None
    pad_x = max(4.0, (x2 - x1) * 0.06)
    pad_y = max(4.0, (y2 - y1) * 0.04)
    result = (
        max(0, int(math.floor(x1 - pad_x))),
        max(0, int(math.floor(y1 - pad_y))),
        min(image.width, int(math.ceil(x2 + pad_x))),
        min(image.height, int(math.ceil(y2 + pad_y))),
    )
    return result if result[2] - result[0] >= 24 and result[3] - result[1] >= 24 else None


def _save_review(
    crop: Image.Image,
    objects: Sequence[Dict[str, Any]],
    rows: Sequence[Dict[str, Any]],
    output_path: Path,
) -> str:
    canvas = crop.copy().convert("RGB")
    draw = ImageDraw.Draw(canvas)
    colors = {"window": "#22c55e", "dormer": "#06b6d4", "door": "#a855f7", "garage": "#f59e0b"}
    for item in objects:
        draw.rectangle(tuple(item["box"]), outline=colors.get(str(item["kind"]), "white"), width=3)
        draw.text((item["box"][0], max(0, item["box"][1] - 12)), str(item["kind"]), fill=colors.get(str(item["kind"]), "white"))
    for row in rows:
        y = float(row["center_y"])
        draw.line((0, y, canvas.width, y), fill="#ef4444", width=2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="JPEG", quality=92)
    return str(output_path.resolve())


def process_site(
    rejected_site: Dict[str, Any],
    assign_site: Dict[str, Any],
    parcel_site: Dict[str, Any],
    out_dir: Path,
) -> Dict[str, Any]:
    site_id = str(rejected_site.get("site_id") or assign_site.get("site_id") or parcel_site.get("site_id"))
    result: Dict[str, Any] = {
        "site_id": site_id,
        "floor_count": None,
        "confidence": "Low",
        "manual_review": True,
        "status": "MANUAL_REVIEW",
        "source_fov": None,
        "objects": [],
        "rows": [],
    }
    image_record = _choose_image(assign_site)
    if image_record is None:
        result["reason"] = "missing_enhanced_image"
        return result
    source = Path(str(image_record.get("enhanced_file_path") or image_record.get("file_path") or ""))
    if not source.is_file():
        result["reason"] = "missing_enhanced_image_file"
        return result
    result["source_fov"] = float(image_record.get("fov") or 0.0)
    boundaries = _target_boundaries(rejected_site, parcel_site, image_record)
    with Image.open(source) as opened:
        picture = opened.convert("RGB")
    crop_box = _crop_box(boundaries, picture)
    if crop_box is None:
        result["reason"] = "target_house_bbox_unavailable"
        return result
    crop = picture.crop(crop_box)
    objects, error = _detect_architecture(crop)
    if error:
        result["reason"] = "floor_model_unavailable"
        result["error"] = error
        return result
    estimate = estimate_floor_rows(objects, crop.height)
    result.update(estimate)
    result["objects"] = objects
    result["target_bbox"] = list(crop_box)
    result["projection_reliable"] = bool(boundaries.get("projection_reliable", False))
    row_sizes = [int(row["detections"]) for row in estimate.get("rows") or []]
    if estimate.get("floor_count") is None:
        confidence = "Low"
    elif result["projection_reliable"] and row_sizes and min(row_sizes) >= 2:
        confidence = "High"
    elif len(objects) >= 2:
        confidence = "Medium"
    else:
        confidence = "Low"
    result["confidence"] = confidence
    result["manual_review"] = confidence == "Low" or not result["projection_reliable"]
    result["status"] = "MANUAL_REVIEW" if result["manual_review"] else "OK"
    review_path = Path(out_dir) / "agent7_floor_review" / site_id / "floor_count.jpg"
    result["review_image"] = _save_review(crop, objects, estimate.get("rows") or [], review_path)
    return result


def run(
    reject_json: Path,
    assign_json: Path,
    site_json: Path,
    out_dir: Path,
    *,
    max_sites: Optional[int] = None,
    cancel_check: Optional[Callable[[], None]] = None,
    progress_callback: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
) -> List[Dict[str, Any]]:
    rejected = json.loads(Path(reject_json).read_text(encoding="utf-8"))
    assigned = json.loads(Path(assign_json).read_text(encoding="utf-8"))
    parcels = json.loads(Path(site_json).read_text(encoding="utf-8"))
    if max_sites is not None:
        rejected = rejected[:max_sites]
    assign_by_id = {str(item["site_id"]): item for item in assigned}
    parcel_by_id = {str(item["site_id"]): item for item in parcels}
    results: List[Dict[str, Any]] = []
    for index, rejected_site in enumerate(rejected, 1):
        if cancel_check:
            cancel_check()
        site_id = str(rejected_site["site_id"])
        result = process_site(
            rejected_site,
            assign_by_id.get(site_id, {"site_id": site_id, "images": []}),
            parcel_by_id.get(site_id, {"site_id": site_id}),
            Path(out_dir),
        )
        results.append(result)
        if progress_callback:
            progress_callback(index, len(rejected), {"site_id": site_id})
        print(
            f"  [{index:>5}/{len(rejected)}] {site_id} floors={result.get('floor_count')} "
            f"confidence={result['confidence']} status={result['status']}"
        )
    output_path = Path(out_dir) / "agent7_floor_count.json"
    output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return results


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Agent 7 - target-facade floor count")
    parser.add_argument("--reject-json", required=True)
    parser.add_argument("--assign-json", required=True)
    parser.add_argument("--site-json", required=True)
    parser.add_argument("--out-dir", "-o", default=".")
    parser.add_argument("--max-sites", type=int)
    args = parser.parse_args(argv)
    run(
        Path(args.reject_json), Path(args.assign_json), Path(args.site_json),
        Path(args.out_dir), max_sites=args.max_sites,
    )


if __name__ == "__main__":
    main()
