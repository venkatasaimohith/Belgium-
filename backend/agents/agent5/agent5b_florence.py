"""Local Florence-2 mailbox verification adapted from the user-supplied code."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageOps

MODEL = "florence-community/Florence-2-large-ft"
TASK = "<CAPTION_TO_PHRASE_GROUNDING>"
PHRASES = ("mailbox", "letter slot")
FLORENCE_ACCEPT_THRESHOLD = 0.80


def regions(width, height, tiles=2, overlap=0.25):
    yield (0, 0, width, height)
    if tiles <= 1:
        return
    crop_w = min(width, math.ceil(width / (tiles - (tiles - 1) * overlap)))
    crop_h = min(height, math.ceil(height / (tiles - (tiles - 1) * overlap)))
    seen = {(0, 0, width, height)}
    for row in range(tiles):
        for col in range(tiles):
            x = round(col * (width - crop_w) / (tiles - 1))
            y = round(row * (height - crop_h) / (tiles - 1))
            box = (x, y, x + crop_w, y + crop_h)
            if box not in seen:
                seen.add(box)
                yield box


def map_box(raw, region):
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        values = [float(value) for value in raw]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    left, top, right, bottom = region
    x1, y1, x2, y2 = values
    if x2 <= x1 or y2 <= y1:
        return None
    box = [
        round(left + max(0, min(right - left, x1))),
        round(top + max(0, min(bottom - top, y1))),
        round(left + max(0, min(right - left, x2))),
        round(top + max(0, min(bottom - top, y2))),
    ]
    return box if box[2] > box[0] and box[3] > box[1] else None


def iou(a, b):
    inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    union = ((a[2] - a[0]) * (a[3] - a[1])
             + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / union if union else 0.0


def merge_candidates(candidates, threshold=0.5):
    groups = []
    for candidate in candidates:
        group = next(
            (item for item in groups if iou(candidate["bbox"], item[0]["bbox"]) >= threshold),
            None,
        )
        if group is None:
            groups.append([candidate])
        else:
            group.append(candidate)
    detections = []
    for group in groups:
        best = max(group, key=lambda item: sum(iou(item["bbox"], other["bbox"]) for other in group))
        sources = [{
            "region": item["region"], "phrase": item["phrase"],
            "model_label": item["model_label"], "bbox": item["bbox"],
        } for item in group]
        detections.append({
            "label": "mailbox", "bbox": best["bbox"], "confidence": None,
            "support": len({(tuple(item["region"]), item["phrase"]) for item in group}),
            "sources": sources,
        })
    detections.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
    return detections


class Florence:
    def __init__(self, model=MODEL, device="auto", cache_dir=None, revision=None,
                 local_only=False, beams=3, max_tokens=512):
        import torch
        from transformers import AutoProcessor, Florence2ForConditionalGeneration
        self.torch = torch
        self.device = "cuda" if device == "auto" and torch.cuda.is_available() else (
            "cpu" if device == "auto" else device
        )
        if self.device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable")
        self.dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.beams, self.max_tokens = beams, max_tokens
        options = {"local_files_only": local_only}
        if cache_dir is not None:
            options["cache_dir"] = str(cache_dir)
        if revision:
            options["revision"] = revision
        self.processor = AutoProcessor.from_pretrained(model, **options)
        self.model = Florence2ForConditionalGeneration.from_pretrained(
            model, dtype=self.dtype, attn_implementation="eager", **options
        ).to(self.device).eval()
        self.revision = getattr(self.model.config, "_commit_hash", None)
        self.model_name = model

    def generate(self, image, task, text_input=""):
        inputs = self.processor(text=task + text_input, images=image, return_tensors="pt")
        inputs = {key: value.to(
            device=self.device,
            dtype=self.dtype if value.is_floating_point() else value.dtype,
        ) for key, value in inputs.items()}
        with self.torch.inference_mode():
            generated = self.model.generate(
                **inputs, max_new_tokens=self.max_tokens,
                num_beams=self.beams, do_sample=False,
            )
        text = self.processor.batch_decode(generated, skip_special_tokens=False)[0]
        parsed = self.processor.post_process_generation(text, task=task, image_size=image.size)
        if task not in parsed:
            raise ValueError(f"Unexpected Florence response: {text[:250]}")
        return parsed[task], text

    def predict(self, image, phrase):
        payload, text = self.generate(image, TASK, phrase)
        if not isinstance(payload, dict) or "bboxes" not in payload:
            raise ValueError(f"Unexpected Florence grounding response: {text[:250]}")
        return payload, text

    def describe(self, image):
        payload, raw = self.generate(image, "<DETAILED_CAPTION>")
        return str(payload), raw

    def close(self):
        if hasattr(self, "model"):
            del self.model
        if self.device == "cuda":
            self.torch.cuda.empty_cache()


def detect(image, backend, phrases=PHRASES, tiles=2, overlap=0.25, merge_iou=0.5):
    candidates, raw_passes = [], []
    for region in regions(*image.size, tiles, overlap):
        crop = image.crop(region)
        for phrase in phrases:
            payload, raw = backend.predict(crop, phrase)
            raw_passes.append({"region": region, "phrase": phrase, "response": raw, "parsed": payload})
            boxes, labels = payload.get("bboxes", []), payload.get("labels", [])
            for index, raw_box in enumerate(boxes):
                box = map_box(raw_box, region)
                if box:
                    candidates.append({
                        "bbox": box, "region": list(region), "phrase": phrase,
                        "model_label": labels[index] if index < len(labels) else phrase,
                    })
    return merge_candidates(candidates, merge_iou), raw_passes


def area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def crop_region(box, size, padding=0.1):
    width, height = box[2] - box[0], box[3] - box[1]
    return (
        max(0, math.floor(box[0] - width * padding)),
        max(0, math.floor(box[1] - height * padding)),
        min(size[0], math.ceil(box[2] + width * padding)),
        min(size[1], math.ceil(box[3] + height * padding)),
    )


def caption_kind(text):
    text = text.lower()
    if re.search(r"\bdoor\s+(?:with|has|containing)\b", text):
        return "door"
    if re.search(r"\b(mail\s*box|letter\s*box|post\s*box|(?:mail|letter|postal)\s+slot)s?\b", text):
        return "mailbox"
    if re.search(r"\b(car|vehicle|wheel)s?\b", text):
        return "other"
    if re.search(r"\bdoors?\b", text):
        return "door"
    if re.search(r"\b(car|vehicle|wheel|chair|bench|flower|plant|logo|google|sign|number|intercom|doorbell|meter|electrical|window|vent|drain|handle|lock|utility|pole)s?\b", text):
        return "other"
    return "unknown"


def slot_inside_door(box, door):
    width, height = box[2] - box[0], box[3] - box[1]
    door_width, door_height = door[2] - door[0], door[3] - door[1]
    intersection = max(0, min(box[2], door[2]) - max(box[0], door[0])) * max(
        0, min(box[3], door[3]) - max(box[1], door[1])
    )
    return (height > 0 and width >= 2.5 * height and width >= 4
            and width <= 0.95 * door_width and height <= 0.20 * door_height
            and area(box) <= 0.20 * area(door) and intersection >= 0.9 * area(box))


def verify_candidates(image, backend, candidates):
    kept, audit = [], []
    for detection in candidates:
        box = detection["bbox"]
        caption, raw = backend.describe(image.crop(tuple(box)))
        kind = caption_kind(caption)
        record = {"candidate": detection, "caption": caption, "raw_caption": raw, "kind": kind}
        mailbox_sources = {(tuple(source["region"]), source["phrase"])
                           for source in detection["sources"]
                           if re.search(r"\b(mailbox|letterbox|postbox)\b", source["phrase"], re.I)}
        slot_only = not mailbox_sources
        windowed_door = slot_only and kind == "other" and "window" in caption.lower()
        if kind == "door" or windowed_door or (
            kind == "unknown" and slot_only and box[3] - box[1] > box[2] - box[0]
        ):
            region = crop_region(box, image.size)
            crop, slot_candidates, passes = image.crop(region), [], []
            for phrase in ("mail slot", "letterbox flap"):
                payload, raw_slot = backend.predict(crop, phrase)
                passes.append({"phrase": phrase, "region": region, "parsed": payload, "raw": raw_slot})
                for raw_box in payload.get("bboxes", []):
                    mapped = map_box(raw_box, region)
                    if mapped and slot_inside_door(mapped, box):
                        slot_candidates.append({
                            "bbox": mapped, "phrase": phrase,
                            "region": list(region), "model_label": phrase,
                        })
            slots = [item for item in merge_candidates(slot_candidates) if item["support"] >= 2]
            for slot in slots:
                slot["verification"] = "door_slot_grounded_by_two_phrases"
            kept.extend(slots)
            record.update(decision="replaced_with_slots" if slots else "rejected_door_without_confirmed_slot",
                          slot_passes=passes, replacements=slots)
        elif kind == "mailbox" or (kind == "unknown" and len(mailbox_sources) >= 2):
            checked = dict(detection, verification=(
                "caption_mailbox" if kind == "mailbox"
                else "repeated_grounding_caption_inconclusive"
            ))
            kept.append(checked)
            record["decision"] = checked["verification"]
        else:
            record["decision"] = "rejected_non_mailbox" if kind == "other" else "review_inconclusive"
        audit.append(record)
    unique = []
    for detection in sorted(kept, key=lambda item: item["support"], reverse=True):
        if not any(iou(detection["bbox"], other["bbox"]) >= 0.5 for other in unique):
            unique.append(detection)
    return unique, audit


class FlorenceMailboxVerifier:
    def __init__(self, **kwargs):
        self.backend = Florence(**kwargs)

    def verify(self, image_path: Path, detection: Dict[str, Any], debug_dir: Optional[Path] = None):
        box_data = detection.get("box") or {}
        rf_box = [float(box_data[key]) for key in ("x1", "y1", "x2", "y2")]
        with Image.open(image_path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")

        # Run Florence grounding on the normal, complete enhanced image. Only
        # results overlapping this RF-DETR proposal are used; Florence cannot
        # add an unrelated detection elsewhere in the image.
        candidates, passes = detect(image, self.backend, PHRASES, tiles=1)
        scored = []
        audit = []
        for item in candidates:
            overlap = iou(rf_box, item["bbox"])
            phrases = {
                str(source.get("phrase") or "").strip().lower()
                for source in item.get("sources") or []
            }
            has_mailbox_prompt = "mailbox" in phrases
            phrase_agreement = min(len(phrases) / 2.0, 1.0)
            box_agreement = min(overlap / 0.5, 1.0)
            verification_score = round(
                0.55 * phrase_agreement + 0.45 * box_agreement, 4
            )
            considered = has_mailbox_prompt and overlap >= 0.20
            audit.append({
                "candidate": item,
                "rf_iou": round(overlap, 4),
                "phrases": sorted(phrases),
                "considered": considered,
                "decision": "matched_rf_candidate" if considered else "ignored_non_matching_grounding",
            })
            if not considered:
                continue
            scored.append({
                "bbox": item["bbox"], "support": int(item.get("support") or 0),
                "phrases": sorted(phrases), "rf_iou": round(overlap, 4),
                "phrase_agreement": phrase_agreement,
                "box_agreement": round(box_agreement, 4),
                "florence_verification_score": verification_score,
            })
        best = max(
            scored,
            key=lambda item: item["florence_verification_score"],
            default=None,
        )
        score = float(best["florence_verification_score"]) if best else 0.0
        accepted = score > FLORENCE_ACCEPT_THRESHOLD
        result = {
            "accepted": accepted, "florence_verification_score": score,
            "florence_native_confidence": None, "threshold": FLORENCE_ACCEPT_THRESHOLD,
            "model": self.backend.model_name, "revision": self.backend.revision,
            "evidence": {
                "phrase_agreement": best["phrase_agreement"] if best else 0.0,
                "box_agreement": best["box_agreement"] if best else 0.0,
                "support": max((item["support"] for item in scored), default=0),
                "max_iou": max((item["rf_iou"] for item in scored), default=0.0),
            },
            "rf_candidate_box": rf_box,
            "scored_mailboxes": scored,
            "passes": passes, "verification": audit,
        }
        if debug_dir is not None:
            debug_dir = Path(debug_dir)
            debug_dir.mkdir(parents=True, exist_ok=True)
            safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(detection.get("det_id") or "candidate"))
            decision = "accepted" if accepted else "rejected"
            marked = image.copy()
            draw = ImageDraw.Draw(marked)
            rf_color = "#22c55e" if accepted else "#ef4444"
            draw.rectangle(rf_box, outline=rf_color, width=5)
            draw.text((rf_box[0], max(0, rf_box[1] - 18)),
                      f"RF {float(detection.get('score') or 0):.2f} -> {decision}", fill=rf_color)
            for item in scored:
                color = "#22c55e" if item["florence_verification_score"] > 0.80 else "#ef4444"
                draw.rectangle(item["bbox"], outline=color, width=4)
                draw.text((item["bbox"][0], max(0, item["bbox"][1] - 16)),
                          f"Florence {item['florence_verification_score']:.2f}", fill=color)
            stem = f"{safe_id}_{decision}"
            debug_image = debug_dir / f"{stem}.jpg"
            marked.save(debug_image, format="JPEG", quality=94)
            result["debug_image"] = str(debug_image.resolve())
            debug_json = debug_dir / f"{stem}.json"
            debug_json.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            result["debug_json"] = str(debug_json.resolve())
        return result

    def close(self):
        self.backend.close()


__all__ = ["FLORENCE_ACCEPT_THRESHOLD", "Florence", "FlorenceMailboxVerifier",
           "MODEL", "caption_kind", "detect", "iou", "map_box",
           "merge_candidates", "verify_candidates"]
