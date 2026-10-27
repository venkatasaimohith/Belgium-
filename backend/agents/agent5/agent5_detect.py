"""Agent 5A RF-DETR plus Agent 5B local Florence-2 FOV cascade."""
from __future__ import annotations

import gc
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

AGENT4_DIR = Path(__file__).resolve().parents[1] / "agent4"
if str(AGENT4_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT4_DIR))

from agent4_detect import (  # noqa: E402
    DEFAULT_RFDETR_LARGE_CHECKPOINT, DEFAULT_SCORE_THRESHOLD, Box,
    RFDetrMailboxBundleDetector, fuse_site_detections, load_detections_json,
    process_image_detections, save_detected_image,
)
try:
    from .agent5b_florence import (
        FlorenceMailboxVerifier, MODEL as FLORENCE_MODEL, iou as box_iou,
    )
except ImportError:
    from agent5b_florence import (
        FlorenceMailboxVerifier, MODEL as FLORENCE_MODEL, iou as box_iou,
    )

PRIMARY_FOV, FALLBACK_FOV = 90.0, 110.0
RF_CANDIDATE_THRESHOLD = 0.70
RF_DIRECT_ACCEPT_THRESHOLD = 0.75
FLORENCE_ACCEPT_THRESHOLD = 0.80
ProgressCallback = Callable[[str, int, int, Dict[str, Any]], None]


def _image_fov(image):
    try:
        return float(image.get("fov"))
    except (TypeError, ValueError):
        return None


def _matching_fov(images, expected):
    return [item for item in images if _image_fov(item) is not None
            and math.isclose(_image_fov(item), expected, abs_tol=0.1)]


def _base(site):
    return {
        "site_id": str(site["site_id"]), "postcode": site.get("postcode"),
        "street_norm": site.get("street_norm"), "hnr_base": site.get("hnr_base"),
        "x": site.get("x"), "y": site.get("y"), "parcel_id": site.get("parcel_id"),
        "flags": list(site.get("flags") or []), "status": site.get("status", "OK"),
        "vision_count": None, "confidence": None, "mailboxes": [],
        "detected_images": [], "detection_fov": None, "fallback_used": False,
        "manual_review": False, "agent5a_candidates": 0,
        "agent5a_direct_accepted": 0, "agent5b_reviewed": 0,
        "agent5b_accepted": 0, "agent5b_rejected": [],
    }


def _detect_images(images, site_id, *, detections_map, detector, score_threshold):
    detections = []
    for image in images:
        current = process_image_detections(
            image, site_id, detections_map=detections_map, demo_boxes=False,
            score_threshold=max(RF_CANDIDATE_THRESHOLD, score_threshold),
            buffer_m=1.0, cam_h=2.5, skip_plausibility=True, detector=detector,
        )
        fov = _image_fov(image)
        for detection in current:
            original_id = str(detection.get("det_id") or "detection")
            detection["det_id"] = f"fov{fov:g}_{original_id}" if fov is not None else original_id
            detection["source_fov"] = fov
            detection["detection_stage"] = "primary_fov90" if fov == PRIMARY_FOV else "fallback_fov110"
            detection["rfdetr_confidence"] = float(detection.get("score") or 0.0)
        detections.extend(current)
    return detections


def _categorize(candidates):
    direct, review = [], []
    for detection in candidates:
        score = float(detection.get("score") or 0.0)
        if score >= RF_DIRECT_ACCEPT_THRESHOLD:
            direct.append({**detection, "decision": "ACCEPTED",
                           "decision_source": "rfdetr_direct",
                           "florence_verification_score": None})
        elif score > RF_CANDIDATE_THRESHOLD:
            review.append(detection)
    return direct, review


def _release(model):
    if model is not None and hasattr(model, "close"):
        model.close()
    del model
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def _coords(detection):
    box = detection.get("box") or {}
    try:
        return [float(box[key]) for key in ("x1", "y1", "x2", "y2")]
    except (KeyError, TypeError, ValueError):
        return None


def _dedupe(detections):
    kept = []
    for detection in sorted(
        detections,
        key=lambda item: float(item.get("florence_verification_score") or item.get("score") or 0),
        reverse=True,
    ):
        coords = _coords(detection)
        if any(coords and _coords(other) and detection.get("image_file") == other.get("image_file")
               and box_iou(coords, _coords(other)) >= 0.5 for other in kept):
            continue
        kept.append(detection)
    return kept


