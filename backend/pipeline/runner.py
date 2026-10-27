"""
Mailbox pipeline runner — Agents 1 → 7 for one job directory.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("pipeline")


class PipelineCancelled(BaseException):
    """Cooperative cancellation that bypasses broad agent Exception handlers."""

BACKEND_ROOT = Path(__file__).resolve().parents[1]
AGENTS_ROOT = BACKEND_ROOT / "agents"
# UI / progress stage keys (frontend uses these)
STAGES = [
    {
        "key": "agent1_dedup",
        "display_name": "1. Input + Dedup",
        "description": "Normalise addresses and group multi-unit dwellings",
    },
    {
        "key": "agent2_parcel",
        "display_name": "2. Parcel",
        "description": "Download the target CadGIS parcel information",
    },
    {
        "key": "agent3_image",
        "display_name": "3. Street View Images",
        "description": "Best front-facing panorama, FOV 90 and 110",
    },
    {
        "key": "agent4_assign",
        "display_name": "4. Image Enhancement",
        "description": "Real-ESRGAN 2x enhancement",
    },
    {
        "key": "agent5_detect",
        "display_name": "5. Detect Mailboxes (A + B)",
        "description": "Agent 5A RF-DETR with Agent 5B local Florence-2 verification",
    },
    {
        "key": "agent6_reject",
        "display_name": "6. Reject Neighbour Mailboxes",
        "description": "Target-house, facade and driveway ownership filtering",
    },
    {
        "key": "agent7_floor_count",
        "display_name": "7. Floor Count",
        "description": "Target-facade window-row floor estimation",
    },
    {
        "key": "export_results",
        "display_name": "Export Results",
        "description": "Build records table and GPKG/KML outputs",
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _import_agent(folder: str, module_name: str):
    path = str(AGENTS_ROOT / folder)
    if path not in sys.path:
        sys.path.insert(0, path)
    # Drop cached module if loaded from another job path
    if module_name in sys.modules:
        del sys.modules[module_name]
    return importlib.import_module(module_name)


def _set_stage(
    job: Dict[str, Any],
    key: str,
    status: str,
    progress: float = 0,
    result: Optional[dict] = None,
    error: str = "",
) -> None:
    stages = job.setdefault("stages", [])
    stage = next((s for s in stages if s.get("name") == key), None)
    if stage is None:
        meta = next((s for s in STAGES if s["key"] == key), {})
        stage = {
            "name": key,
            "display_name": meta.get("display_name", key),
            "status": "PENDING",
            "progress": 0,
            "result": {},
            "started_at": None,
            "completed_at": None,
            "error_message": "",
        }
        stages.append(stage)
    if status == "RUNNING" and not stage.get("started_at"):
        stage["started_at"] = _now()
    if status in ("COMPLETED", "FAILED", "SKIPPED"):
        stage["completed_at"] = _now()
    stage["status"] = status
    stage["progress"] = progress
    if result is not None:
        stage["result"] = result
    if error:
        stage["error_message"] = error
    job["current_agent"] = key if status == "RUNNING" else job.get("current_agent")
    done = sum(1 for s in stages if s.get("status") == "COMPLETED")
    job["progress_pct"] = int((done / max(len(STAGES), 1)) * 100)
    if status == "RUNNING":
        job["progress_pct"] = min(
            99, int(((done + progress / 100.0) / max(len(STAGES), 1)) * 100)
        )
    job["updated_at"] = _now()


def init_job_stages(job: Dict[str, Any]) -> None:
    job["stages"] = []
    for meta in STAGES:
        job["stages"].append(
            {
                "name": meta["key"],
                "display_name": meta["display_name"],
                "status": "PENDING",
                "progress": 0,
                "result": {},
                "started_at": None,
                "completed_at": None,
                "error_message": "",
            }
        )


def _nullish(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null", "<na>"}:
        return None
    return value


def _building_type(raw: Any, predicted: Any = None) -> Optional[str]:
    t = str(raw or predicted or "").strip().upper()
    if t in ("SFU", "SDU", "SINGLE"):
        return "SDU"
    if t in ("MDU", "MULTI"):
        return "MDU"
    return t or None


def _bm_address(house: Any, box: Any, sub: Any) -> Optional[str]:
    h = "" if _nullish(house) is None else str(house).strip()
    b = "" if _nullish(box) is None else str(box).strip()
    s = "" if _nullish(sub) is None else str(sub).strip()
    if not h and not b:
        return None
    # e.g. 18A, 45, 8A (box letter) — subnumber kept in bm_subnumber
    if b and not b.isdigit():
        return f"{h}{b}"
    if b and b.isdigit() and not s:
        return f"{h}"  # numeric box stays in bm_box
    return h or None


def _bm_normalized(house: Any, box: Any, sub: Any) -> Optional[str]:
    h = "" if _nullish(house) is None else str(house).strip()
    b = "" if _nullish(box) is None else str(box).strip()
    s = "" if _nullish(sub) is None else str(sub).strip()
    if not h and not b and not s:
        return None
    addr = _bm_address(house, box, sub) or h
    return f"{addr}|{b if b.isdigit() else ''}|{s}"


def _check_label(v: Dict[str, Any], d: Dict[str, Any], member_count: int) -> str:
    """Landbase Check column: VALID / NEW / TBC."""
    flags = v.get("flags") or []
    action = v.get("action") or ""
    vision = d.get("vision_count")
    final = v.get("final_count")
    if d.get("status") == "MANUAL_REVIEW" or "MANUAL_REVIEW" in (d.get("flags") or []):
        return "TBC"
    if v.get("status") == "MISMATCH" or "MISMATCH" in flags:
        return "TBC"
    if "NO_IMAGERY" in flags or v.get("vision_status") == "NO_STREETVIEW":
        return "TBC"
    if action == "accept_match":
        return "VALID"
    if final is not None and member_count and final > member_count:
        return "NEW"
    if vision is not None and member_count and vision > member_count:
        return "NEW"
    if "FROM_REGISTER" in flags:
        return "VALID"
    if final is None and vision is None:
        return "TBC"
    return "NEW"


def _remarks_for(member: Dict[str, Any], v: Dict[str, Any], d: Dict[str, Any]) -> Optional[str]:
    parts = []
    for key in ("opmerking", "FK_opmerking", "FK check"):
        val = _nullish(member.get(key))
        if val is not None:
            parts.append(str(val))
    detector_remark = _nullish(d.get("remarks"))
    if detector_remark is not None:
        parts.append(str(detector_remark))
    mb = v.get("final_count")
    if mb is None:
        mb = d.get("vision_count")
    if mb is not None:
        parts.append(f"{mb} Mailboxes available" if mb else "No mailbox detected")
    flags = list(v.get("flags") or []) + list(d.get("flags") or [])
    if "MISMATCH" in flags:
        parts.append("vision/register mismatch")
    if "FROM_REGISTER" in flags:
        parts.append("count from register")
    if "MANUAL_REVIEW" in flags or d.get("manual_review"):
        parts.append("manual mailbox ownership review required")
    if d.get("rejected_count"):
        parts.append(f"{d['rejected_count']} neighbour mailbox detections rejected")
    # de-dupe while preserving order
    seen = set()
    uniq = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return "; ".join(uniq) if uniq else None


def member_to_landbase_row(
    member: Dict[str, Any],
    *,
    row_index: int,
    site_id: Optional[str] = None,
    verify: Optional[Dict[str, Any]] = None,
    detect: Optional[Dict[str, Any]] = None,
    floor: Optional[Dict[str, Any]] = None,
    pending: bool = False,
    member_count: int = 1,
) -> Dict[str, Any]:
    """Shape one address into the landbase UI columns (screenshot schema)."""
    v = verify or {}
    d = detect or {}
    f = floor or {}
    street = _nullish(
        member.get("aansluitadres_straatnaam")
        or member.get("bm_street_name")
        or member.get("street")
    )
    house = _nullish(
        member.get("aansluitadres_huisnummer")
        or member.get("bm_housenumber")
        or member.get("housenr")
        or member.get("hnr_base")
    )
    box = _nullish(
        member.get("aansluitadres_boxnummer")
        or member.get("bm_box")
        or member.get("hnr_bus")
    )
    sub = _nullish(
        member.get("aansluitadres_extra")
        or member.get("bm_subnumber")
        or member.get("hnr_letter")
    )
    # letter suffix on house (12A) → prefer as box-style letter in bm_address
    if sub is None and member.get("hnr_letter"):
        sub = _nullish(member.get("hnr_letter"))

    municipality = _nullish(
        member.get("aansluitadres_gemeente")
        or member.get("m_municipality_name")
        or member.get("aansluitadres_plaatsnaam")
        or member.get("name")
    )
    fid = _nullish(member.get("id") or member.get("fid") or member.get("ga_id"))
    loc_id = _nullish(member.get("loc_id") or member.get("bestadd_id"))
    btype = _building_type(
        member.get("aansluit_type") or member.get("Building Type"),
        v.get("predicted_type"),
    )
    floors = _nullish(f.get("floor_count"))
    if floors is None:
        floors = _nullish(
            member.get("Number of Floors")
            or member.get("number_of_floors")
            or member.get("floors")
        )

    check = None if pending else _check_label(v, d, member_count)
    remarks = None if pending else _remarks_for(member, v, d)
    if not pending and f.get("manual_review") and f.get("floor_count") is not None:
        floor_note = f"Floor count {f['floor_count']} requires manual review"
        remarks = f"{remarks}; {floor_note}" if remarks else floor_note
    if pending:
        remarks = _nullish(member.get("opmerking") or member.get("FK_opmerking"))

    bm_addr = _bm_address(house, box, sub)
    return {
        "id": f"rec-{row_index}",
        "fid": fid if fid is not None else row_index,
        "loc_id": loc_id,
        "bm_street_name": street,
        "bm_housenumber": None if house is None else str(house),
        "bm_box": None if box is None else str(box),
        "bm_subnumber": None if sub is None else str(sub),
        "bm_address": bm_addr,
        "m_municipality_name": municipality,
        "bm_normalized": _bm_normalized(house, box, sub),
        "name": municipality,
        "Check": check,
        "Building Type": btype,
        "Remarks": remarks,
        "Number of Floors": floors,
        "floor_count_source": "agent7_facade" if f.get("floor_count") is not None else None,
        "floor_confidence": f.get("confidence"),
        "floor_manual_review": bool(f.get("manual_review")),
        # keep pipeline extras (hidden from main table, useful for export/debug)
        "site_id": site_id,
        "vision_count": d.get("vision_count"),
        "raw_vision_count": d.get("raw_vision_count"),
        "rejected_count": d.get("rejected_count"),
        "manual_review_count": d.get("manual_review_count"),
        "register_count": v.get("register_count"),
        "final_count": v.get("final_count") if v.get("final_count") is not None else d.get("vision_count"),
        "count_source": v.get("count_source") or ("agent6_rejection" if d else None),
        "confidence": v.get("confidence") or d.get("confidence"),
        "flags": sorted(set(list(v.get("flags") or []) + list(d.get("flags") or []))),
        "status": "PENDING" if pending else (check or "TBC"),
    }


def build_records_from_pipeline(out_dir: Path) -> List[Dict[str, Any]]:
    """Merge Agent 1, Agent 6 mailbox, and Agent 7 floor-count results."""
    mapping_path = out_dir / "agent1_mapping.json"
    detect_path = out_dir / "agent6_reject.json"
    floor_path = out_dir / "agent7_floor_count.json"

    mapping = {}
    if mapping_path.exists():
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))

    detect_by_site = {}
    if detect_path.exists():
        for row in json.loads(detect_path.read_text(encoding="utf-8")):
            detect_by_site[row["site_id"]] = row

    floor_by_site = {}
    if floor_path.exists():
        for row in json.loads(floor_path.read_text(encoding="utf-8")):
            floor_by_site[row["site_id"]] = row

    records: List[Dict[str, Any]] = []
    site_ids = sorted(mapping.keys()) or sorted(detect_by_site.keys())
    row_i = 0
    for site_id in site_ids:
        members = mapping.get(site_id) or [{}]
        v: Dict[str, Any] = {}
        d = detect_by_site.get(site_id) or {}
        f = floor_by_site.get(site_id) or {}
        for member in members:
            row_i += 1
            records.append(
                member_to_landbase_row(
                    member,
                    row_index=row_i,
                    site_id=site_id,
                    verify=v,
                    detect=d,
                    floor=f,
                    pending=False,
                    member_count=len(members),
                )
            )
    return records


def run_pipeline(
    job: Dict[str, Any],
    *,
    persist: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """
    Execute Agents 1–7 for *job*.
    job must contain: job_id, input_path, work_dir, options
    """
    def save():
        if persist:
            persist(job)

    def check_cancel() -> None:
        cancel_file = Path(job["work_dir"]) / ".cancel_requested"
        if job.get("cancel_requested") or cancel_file.exists():
            raise PipelineCancelled("Stopped by user")

    progress_lock = threading.Lock()
    last_progress_save: Dict[str, float] = {}

    def report_stage(key: str):
        """Create a throttled live done/total callback for an agent loop."""
        def report(done: int, total: int, extra: Optional[Dict[str, Any]] = None) -> None:
            safe_total = max(0, int(total))
            safe_done = max(0, min(int(done), safe_total)) if safe_total else max(0, int(done))
            pct = (safe_done / safe_total * 100.0) if safe_total else 0.0
            with progress_lock:
                stage = next(s for s in job.get("stages", []) if s.get("name") == key)
                result = {**(stage.get("result") or {}), "done": safe_done, "total": safe_total}
                if extra:
                    result.update(extra)
                _set_stage(job, key, "RUNNING", pct, result)
                now = time.monotonic()
                if safe_done >= safe_total or now - last_progress_save.get(key, 0.0) >= 0.25:
                    save()
                    last_progress_save[key] = now
        return report

    def report_detection(
        phase: str, done: int, total: int, extra: Optional[Dict[str, Any]] = None
    ) -> None:
        """Store independent live counters for Agent 5A and Agent 5B."""
        with progress_lock:
            stage = next(
                s for s in job.get("stages", []) if s.get("name") == "agent5_detect"
            )
            result = dict(stage.get("result") or {})
            result[phase] = {"done": int(done), "total": int(total), **(extra or {})}
            phases = [
                value for name, value in result.items()
                if name in {"agent5a_detect", "agent5b_verify"} and isinstance(value, dict)
            ]
            total_work = sum(max(0, int(value.get("total") or 0)) for value in phases)
            done_work = sum(max(0, int(value.get("done") or 0)) for value in phases)
            pct = done_work / total_work * 100.0 if total_work else 0.0
            _set_stage(job, "agent5_detect", "RUNNING", pct, result)
            now = time.monotonic()
            if done >= total or now - last_progress_save.get("agent5_detect", 0.0) >= 0.25:
                save()
                last_progress_save["agent5_detect"] = now

    work_dir = Path(job["work_dir"])
    out_dir = work_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    input_path = Path(job["input_path"])
    opts = job.get("options") or {}

    # An explicit None means "all sites". Only consult the environment when
    # older/direct callers omitted the option completely.
    if "max_sites" in opts:
        max_sites = opts["max_sites"]
    else:
        env_max = os.environ.get("MAILBOX_MAX_SITES", "0")
        max_sites = int(env_max) if env_max not in ("", "0", "none", "None") else None

    dry_run_images = bool(opts.get("dry_run_images", not bool(os.environ.get("GOOGLE_MAPS_API_KEY"))))
    # Production mailbox detection always uses rfdetr_large_mailbox_bundle.
    # Demo boxes remain available only through Agent 4's standalone debug CLI.
    fetch_wfs = bool(opts.get("fetch_wfs", True))
    use_api_cache = bool(opts.get("use_api_cache", job.get("use_api_cache", True)))
    parcel_gpkg = opts.get("parcel_gpkg")
    streetview_year_raw = opts.get("streetview_year", job.get("streetview_year", "latest"))
    streetview_year: Optional[int] = None
    if str(streetview_year_raw).strip().lower() not in ("", "latest", "auto", "none"):
        try:
            candidate_year = int(streetview_year_raw)
            if 2007 <= candidate_year <= datetime.now(timezone.utc).year:
                streetview_year = candidate_year
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid Street View year: %r", streetview_year_raw)

    init_job_stages(job)
    job["status"] = "PROCESSING"
    job.pop("error", None)
    job.pop("traceback", None)
    job["updated_at"] = _now()
    save()

    try:
        check_cancel()
        # ── Agent 1 ──────────────────────────────────────────────
        _set_stage(job, "agent1_dedup", "RUNNING", 10)
        save()
        check_cancel()
        a1 = _import_agent("agent1", "agent1_input_dedup")
        gdf = a1.load_input(input_path)
        _set_stage(
            job, "agent1_dedup", "RUNNING", 25,
            {"total": len(gdf), "done": 0},
        )
        save()
        col_map = a1.detect_columns(gdf)
        sites_gdf, mapping = a1.deduplicate(
            gdf, col_map, max_radius=float(opts.get("max_radius", 30)),
            progress_callback=report_stage("agent1_dedup"),
        )
        a1.write_sites_gpkg(sites_gdf, out_dir / "agent1_sites.gpkg")
        a1.write_mapping_json(mapping, out_dir / "agent1_mapping.json")
        job["summary_json"] = {
            **(job.get("summary_json") or {}),
            "total_input_records": int(len(gdf)),
            "unique_addresses": int(len(sites_gdf)),
            "multi_address_sites": int((sites_gdf["member_count"] > 1).sum()),
        }
        _set_stage(
            job,
            "agent1_dedup",
            "COMPLETED",
            100,
            {"total": len(gdf), "done": len(gdf), "sites": len(sites_gdf)},
        )
        save()

        # ── Agent 2 ──────────────────────────────────────────────
        _set_stage(job, "agent2_parcel", "RUNNING", 10)
        save()
        a2 = _import_agent("agent2", "agent2_parcel")
        a2.run(
            sites_gpkg=out_dir / "agent1_sites.gpkg",
            mapping_json=out_dir / "agent1_mapping.json",
            parcel_gpkg=Path(parcel_gpkg).resolve() if parcel_gpkg else None,
            parcel_layer=opts.get("parcel_layer"),
            building_gpkg=Path(opts["building_gpkg"]).resolve() if opts.get("building_gpkg") else None,
            building_layer=opts.get("building_layer"),
            road_gpkg=Path(opts["road_gpkg"]).resolve() if opts.get("road_gpkg") else None,
            road_layer=opts.get("road_layer"),
            fetch_wfs=fetch_wfs and not parcel_gpkg,
            out_dir=out_dir,
            max_sites=max_sites,
            use_api_cache=use_api_cache,
            cancel_check=check_cancel,
            progress_callback=report_stage("agent2_parcel"),
        )
        # Agent2 writes agent2_site.json into out_dir
        site_json = out_dir / "agent2_site.json"
        n_sites = 0
        if site_json.exists():
            n_sites = len(json.loads(site_json.read_text(encoding="utf-8")))
        _set_stage(
            job,
            "agent2_parcel",
            "COMPLETED",
            100,
            {"total": n_sites, "done": n_sites},
        )
        save()
        check_cancel()

        # ── Agent 3 ──────────────────────────────────────────────
        _set_stage(job, "agent3_image", "RUNNING", 10)
        save()
        a3 = _import_agent("agent3", "agent3_image")
        a3.run(
            site_json=out_dir / "agent2_site.json",
            out_dir=out_dir,
            agent1_mapping_json=out_dir / "agent1_mapping.json",
            api_key=os.environ.get("GOOGLE_MAPS_API_KEY")
            or os.environ.get("STREETVIEW_API_KEY"),
            dry_run=dry_run_images,
            max_sites=max_sites,
            capture_year=streetview_year,
            use_api_cache=use_api_cache,
            workers=max(1, int(opts.get("streetview_workers", 10))),
            cancel_check=check_cancel,
            progress_callback=report_stage("agent3_image"),
        )
        images = json.loads((out_dir / "agent3_images.json").read_text(encoding="utf-8"))
        ok_img = sum(1 for r in images if r.get("status") == "OK")
        _set_stage(
            job,
            "agent3_image",
            "COMPLETED",
            100,
            {
                "total": len(images), "done": len(images), "successful": ok_img,
                "no_streetview": len(images) - ok_img,
            },
        )
        save()
        check_cancel()

        # ── Agent 4a Assign ──────────────────────────────────────
        _set_stage(job, "agent4_assign", "RUNNING", 10)
        save()
        a4a = _import_agent("agent4", "agent4_assign")
        enhanced = a4a.run(
            images_json=out_dir / "agent3_images.json",
            site_json=out_dir / "agent2_site.json",
            out_dir=out_dir,
            buffer_m=float(opts.get("buffer_m", 1.0)),
            enhancement_mode=str(opts.get("enhancement_mode", "ai")),
            enhancement_scale=float(opts.get("enhancement_scale", 2.0)),
            enhancement_weights=Path(
                opts.get("realesrgan_weights")
                or os.environ.get("REALESRGAN_WEIGHTS")
                or a4a.DEFAULT_WEIGHTS
            ).resolve(),
            enhancement_device=str(
                opts.get("enhancement_device")
                or os.environ.get("MAILBOX_ENHANCEMENT_DEVICE", "auto")
            ),
            enhancement_workers=max(1, int(opts.get("enhancement_workers", 10))),
            max_sites=max_sites,
            cancel_check=check_cancel,
            progress_callback=report_stage("agent4_assign"),
        )
        enhanced_images = [
            image
            for site in enhanced
            for image in (site.get("images") or [])
            if image.get("enhanced_file_path")
        ]
        enhancement_modes = sorted(
            {str(image.get("enhancement_mode")) for image in enhanced_images}
        )
        enhancement_devices = sorted(
            {str(image.get("enhancement_device")) for image in enhanced_images}
        )
        _set_stage(
            job,
            "agent4_assign",
            "COMPLETED",
            100,
            {
                "total": len(enhanced),
                "done": len(enhanced),
                "images": len(enhanced_images),
                "mode": enhancement_modes[0] if len(enhancement_modes) == 1 else enhancement_modes,
                "device": enhancement_devices[0] if len(enhancement_devices) == 1 else enhancement_devices,
            },
        )
        save()
        check_cancel()

        # ── Agent 5 Detect ───────────────────────────────────────
        _set_stage(job, "agent5_detect", "RUNNING", 10)
        save()
        a5 = _import_agent("agent5", "agent5_detect")
        a5.run(
            assign_json=out_dir / "agent4_assign.json",
            out_dir=out_dir,
            demo_boxes=False,
            max_sites=max_sites,
            score_threshold=max(0.70, float(opts.get("detection_threshold", 0.70))),
            progress_callback=report_detection,
        )
        detect = json.loads((out_dir / "agent5_detect.json").read_text(encoding="utf-8"))
        _set_stage(
            job,
            "agent5_detect",
            "COMPLETED",
            100,
            {
                "total": len(detect),
                "done": len(detect),
                "checkpoint": str(a5.DEFAULT_RFDETR_LARGE_CHECKPOINT),
                "score_threshold": max(0.70, float(opts.get("detection_threshold", 0.70))),
                "mailboxes": sum(r.get("vision_count") or 0 for r in detect),
                "agent5a_detect": next(
                    (s.get("result", {}).get("agent5a_detect") for s in job["stages"] if s["name"] == "agent5_detect"),
                    {"done": len(detect), "total": len(detect)},
                ) or {"done": len(detect), "total": len(detect)},
                "agent5b_verify": next(
                    (s.get("result", {}).get("agent5b_verify") for s in job["stages"] if s["name"] == "agent5_detect"),
                    {"done": 0, "total": 0},
                ) or {"done": 0, "total": 0},
            },
        )
        save()
        check_cancel()

        # ── Agent 6 Reject Neighbours ────────────────────────────
        _set_stage(job, "agent6_reject", "RUNNING", 10)
        save()
        a6 = _import_agent("agent6", "agent6_reject")
        rejected = a6.run(
            detect_json=out_dir / "agent5_detect.json",
            assign_json=out_dir / "agent4_assign.json",
            site_json=out_dir / "agent2_site.json",
            out_dir=out_dir,
            vision_enabled=bool(opts.get("rejection_vision", True)),
            use_cache=use_api_cache,
            max_sites=max_sites,
            cancel_check=check_cancel,
            progress_callback=report_stage("agent6_reject"),
        )
        _set_stage(
            job,
            "agent6_reject",
            "COMPLETED",
            100,
            {
                "total": len(rejected),
                "done": len(rejected),
                "mailboxes_kept": sum(r.get("vision_count") or 0 for r in rejected),
                "mailboxes_rejected": sum(r.get("rejected_count") or 0 for r in rejected),
                "manual_review": sum(1 for r in rejected if r.get("manual_review")),
            },
        )
        save()

        # ── Export / records ─────────────────────────────────────
        # Agent 7 — Floor Count
        check_cancel()
        _set_stage(job, "agent7_floor_count", "RUNNING", 10)
        save()
        a7 = _import_agent("agent7", "agent7_floor_count")
        floor_results = a7.run(
            reject_json=out_dir / "agent6_reject.json",
            assign_json=out_dir / "agent4_assign.json",
            site_json=out_dir / "agent2_site.json",
            out_dir=out_dir,
            max_sites=max_sites,
            cancel_check=check_cancel,
            progress_callback=report_stage("agent7_floor_count"),
        )
        _set_stage(
            job,
            "agent7_floor_count",
            "COMPLETED",
            100,
            {
                "total": len(floor_results),
                "done": len(floor_results),
                "counted": sum(1 for row in floor_results if row.get("floor_count") is not None),
                "manual_review": sum(1 for row in floor_results if row.get("manual_review")),
            },
        )
        save()

        _set_stage(job, "export_results", "RUNNING", 50)
        save()
        records = build_records_from_pipeline(out_dir)
        job["records"] = records
        job["summary_json"] = {
            **(job.get("summary_json") or {}),
            "sites_processed": len(records),
            "check_valid": sum(1 for r in records if r.get("Check") == "VALID"),
            "check_new": sum(1 for r in records if r.get("Check") == "NEW"),
            "check_tbc": sum(1 for r in records if r.get("Check") == "TBC"),
            "mailboxes_detected": sum(
                (r.get("final_count") or r.get("vision_count") or 0) for r in records
            ),
            "floors_counted": sum(1 for r in records if r.get("Number of Floors") is not None),
            "floor_manual_review": sum(1 for r in records if r.get("floor_manual_review")),
            "model_selected": "rfdetr_large_mailbox_bundle_last_ema",
            "max_sites": max_sites,
            "dry_run_images": dry_run_images,
            "streetview_year": streetview_year if streetview_year is not None else "latest",
            "use_api_cache": use_api_cache,
        }
        job["outputs"] = [
            {"type": "json", "file_path": str(out_dir / "agent6_reject.json")},
            {"type": "json", "file_path": str(out_dir / "agent7_floor_count.json")},
            {"type": "gpkg", "file_path": str(out_dir / "agent1_sites.gpkg")},
            {"type": "gpkg", "file_path": str(out_dir / "agent2_result.gpkg")},
        ]
        _set_stage(
            job,
            "export_results",
            "COMPLETED",
            100,
            {"total": len(records), "done": len(records)},
        )
        job["status"] = "COMPLETED"
        job["progress_pct"] = 100
        job["current_agent"] = "export_results"
        job["updated_at"] = _now()
        save()
        logger.info("Pipeline completed for job %s (%d records)", job.get("job_id"), len(records))
        return job

    except PipelineCancelled as exc:
        cur = job.get("current_agent") or "agent1_dedup"
        _set_stage(job, cur, "FAILED", 0, error=str(exc))
        job["status"] = "FAILED"
        job["error"] = str(exc)
        job["cancel_requested"] = False
        (work_dir / ".cancel_requested").unlink(missing_ok=True)
        job["updated_at"] = _now()
        save()
        logger.info("Pipeline stopped for job %s", job.get("job_id"))
        return job

    except Exception as exc:
        logger.exception("Pipeline failed for job %s", job.get("job_id"))
        cur = job.get("current_agent") or "agent1_dedup"
        _set_stage(job, cur, "FAILED", 0, error=str(exc))
        job["status"] = "FAILED"
        job["error"] = str(exc)
        job["traceback"] = traceback.format_exc()
        job["updated_at"] = _now()
        save()
        return job
