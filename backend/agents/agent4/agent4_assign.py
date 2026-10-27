#!/usr/bin/env python3
"""
Agent 4a — Image Enhancement + frontage geometry
=================================================
Runs FIRST inside Agent 4 (before detection).

For each image from Agent 3:

  1. Recompute left/right frontage corners from the panorama camera.
  2. Project the target frontage (+ buffer) into image pixel columns.
  3. Enhance/upscale the full image with Real-ESRGAN.
  4. Write enhanced images and assign.json with geometry ranges.

Ownership is decided later by geometry after detection on the enhanced image.

All code for this step lives in this folder so you can debug Assign alone.

Usage
-----
    python agent4_assign.py \\
        --images-json ../output/agent3_images.json \\
        --site-json ../output/agent2_site.json \\
        --out-dir ../output \\
        --buffer-m 1.0
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw
from shapely import wkt
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

try:
    from .image_enhancement import DEFAULT_WEIGHTS, ImageEnhancer
except ImportError:
    from image_enhancement import DEFAULT_WEIGHTS, ImageEnhancer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agent4_assign")


# ─────────────────────────────────────────────
#  Geometry (from report appendix)
# ─────────────────────────────────────────────

def left_right(
    P: Sequence[float], Q: Sequence[float], cam: Sequence[float]
) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    M = ((P[0] + Q[0]) / 2.0, (P[1] + Q[1]) / 2.0)
    dx, dy = M[0] - cam[0], M[1] - cam[1]

    def cross(C: Sequence[float]) -> float:
        return dx * (C[1] - cam[1]) - dy * (C[0] - cam[0])

    if cross(P) > cross(Q):
        return (float(P[0]), float(P[1])), (float(Q[0]), float(Q[1]))
    return (float(Q[0]), float(Q[1])), (float(P[0]), float(P[1]))


def col_to_bearing(x: float, heading: float, fov: float, W: int) -> float:
    f = (W / 2.0) / math.tan(math.radians(fov / 2.0))
    return (heading + math.degrees(math.atan((x - W / 2.0) / f))) % 360.0


def bearing_to_col(theta_deg: float, heading: float, fov: float, W: int) -> float:
    """Inverse of col_to_bearing (handles wrap near ±180 relative heading)."""
    f = (W / 2.0) / math.tan(math.radians(fov / 2.0))
    rel = ((theta_deg - heading + 180.0) % 360.0) - 180.0
    return (W / 2.0) + f * math.tan(math.radians(rel))


def bearing_deg(cam: Sequence[float], target: Sequence[float]) -> float:
    dx = target[0] - cam[0]
    dy = target[1] - cam[1]
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0


def ray_hit_frontage(
    cam: Sequence[float],
    theta_deg: float,
    A: Sequence[float],
    B: Sequence[float],
) -> Optional[Tuple[float, float]]:
    """Return (t along ray, s metres from A) or None."""
    ux = math.sin(math.radians(theta_deg))
    uy = math.cos(math.radians(theta_deg))
    vx, vy = B[0] - A[0], B[1] - A[1]
    wx, wy = A[0] - cam[0], A[1] - cam[1]
    det = vx * uy - ux * vy
    if abs(det) < 1e-9:
        return None
    t = (vx * wy - vy * wx) / det
    r = (ux * wy - uy * wx) / det
    if t <= 0:
        return None
    return t, r * math.hypot(vx, vy)


def classify_s(s: float, L: float, b: float = 1.0) -> str:
    if 0 <= s <= L:
        return "TARGET"
    if -b <= s < 0 or L < s <= L + b:
        return "AMBIGUOUS"
    return "NEIGHBOUR"


def frontage_length(A: Sequence[float], B: Sequence[float]) -> float:
    return math.hypot(B[0] - A[0], B[1] - A[1])


def refine_frontage_from_parcel(
    site: Dict[str, Any],
    point_a: Sequence[float],
    point_b: Sequence[float],
    *,
    line_tolerance_m: float = 0.75,
    max_angle_deg: float = 15.0,
) -> Tuple[Tuple[float, float], Tuple[float, float], bool]:
    """Extend a split cadastral edge across its complete parcel frontage.

    Cadastral polygons often contain several vertices along what is physically
    one straight road boundary. Agent 2 may therefore provide only one of
    those short segments. Using only that partial segment can cut through
    the target property and leave a neighbouring side sharp.

    This function stays conservative: it considers only parcel-boundary
    segments that are nearly parallel to, and within ``line_tolerance_m`` of,
    the supplied frontage line. Unrelated side and rear boundaries are not
    included. The original frontage is returned when parcel geometry is not
    available or no reliable extension exists.
    """
    a = (float(point_a[0]), float(point_a[1]))
    b = (float(point_b[0]), float(point_b[1]))
    original_length = frontage_length(a, b)
    parcel_wkt = site.get("parcel_geom_wkt")
    if original_length <= 1e-9 or not parcel_wkt:
        return a, b, False

    try:
        parcel = wkt.loads(str(parcel_wkt))
    except Exception as exc:
        logger.warning("%s: invalid parcel geometry: %s", site.get("site_id"), exc)
        return a, b, False

    polygons = (
        list(parcel.geoms)
        if getattr(parcel, "geom_type", "") == "MultiPolygon"
        else [parcel]
    )
    polygons = [polygon for polygon in polygons if hasattr(polygon, "exterior")]
    if not polygons:
        return a, b, False

    midpoint = Point((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    polygon = min(polygons, key=lambda item: item.exterior.distance(midpoint))
    coords = list(polygon.exterior.coords)

    ux = (b[0] - a[0]) / original_length
    uy = (b[1] - a[1]) / original_length
    min_alignment = math.cos(math.radians(max_angle_deg))

    def signed_projection(point: Sequence[float]) -> float:
        return (float(point[0]) - a[0]) * ux + (float(point[1]) - a[1]) * uy

    def distance_to_frontage_line(point: Sequence[float]) -> float:
        dx = float(point[0]) - a[0]
        dy = float(point[1]) - a[1]
        return abs(dx * uy - dy * ux)

    projections = [0.0, original_length]
    for start, end in zip(coords, coords[1:]):
        seg_dx = float(end[0]) - float(start[0])
        seg_dy = float(end[1]) - float(start[1])
        seg_length = math.hypot(seg_dx, seg_dy)
        if seg_length <= 1e-9:
            continue
        alignment = abs((seg_dx * ux + seg_dy * uy) / seg_length)
        if alignment < min_alignment:
            continue
        if (
            distance_to_frontage_line(start) > line_tolerance_m
            or distance_to_frontage_line(end) > line_tolerance_m
        ):
            continue
        projections.extend((signed_projection(start), signed_projection(end)))

    low = min(projections)
    high = max(projections)
    refined_length = high - low
    if refined_length <= original_length + 0.05:
        return a, b, False

    refined_a = (a[0] + ux * low, a[1] + uy * low)
    refined_b = (a[0] + ux * high, a[1] + uy * high)
    logger.info(
        "%s: extended split frontage %.2fm -> %.2fm from parcel boundary",
        site.get("site_id", "site"),
        original_length,
        refined_length,
    )
    return refined_a, refined_b, True


def project_frontage_columns(
    cam: Sequence[float],
    left: Sequence[float],
    right: Sequence[float],
    heading: float,
    fov: float,
    W: int,
    buffer_m: float,
) -> Dict[str, Any]:
    """
    Project the visible target frontage and buffer into pixel columns.

    A direct endpoint projection is not safe for rotated Street View images:
    ``tan(relative_bearing)`` repeats after 180 degrees, so a frontage behind
    the camera can appear to be in front of it.  That previously put the sharp
    band over a neighbour (or kept almost the whole image sharp).

    Instead, follow the ownership geometry used by Agent 4b: cast one ray for
    every image column and intersect it with the frontage line.  Only forward
    intersections whose position ``s`` lies on the target frontage (or its
    metre buffer) are retained.  This naturally handles bearing wrap-around,
    oblique views and views in which the target is completely outside the FOV.
    """
    L = frontage_length(left, right)
    target_columns: List[int] = []
    buffer_columns: List[int] = []

    if L > 0.0 and W > 0:
        for column in range(W):
            # Use the centre of the pixel column for a stable raster mask.
            theta = col_to_bearing(column + 0.5, heading, fov, W)
            hit = ray_hit_frontage(cam, theta, left, right)
            if hit is None:
                continue
            _, s = hit
            if 0.0 <= s <= L:
                target_columns.append(column)
            if -buffer_m <= s <= L + buffer_m:
                buffer_columns.append(column)

    def column_range(columns: Sequence[int]) -> Tuple[float, float]:
        if not columns:
            # An empty interval deliberately means "keep nothing sharp".
            return 0.0, 0.0
        return float(min(columns)), float(max(columns) + 1)

    target_lo, target_hi = column_range(target_columns)
    buffer_lo, buffer_hi = column_range(buffer_columns)

    return {
        "frontage_length_m": round(L, 3),
        "buffer_m": buffer_m,
        "col_target_lo": round(target_lo, 2),
        "col_target_hi": round(target_hi, 2),
        "col_buffer_lo": round(buffer_lo, 2),
        "col_buffer_hi": round(buffer_hi, 2),
        "left_corner": list(left),
        "right_corner": list(right),
        "sample_count": W,
        "target_visible": bool(target_columns),
        "buffer_visible": bool(buffer_columns),
        "visible_target_columns": len(target_columns),
        "visible_buffer_columns": len(buffer_columns),
    }


def _load_polygon_geometry(value: Any, site_id: str, label: str) -> Optional[Any]:
    """Load and repair an optional polygonal WKT value."""
    if not value:
        return None
    try:
        geometry = wkt.loads(str(value))
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        if geometry.is_empty:
            return None
        return geometry
    except Exception as exc:
        logger.warning("%s: invalid %s geometry: %s", site_id, label, exc)
        return None


def _polygon_parts(geometry: Any) -> List[Any]:
    """Return the non-empty polygon members of any Shapely geometry."""
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if hasattr(geometry, "geoms"):
        parts: List[Any] = []
        for member in geometry.geoms:
            parts.extend(_polygon_parts(member))
        return parts
    return []


def select_target_building_geometry(
    site: Dict[str, Any],
) -> Tuple[Optional[Any], str]:
    """Select the CadGIS footprint portion belonging to the requested address.

    The building is the visual authority. The parcel is used only to split a
    shared/terraced footprint into the portion associated with this address;
    it is never added to the visible mask while a building is available. The
    address point selects the relevant component of multipart footprints.
    """
    site_id = str(site.get("site_id") or "site")
    building = _load_polygon_geometry(
        site.get("building_geom_wkt"), site_id, "building"
    )
    if building is None:
        return None, "none"

    candidates = _polygon_parts(building)
    source = "cadgis_building"
    parcel = _load_polygon_geometry(site.get("parcel_geom_wkt"), site_id, "parcel")

    # The tolerance absorbs small cadastral rounding gaps. Only the building
    # intersection is retained: the larger parcel does not enter the mask.
    if parcel is not None:
        clipped_parts: List[Any] = []
        selector = parcel.buffer(0.10)
        for candidate in candidates:
            clipped_parts.extend(_polygon_parts(candidate.intersection(selector)))
        if clipped_parts:
            candidates = clipped_parts
            source = "cadgis_building_parcel_selected"

    if not candidates:
        return None, "none"

    address: Optional[Point] = None
    if site.get("x") is not None and site.get("y") is not None:
        try:
            address = Point(float(site["x"]), float(site["y"]))
        except (TypeError, ValueError):
            address = None

    if address is not None:
        containing = [part for part in candidates if part.buffer(0.10).covers(address)]
        if containing:
            selected = max(containing, key=lambda part: part.area)
            source += "_address_containing"
        else:
            selected = min(candidates, key=lambda part: part.distance(address))
            source += "_address_nearest"
    else:
        selected = max(candidates, key=lambda part: part.area)
        source += "_largest_component"

    return selected, source


def build_visual_target_geometry(
    site: Dict[str, Any],
    left: Sequence[float],
    right: Sequence[float],
    *,
    padding_m: float,
) -> Tuple[Optional[Any], str]:
    """Build target geometry retained for ownership/debug compatibility.

    Ownership remains based on the frontage line.  The visual mask instead
    uses the address-selected CadGIS building footprint as its primary shape,
    plus a narrow corridor around the target frontage. The parcel is only a
    selector for shared footprints. This keeps the full target facade and
    possible gate/road-edge mailbox visible without treating a neighbouring
    parcel as part of the target.
    """
    site_id = str(site.get("site_id") or "site")
    building, building_source = select_target_building_geometry(site)

    components: List[Any] = []
    source_parts: List[str] = []
    if building is not None:
        components.append(building.buffer(padding_m, join_style=2))
        source_parts.append(building_source)
    else:
        # Backward-compatible fallback for old Agent 2 output without a
        # building footprint. New/live CadGIS jobs should not use this path.
        parcel = _load_polygon_geometry(
            site.get("parcel_geom_wkt"), site_id, "parcel"
        )
        if parcel is not None:
            components.append(parcel.buffer(padding_m, join_style=2))
            source_parts.append("parcel_fallback_no_building")

    if frontage_length(left, right) > 0.0:
        frontage_corridor = LineString([left, right]).buffer(
            padding_m, cap_style=2, join_style=2
        )
        components.append(frontage_corridor)
        source_parts.append("frontage_gate_corridor")

    if not components:
        return None, "none"
    target = unary_union(components)
    if target.is_empty:
        return None, "none"
    return target, "+".join(source_parts)


def visible_point_column(
    cam: Sequence[float],
    point: Sequence[float],
    heading: float,
    fov: float,
    width: int,
) -> Optional[float]:
    """Project a ground point only when it lies inside the current image FOV."""
    theta = bearing_deg(cam, point)
    relative = ((theta - heading + 180.0) % 360.0) - 180.0
    if abs(relative) > fov / 2.0:
        return None
    return max(0.0, min(float(width), bearing_to_col(theta, heading, fov, width)))


def _image_download_implementation():
    """Load the pasted image-download vision code without duplicating it."""
    root = Path(__file__).resolve().parents[3] / "image downlaod"
    if not root.is_dir():
        raise RuntimeError(f"Missing image download project: {root}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import streetview_frontage  # type: ignore
    import vision_corners  # type: ignore

    return streetview_frontage, vision_corners


def detect_house_from_parcel_hint(
    image_rec: Dict[str, Any],
    site: Dict[str, Any],
    out_dir: Path,
) -> Optional[Dict[str, Any]]:
    """Locate the target house using the parcel/building centroid as a hint."""
    image_path = Path(str(image_rec.get("file_path") or ""))
    if not image_path.is_file():
        return None
    try:
        frontage, vision = _image_download_implementation()
        geometry, geometry_source = select_target_building_geometry(site)
        if geometry is not None:
            target = geometry.centroid
            target_x, target_y = float(target.x), float(target.y)
        else:
            target_x, target_y = float(site["x"]), float(site["y"])
            geometry_source = "address_point"

        target_lat, target_lon = frontage.lambert_to_wgs84(target_x, target_y)
        projected = frontage.project_world_to_image(
            target_lat,
            target_lon,
            float(image_rec["lat"]),
            float(image_rec["lon"]),
            float(image_rec["heading"]),
            float(image_rec.get("pitch", 0.0)),
            float(image_rec["fov"]),
            int(image_rec.get("image_width") or 640),
            int(image_rec.get("image_height") or 640),
            point_z=0.0,
        )
        if projected is None:
            return None
        address = " ".join(
            str(value).strip()
            for value in (site.get("street_norm"), site.get("hnr_base"))
            if value is not None and str(value).strip()
        )
        result = vision.vision_detect_house_bbox(
            image_path=image_path,
            hint_cx=float(projected[0]),
            hint_cy=float(projected[1]),
            address=address,
            save_hint_image=True,
        )
        if result is None:
            return None

        payload = {
            "source": "vision_parcel_centroid_hint",
            "hint_geometry_source": geometry_source,
            "hint_px": [round(float(projected[0]), 2), round(float(projected[1]), 2)],
            "source_image": str(image_path.resolve()),
            "source_fov": float(image_rec["fov"]),
            "source_heading": float(image_rec["heading"]),
            **result,
        }
        site_dir = out_dir / str(site.get("site_id") or "site")
        site_dir.mkdir(parents=True, exist_ok=True)
        bbox_path = site_dir / "house_bbox.json"
        bbox_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        annotated = Image.open(image_path).convert("RGB")
        draw = ImageDraw.Draw(annotated, "RGBA")
        box = result["bbox"]
        draw.rectangle(
            (box["x1"], box["y1"], box["x2"], box["y2"]),
            outline=(255, 32, 32, 255),
            width=4,
        )
        polygon = result.get("polygon") or []
        if len(polygon) >= 3:
            draw.line([tuple(point) for point in polygon] + [tuple(polygon[0])], fill=(255, 150, 0, 255), width=3)
        annotated_path = site_dir / "house_bbox_annotated.jpg"
        annotated.save(annotated_path, quality=95)
        payload["bbox_json_path"] = str(bbox_path.resolve())
        payload["annotated_file_path"] = str(annotated_path.resolve())
        payload["hint_file_path"] = str(
            image_path.with_name(image_path.stem + "_hint.jpg").resolve()
        )
        return payload
    except Exception as exc:
        logger.warning("%s: parcel-hint house detection failed: %s", site.get("site_id"), exc)
        return None


def transform_bbox_columns(
    house_box: Dict[str, Any], image_rec: Dict[str, Any]
) -> Optional[Tuple[float, float]]:
    """Map the source bbox columns to another same-centre Street View FOV."""
    bbox = house_box.get("bbox") or {}
    try:
        source_w = float(house_box.get("width") or image_rec.get("image_width") or 640)
        target_w = float(image_rec.get("image_width") or 640)
        source_fov = float(house_box["source_fov"])
        target_fov = float(image_rec["fov"])
        source_heading = float(house_box["source_heading"])
        target_heading = float(image_rec["heading"])
        # Rectified images rotate to a different heading; geometry projection is
        # safer for those views than pretending they share the source centre.
        if abs(((target_heading - source_heading + 180.0) % 360.0) - 180.0) > 1.0:
            return None
        source_focal = (source_w / 2.0) / math.tan(math.radians(source_fov / 2.0))
        target_focal = (target_w / 2.0) / math.tan(math.radians(target_fov / 2.0))

        def convert(x: float) -> float:
            angle = math.atan((x - source_w / 2.0) / source_focal)
            return target_w / 2.0 + target_focal * math.tan(angle)

        lo = max(0.0, min(target_w, convert(float(bbox["x1"]))))
        hi = max(0.0, min(target_w, convert(float(bbox["x2"]))))
        return (lo, hi) if hi > lo else None
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


# ─────────────────────────────────────────────
#  Per-site / per-image assign
# ─────────────────────────────────────────────

def assign_image(
    image_rec: Dict[str, Any],
    site: Dict[str, Any],
    *,
    buffer_m: float,
    enhancer: ImageEnhancer,
    out_enhanced_dir: Path,
) -> Dict[str, Any]:
    cam = (float(image_rec["cam_x"]), float(image_rec["cam_y"]))
    camera_frontage = image_rec.get("frontage_source") == "agent3_camera_sightline"
    if camera_frontage:
        pa = image_rec.get("left_corner")
        pb = image_rec.get("right_corner")
    else:
        pa = site.get("frontage_point_a") or image_rec.get("left_corner")
        pb = site.get("frontage_point_b") or image_rec.get("right_corner")
    if not pa or not pb:
        return {
            "pano_id": image_rec.get("pano_id"),
            "status": "NO_GEOMETRY",
            "file_path": image_rec.get("file_path"),
        }

    original_frontage_length = frontage_length(pa, pb)
    # Agent 3's camera sightline already identifies the exact parcel edge in
    # front of the addressed building. Expanding it from the full parcel can
    # pull an adjacent facade back into the target projection.
    if camera_frontage:
        pa = (float(pa[0]), float(pa[1]))
        pb = (float(pb[0]), float(pb[1]))
        frontage_refined = False
    else:
        pa, pb, frontage_refined = refine_frontage_from_parcel(site, pa, pb)
    left, right = left_right(pa, pb, cam)
    heading = float(image_rec["heading"])
    fov = float(image_rec["fov"])
    src = Path(image_rec["file_path"])
    if not src.is_file():
        raise FileNotFoundError(f"Agent 3 image not found: {src}")
    enhanced_path = (
        out_enhanced_dir
        / site["site_id"]
        / f"{src.stem}_{enhancer.mode}_x{enhancer.scale:g}.jpg"
    )
    W, H = enhancer.process(src, enhanced_path)

    proj = project_frontage_columns(
        cam, left, right, heading, fov, W, buffer_m=buffer_m
    )
    return {
        "pano_id": image_rec.get("pano_id"),
        "file_path": str(src.resolve()) if src.exists() else image_rec.get("file_path"),
        "enhanced_file_path": str(enhanced_path.resolve()),
        "enhancement_mode": enhancer.mode,
        "enhancement_scale": enhancer.scale,
        "enhancement_device": enhancer.device,
        "cam_x": cam[0],
        "cam_y": cam[1],
        "heading": heading,
        "fov": fov,
        "pitch": image_rec.get("pitch", 0.0),
        "image_width": W,
        "image_height": H,
        "date": image_rec.get("date"),
        # Preserve Agent 3 view information for Agent 4b and review tooling.
        "image_type": image_rec.get("image_type"),
        "viewpoint": image_rec.get("viewpoint"),
        "rotation_label": image_rec.get("rotation_label"),
        "rotation_offset_deg": image_rec.get("rotation_offset_deg"),
        "frontage_refined_from_parcel": frontage_refined,
        "frontage_source": image_rec.get("frontage_source") or "agent2_parcel",
        "original_frontage_length_m": round(original_frontage_length, 3),
        "status": "OK",
        **proj,
    }


def process_site(
    site_images: Dict[str, Any],
    site_geom: Dict[str, Any],
    *,
    buffer_m: float,
    enhancer: ImageEnhancer,
    out_enhanced_dir: Path,
) -> Dict[str, Any]:
    site_id = site_images["site_id"]
    out: Dict[str, Any] = {
        "site_id": site_id,
        "postcode": site_images.get("postcode") or site_geom.get("postcode"),
        "street_norm": site_images.get("street_norm") or site_geom.get("street_norm"),
        "hnr_base": site_images.get("hnr_base") or site_geom.get("hnr_base"),
        "x": site_geom.get("x") or site_images.get("x"),
        "y": site_geom.get("y") or site_images.get("y"),
        "frontage_point_a": site_geom.get("frontage_point_a"),
        "frontage_point_b": site_geom.get("frontage_point_b"),
        "frontage_length_m": site_geom.get("frontage_length_m"),
        "parcel_id": site_geom.get("parcel_id"),
        "flags": list(site_images.get("flags") or []),
        "status": site_images.get("status", "OK"),
        "images": [],
    }

    if site_images.get("status") == "NO_STREETVIEW":
        out["status"] = "NO_STREETVIEW"
        out["flags"] = sorted(set(out["flags"] + ["NO_STREETVIEW", "NO_IMAGERY"]))
        return out

    for img in site_images.get("images") or []:
        out["images"].append(
            assign_image(
                img,
                site_geom,
                buffer_m=buffer_m,
                enhancer=enhancer,
                out_enhanced_dir=out_enhanced_dir,
            )
        )

    if not out["images"]:
        out["status"] = "NO_STREETVIEW"
    else:
        out["status"] = "OK"
    return out


def run(
    images_json: Path,
    site_json: Path,
    out_dir: Path,
    *,
    buffer_m: float = 1.0,
    enhancement_mode: str = "ai",
    enhancement_scale: float = 2.0,
    enhancement_weights: Path = DEFAULT_WEIGHTS,
    enhancement_device: str = "auto",
    enhancement_workers: int = 10,
    max_sites: Optional[int] = None,
    cancel_check: Optional[Callable[[], None]] = None,
    progress_callback: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
) -> List[Dict[str, Any]]:
    images_data = json.loads(Path(images_json).read_text(encoding="utf-8"))
    sites_data = json.loads(Path(site_json).read_text(encoding="utf-8"))
    site_by_id = {s["site_id"]: s for s in sites_data}

    if max_sites is not None:
        images_data = images_data[:max_sites]

    out_dir = Path(out_dir)
    enhanced_dir = out_dir / "enhanced"
    enhanced_dir.mkdir(parents=True, exist_ok=True)
    enhancer = ImageEnhancer(
        mode=enhancement_mode,
        scale=enhancement_scale,
        weights=enhancement_weights,
        device=enhancement_device,
    )
    logger.info(
        "Image enhancement ready: mode=%s scale=%s device=%s weights=%s",
        enhancer.mode,
        enhancer.scale,
        enhancer.device,
        enhancer.weights,
    )

    total = len(images_data)

    def process_one(site_images: Dict[str, Any]) -> Dict[str, Any]:
        if cancel_check:
            cancel_check()
        geom = site_by_id.get(site_images["site_id"], {})
        return process_site(
            site_images,
            geom,
            buffer_m=buffer_m,
            enhancer=enhancer,
            out_enhanced_dir=enhanced_dir,
        )

    # AI mode also uses the worker pool.  RealESRGAN protects the shared GPU
    # inference section with a lock, while decoding, preparation and saving
    # overlap across workers without creating extra model copies.
    if total > 1 and enhancement_workers > 1:
        with ThreadPoolExecutor(
            max_workers=min(int(enhancement_workers), total)
        ) as executor:
            ordered: List[Optional[Dict[str, Any]]] = [None] * total
            futures = {
                executor.submit(process_one, site_images): index
                for index, site_images in enumerate(images_data)
            }
            completed = 0
            for future in as_completed(futures):
                ordered[futures[future]] = future.result()
                completed += 1
                if progress_callback:
                    progress_callback(completed, total, {})
            results = [item for item in ordered if item is not None]
    else:
        results = []
        for site_images in images_data:
            results.append(process_one(site_images))
            if progress_callback:
                progress_callback(len(results), total, {})

    for i, res in enumerate(results):
        n = len(res.get("images") or [])
        print(
            f"  [{i+1:>5}/{total}] {res['site_id']}  status={res['status']:14}  "
            f"assigned_images={n}"
        )

    out_path = out_dir / "agent4_assign.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    ok = sum(1 for r in results if r["status"] == "OK")
    print("\n" + "=" * 60)
    print("  Agent 4a Image Enhancement complete")
    print(f"  Sites processed   : {total}")
    print(f"  OK                : {ok}")
    print(f"  Assign JSON       : {out_path}")
    print(f"  Enhancement mode  : {enhancer.mode}")
    print(f"  Enhanced images   : {enhanced_dir}")
    print("=" * 60 + "\n")
    return results


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Agent 4a - Image Enhancement before mailbox detection.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--images-json", required=True, help="agent3_images.json")
    p.add_argument("--site-json", required=True, help="agent2_site.json")
    p.add_argument("--out-dir", "-o", default=".")
    p.add_argument("--buffer-m", type=float, default=1.0)
    p.add_argument("--enhancement-mode", choices=("auto", "enhance", "ai"), default="ai")
    p.add_argument("--enhancement-scale", type=float, default=2.0)
    p.add_argument("--enhancement-weights", type=Path, default=DEFAULT_WEIGHTS)
    p.add_argument("--enhancement-device", choices=("auto", "cpu", "cuda"), default="auto")
    p.add_argument("--enhancement-workers", type=int, default=10)
    p.add_argument("--max-sites", type=int, default=None)
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    run(
        images_json=Path(args.images_json).resolve(),
        site_json=Path(args.site_json).resolve(),
        out_dir=Path(args.out_dir).resolve(),
        buffer_m=args.buffer_m,
        enhancement_mode=args.enhancement_mode,
        enhancement_scale=args.enhancement_scale,
        enhancement_weights=args.enhancement_weights,
        enhancement_device=args.enhancement_device,
        enhancement_workers=max(1, args.enhancement_workers),
        max_sites=args.max_sites,
    )


if __name__ == "__main__":
    main()