def _verify(
    review_items: List[Tuple[str, Dict[str, Any]]], *, verifier=None,
    progress_callback=None, progress_offset=0, progress_total=None,
    debug_root: Optional[Path] = None,
):
    accepted, audit = {}, []
    if not review_items:
        return accepted, audit
    owns = verifier is None
    if verifier is None:
        verifier = FlorenceMailboxVerifier(
            model=os.environ.get("MAILBOX_FLORENCE_MODEL", FLORENCE_MODEL),
            device=os.environ.get("MAILBOX_FLORENCE_DEVICE", "auto"),
            cache_dir=Path(os.environ.get(
                "MAILBOX_FLORENCE_CACHE",
                str(Path(__file__).resolve().parent / ".florence_cache"),
            )),
            local_only=os.environ.get("MAILBOX_FLORENCE_LOCAL_ONLY", "0").lower()
            in {"1", "true", "yes"},
        )
    total = progress_total if progress_total is not None else progress_offset + len(review_items)
    try:
        for index, (site_id, detection) in enumerate(review_items, 1):
            path = Path(str(detection.get("image_file") or ""))
            verification = (
                verifier.verify(path, detection, debug_root / site_id)
                if debug_root is not None else verifier.verify(path, detection)
            )
            score = float(verification.get("florence_verification_score") or 0.0)
            summary = {
                "accepted": bool(verification.get("accepted")),
                "florence_verification_score": score,
                "florence_native_confidence": None, "threshold": 0.80,
                "model": verification.get("model"), "revision": verification.get("revision"),
                "evidence": verification.get("evidence") or {},
            }
            audit.append({
                "site_id": site_id, "det_id": detection.get("det_id"),
                "source_fov": detection.get("source_fov"),
                "rfdetr_confidence": detection.get("rfdetr_confidence"),
                **verification,
            })
            if summary["accepted"] and score > FLORENCE_ACCEPT_THRESHOLD:
                accepted.setdefault(site_id, []).append({
                    **detection, "decision": "ACCEPTED",
                    "decision_source": "florence_verified",
                    "florence_verification_score": score,
                    "florence_verification": summary,
                })
            if progress_callback:
                count = sum(len(items) for items in accepted.values())
                progress_callback("agent5b_verify", progress_offset + index, total,
                                  {"accepted": count, "rejected": progress_offset + index - count})
    finally:
        if owns:
            _release(verifier)
    return accepted, audit


def _annotate(site, accepted, root):
    by_image = {}
    for detection in accepted:
        if detection.get("image_file"):
            by_image.setdefault(str(detection["image_file"]), []).append(detection)
    paths = []
    for image_file, detections in by_image.items():
        source = Path(image_file)
        if source.is_file():
            output = save_detected_image(source, root / str(site["site_id"]), detections)
            paths.append(str(output))
            for detection in detections:
                detection["detected_file_path"] = str(output)
    return paths


def _finalize(site, accepted, *, selected_fov, fallback_used, reviewed, detected_root):
    result = _base(site)
    if site.get("status") == "NO_STREETVIEW":
        result["flags"] = sorted(set(result["flags"] + ["NO_STREETVIEW", "NO_IMAGERY"]))
        return result
    accepted = _dedupe(accepted)
    fused = fuse_site_detections(accepted)
    result.update(fused)
    result["flags"] = sorted(set(list(site.get("flags") or []) + fused.get("flags", [])))
    result["detection_fov"], result["fallback_used"] = selected_fov, fallback_used
    result["agent5a_candidates"] = len({
        (item.get("source_fov"), item.get("det_id")) for item in [*accepted, *reviewed]
    })
    result["agent5a_direct_accepted"] = sum(item.get("decision_source") == "rfdetr_direct" for item in accepted)
    result["agent5b_reviewed"] = len(reviewed)
    result["agent5b_accepted"] = sum(
        item.get("decision_source") == "florence_verified" for item in accepted
    )
    accepted_ids = {(item.get("source_fov"), item.get("det_id")) for item in accepted}
    result["agent5b_rejected"] = [{
        "det_id": item.get("det_id"), "rfdetr_confidence": item.get("rfdetr_confidence"),
        "reason": "florence_score_not_above_0.80",
    } for item in reviewed if (item.get("source_fov"), item.get("det_id")) not in accepted_ids]
    result["detected_images"] = _annotate(site, accepted, detected_root)
    if fallback_used and accepted:
        result["flags"] = sorted(set(result["flags"] + ["FOV110_FALLBACK", "MANUAL_REVIEW"]))
        result["status"], result["manual_review"] = "MANUAL_REVIEW", True
        result["manual_review_reason"] = "mailboxes_detected_only_in_fov110"
        result["remarks"] = f"FOV 110 fallback detected {len(accepted)} verified mailbox(es); manual ownership review required"
        result["confidence"] = "Low"
    return result


def process_site(site, *, detections_map, demo_boxes, score_threshold, detector,
                 detected_root, verifier=None):
    if demo_boxes:
        raise ValueError("Demo boxes are not supported")
    if site.get("status") == "NO_STREETVIEW":
        return _finalize(site, [], selected_fov=None, fallback_used=False,
                         reviewed=[], detected_root=detected_root)
    images = list(site.get("images") or [])
    primary, fallback = _matching_fov(images, 90.0), _matching_fov(images, 110.0)
    if not primary and not fallback and images:
        primary = [images[0]]
    all_reviewed = []
    for fov, selected, used in ((90.0, primary, False), (110.0, fallback, True)):
        if not selected:
            continue
        candidates = _detect_images(selected, str(site["site_id"]),
                                    detections_map=detections_map, detector=detector,
                                    score_threshold=score_threshold)
        direct, review = _categorize(candidates)
        all_reviewed.extend(review)
        verified, _ = _verify([(str(site["site_id"]), item) for item in review], verifier=verifier) if review else ({}, [])
        accepted = direct + verified.get(str(site["site_id"]), [])
        if accepted:
            return _finalize(site, accepted, selected_fov=fov, fallback_used=used,
                             reviewed=all_reviewed, detected_root=detected_root)
    return _finalize(site, [], selected_fov=110.0 if fallback else 90.0,
                     fallback_used=bool(fallback), reviewed=all_reviewed,
                     detected_root=detected_root)


def run(assign_json: Path, out_dir: Path, *, detections_json=None, demo_boxes=False,
        score_threshold=DEFAULT_SCORE_THRESHOLD, max_sites=None, progress_callback=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sites = json.loads(Path(assign_json).read_text(encoding="utf-8"))
    if max_sites is not None:
        sites = sites[:max_sites]
    detections_map = load_detections_json(detections_json) if detections_json else None
    site_by_id = {str(site["site_id"]): site for site in sites}
    accepted_by_site, reviewed_by_site, chosen_fov, fallback_used = {}, {}, {}, {}
    florence_audit = []
    pending = [str(site["site_id"]) for site in sites if site.get("status") != "NO_STREETVIEW"]
    rf_done, rf_total, fl_done, fl_total = 0, len(pending), 0, 0
    for fov in (90.0, 110.0):
        wave = []
        for site_id in pending:
            images = list(site_by_id[site_id].get("images") or [])
            matches = _matching_fov(images, fov)
            if fov == 90.0 and not matches and images and not any(_image_fov(item) in (90.0, 110.0) for item in images):
                matches = [images[0]]
            if matches:
                wave.append((site_id, matches))
        if fov == 110.0:
            rf_total += len(wave)
        if not wave:
            continue
        detector = None if detections_map is not None else RFDetrMailboxBundleDetector()
        candidates_by_site = {}
        try:
            for site_id, images in wave:
                candidates_by_site[site_id] = _detect_images(
                    images, site_id, detections_map=detections_map, detector=detector,
                    score_threshold=max(0.70, score_threshold),
                )
                rf_done += 1
                if progress_callback:
                    progress_callback("agent5a_detect", rf_done, max(rf_total, rf_done), {"fov": fov})
        finally:
            _release(detector)
        review_items, wave_accepted = [], {}
        for site_id, candidates in candidates_by_site.items():
            direct, review = _categorize(candidates)
            wave_accepted[site_id] = direct
            reviewed_by_site.setdefault(site_id, []).extend(review)
            review_items.extend((site_id, item) for item in review)
        fl_total += len(review_items)
        if review_items:
            verified, audit = _verify(
                review_items, progress_callback=progress_callback,
                progress_offset=fl_done, progress_total=fl_total,
                debug_root=out_dir / "agent5_florence_debug",
            )
            fl_done += len(review_items)
            florence_audit.extend(audit)
            for site_id, detections in verified.items():
                wave_accepted.setdefault(site_id, []).extend(detections)
        next_pending = []
        for site_id, _ in wave:
            accepted = wave_accepted.get(site_id, [])
            if accepted:
                accepted_by_site[site_id], chosen_fov[site_id] = accepted, fov
                fallback_used[site_id] = fov == 110.0
            elif fov == 90.0:
                next_pending.append(site_id)
                chosen_fov[site_id], fallback_used[site_id] = 90.0, False
            else:
                chosen_fov[site_id], fallback_used[site_id] = 110.0, True
        pending = next_pending
    results = [_finalize(
        site, accepted_by_site.get(str(site["site_id"]), []),
        selected_fov=chosen_fov.get(str(site["site_id"])),
        fallback_used=fallback_used.get(str(site["site_id"]), False),
        reviewed=reviewed_by_site.get(str(site["site_id"]), []),
        detected_root=out_dir / "detected",
    ) for site in sites]
    (out_dir / "agent5a_candidates.json").write_text(json.dumps(reviewed_by_site, indent=2), encoding="utf-8")
    (out_dir / "agent5b_florence.json").write_text(json.dumps(florence_audit, indent=2), encoding="utf-8")
    (out_dir / "agent5_detect.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


__all__ = ["Box", "DEFAULT_RFDETR_LARGE_CHECKPOINT", "FLORENCE_ACCEPT_THRESHOLD",
           "RF_CANDIDATE_THRESHOLD", "RF_DIRECT_ACCEPT_THRESHOLD",
           "RFDetrMailboxBundleDetector", "process_site", "run"]
