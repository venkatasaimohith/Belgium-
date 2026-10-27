"""Agent 6 — geometry-first neighbour-mailbox rejection.

Only this agent assigns mailbox ownership.  It consumes earlier-agent outputs
without modifying them and uses, in order:

1. target and adjacent cadastral frontage geometry;
2. a parcel-clipped target building and camera-facing facade projection;
3. one bounded image calibration shared by every projected parcel edge;
4. mailbox bearings from the calibrated Street View frame;
5. semantic segmentation for facade context and vertical image extent;
6. target-door proximity only as a conservative fallback; and
7. manual review whenever independent evidence disagrees.

The previous vision-generated facade/driveway polygons are intentionally not
used by this implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import math
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import geopandas as gpd
from PIL import Image, ImageDraw
from shapely import wkt
from shapely.geometry import LineString, MultiPolygon, Point, Polygon


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from api_cache import cached_urlopen_bytes


logger = logging.getLogger("agent6_reject")

TARGET = "TARGET"
NEIGHBOUR = "NEIGHBOUR"
MANUAL_REVIEW = "MANUAL_REVIEW"

WFS_URL = "https://geo.api.vlaanderen.be/GRB/wfs"
WFS_QUERY = "service=WFS&version=2.0.0&request=GetFeature&outputFormat=application/json"
PARCEL_TYPE = "GRB:ADP"
LAMBERT72 = 31370
ADJACENT_TOLERANCE_M = 1.5
RAY_LENGTH_M = 250.0
GEOMETRY_CACHE_VERSION = 5

_MASK2FORMER: Any = None
_MASK2FORMER_PROCESSOR: Any = None
_MASK2FORMER_MODEL_ID: Optional[str] = None
_MASK2FORMER_UNAVAILABLE = False
_DOOR_DETECTOR: Any = None
_DOOR_DETECTOR_UNAVAILABLE = False


def pinhole_bearing(x_px: float, width: float, heading: float, fov: float) -> float:
    """Convert an image column to a compass bearing using pinhole projection."""
    if width <= 0 or not 0.0 < fov < 180.0:
        raise ValueError("Image width and FOV must be valid")
    focal = (width / 2.0) / math.tan(math.radians(fov / 2.0))
    offset = math.degrees(math.atan((float(x_px) - width / 2.0) / focal))
    return (float(heading) + offset) % 360.0


def _ray_segment_hit(
    camera: Sequence[float],
    bearing: float,
    start: Sequence[float],
    end: Sequence[float],
    *,
    endpoint_tolerance_m: float = 0.20,
) -> Optional[Dict[str, float]]:
    """Return ray distance and position along a frontage segment."""
    cam_x, cam_y = float(camera[0]), float(camera[1])
    ax, ay = float(start[0]), float(start[1])
    bx, by = float(end[0]), float(end[1])
    ux, uy = math.sin(math.radians(bearing)), math.cos(math.radians(bearing))
    vx, vy = bx - ax, by - ay
    wx, wy = ax - cam_x, ay - cam_y
    determinant = vx * uy - ux * vy
    if abs(determinant) < 1e-9:
        return None
    distance = (vx * wy - vy * wx) / determinant
    ratio = (ux * wy - uy * wx) / determinant
    length = math.hypot(vx, vy)
    if distance <= 0.0 or length <= 0.0:
        return None
    tolerance_ratio = endpoint_tolerance_m / length
    if ratio < -tolerance_ratio or ratio > 1.0 + tolerance_ratio:
        return None
    return {
        "distance_m": float(distance),
        "along_m": float(ratio * length),
        "x": float(cam_x + distance * ux),
        "y": float(cam_y + distance * uy),
    }


def _polygon_parts(geometry: Any) -> List[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return [part for part in geometry.geoms if not part.is_empty]
    return []


def _bearing_xy(origin: Sequence[float], target: Sequence[float]) -> float:
    dx = float(target[0]) - float(origin[0])
    dy = float(target[1]) - float(origin[1])
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _project_world_column(image: Dict[str, Any], point: Sequence[float]) -> Optional[float]:
    """Project a Lambert-72 ground point into a rectilinear image column."""
    width = float(image.get("image_width") or 0.0)
    fov = float(image.get("fov") or 0.0)
    if width <= 0.0 or not 0.0 < fov < 180.0:
        return None
    camera = [float(image["cam_x"]), float(image["cam_y"])]
    bearing = _bearing_xy(camera, point)
    relative = (bearing - float(image["heading"]) + 180.0) % 360.0 - 180.0
    if abs(relative) >= 89.0:
        return None
    focal = (width / 2.0) / math.tan(math.radians(fov / 2.0))
    column = width / 2.0 + focal * math.tan(math.radians(relative))
    # Keep partially visible edges usable while rejecting geometry wholly far
    # outside the current view.
    if column < -width * 0.5 or column > width * 1.5:
        return None
    return max(0.0, min(width, float(column)))


def _heading_for_pixel_alignment(
    image: Dict[str, Any], projected_x: float, observed_x: float
) -> float:
    """Return a heading that moves a projected column onto an observed column."""
    width = float(image["image_width"])
    fov = float(image["fov"])
    focal = (width / 2.0) / math.tan(math.radians(fov / 2.0))

    def offset(column: float) -> float:
        return math.degrees(math.atan((float(column) - width / 2.0) / focal))

    return (float(image["heading"]) + offset(projected_x) - offset(observed_x)) % 360.0


def _estimate_horizontal_calibration(
    image: Dict[str, Any],
    facade_left: float,
    facade_right: float,
    picture: Image.Image,
    building_mask: Optional[Any] = None,
) -> Dict[str, Any]:
    """Align the projected facade sides to a pair of real vertical seams.

    One global yaw correction is estimated; parcel sides are never moved
    independently.  Weak or ambiguous image evidence is reported as
    unverified instead of being allowed to cause an automatic rejection.
    """
    try:
        import cv2
        import numpy as np

        gray = cv2.cvtColor(np.asarray(picture), cv2.COLOR_RGB2GRAY)
        height, width = gray.shape
        span = float(facade_right) - float(facade_left)
        if width <= 0 or span < width * 0.025:
            return {"status": "unverified", "reason": "projected_facade_too_narrow", "reliable": False}

        y0, y1 = int(height * 0.14), int(height * 0.90)
        gradient = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))[y0:y1]
        if building_mask is not None:
            mask_roi = np.asarray(building_mask[y0:y1]).astype(bool)
            gradient = np.where(mask_roi, gradient, gradient * 0.35)
        profile = np.mean(gradient, axis=0)
        profile = cv2.GaussianBlur(profile.reshape(1, -1), (0, 1), sigmaX=2.5).reshape(-1)
        baseline = float(np.median(profile)) + 1e-6

        max_shift = max(8, min(int(width * 0.12), int(span * 0.80)))
        candidates: List[Tuple[float, int, float, float]] = []
        for shift in range(-max_shift, max_shift + 1):
            left = int(round(float(facade_left) + shift))
            right = int(round(float(facade_right) + shift))
            if left < 2 or right >= width - 2:
                continue
            left_score = float(profile[left]) / baseline
            right_score = float(profile[right]) / baseline
            # Both sides must be supported. The minimum term prevents one
            # strong window/lamppost edge from dragging the whole projection.
            score = 0.55 * min(left_score, right_score) + 0.45 * (left_score + right_score) / 2.0
            candidates.append((score, shift, left_score, right_score))
        if not candidates:
            return {"status": "unverified", "reason": "no_alignment_candidates", "reliable": False}

        candidates.sort(reverse=True)
        best_score, best_shift, left_score, right_score = candidates[0]
        separated = [item[0] for item in candidates if abs(item[1] - best_shift) >= max(6, int(width * 0.01))]
        runner_up = max(separated) if separated else baseline
        prominence = best_score / max(float(runner_up), 1e-6)
        zero_score = next((item[0] for item in candidates if item[1] == 0), best_score)
        improvement = best_score / max(float(zero_score), 1e-6)
        edge_strength = min(left_score, right_score)
        reliable = (
            edge_strength >= 1.35
            and prominence >= 1.025
            and abs(best_shift) < max_shift - 2
            and (abs(best_shift) <= width * 0.012 or improvement >= 1.08)
        )
        projected_mid = (float(facade_left) + float(facade_right)) / 2.0
        observed_mid = projected_mid + float(best_shift)
        calibrated_heading = _heading_for_pixel_alignment(image, projected_mid, observed_mid)
        correction = (calibrated_heading - float(image["heading"]) + 180.0) % 360.0 - 180.0
        return {
            "status": "aligned" if reliable else "unverified",
            "reliable": bool(reliable),
            "pixel_shift": float(best_shift),
            "heading_correction_deg": float(correction),
            "calibrated_heading": float(calibrated_heading),
            "edge_strength": float(edge_strength),
            "peak_prominence": float(prominence),
            "improvement_over_uncalibrated": float(improvement),
            "search_radius_px": int(max_shift),
        }
    except Exception as exc:
        logger.info("Horizontal facade calibration unavailable: %s", exc)
        return {"status": "unverified", "reason": str(exc), "reliable": False}


def _camera_facing_edge(geometry: Any, camera: Point, target: Point) -> Optional[LineString]:
    """Select the boundary edge first crossed from the camera to the target."""
    sight_line = LineString([(camera.x, camera.y), (target.x, target.y)])
    hits: List[Tuple[float, float, LineString]] = []
    fallback: List[Tuple[float, float, LineString]] = []
    for edge in _edges(geometry):
        fallback.append((edge.distance(sight_line), edge.distance(camera), edge))
        intersection = edge.intersection(sight_line)
        if not intersection.is_empty:
            hits.append((intersection.distance(camera), -edge.length, edge))
    if hits:
        return min(hits, key=lambda item: (item[0], item[1]))[2]
    return min(fallback, key=lambda item: (item[0], item[1]))[2] if fallback else None


def _extend_edge(edge: LineString, amount_m: float) -> LineString:
    start, end = list(edge.coords)[0], list(edge.coords)[-1]
    dx, dy = float(end[0]) - float(start[0]), float(end[1]) - float(start[1])
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return edge
    ux, uy = dx / length, dy / length
    return LineString(
        [
            (float(start[0]) - ux * amount_m, float(start[1]) - uy * amount_m),
            (float(end[0]) + ux * amount_m, float(end[1]) + uy * amount_m),
        ]
    )


def _parcel_guided_target_geometry(
    site: Dict[str, Any], image: Dict[str, Any]
) -> Tuple[Any, Any, Optional[LineString]]:
    """Return parcel, parcel-clipped target building, and visible frontage."""
    parcel_wkt = site.get("parcel_geom_wkt")
    building_wkt = site.get("building_geom_wkt")
    if not parcel_wkt or not building_wkt:
        return None, None, None
    parcel = wkt.loads(str(parcel_wkt))
    building = wkt.loads(str(building_wkt))
    if not parcel.is_valid:
        parcel = parcel.buffer(0)
    if not building.is_valid:
        building = building.buffer(0)
    target_building = building.intersection(parcel.buffer(0.20))
    if target_building.is_empty:
        target_building = building
    address = Point(
        float(site.get("x") or target_building.centroid.x),
        float(site.get("y") or target_building.centroid.y),
    )
    parts = _polygon_parts(target_building)
    if parts:
        target_building = min(
            parts,
            key=lambda part: (
                0 if part.covers(address) else 1,
                part.distance(address),
                -part.area,
            ),
        )
    camera = Point(float(image["cam_x"]), float(image["cam_y"]))
    frontage = _camera_facing_edge(parcel, camera, target_building.centroid)
    return parcel, target_building, frontage


def _edges(geometry: Any) -> List[LineString]:
    result: List[LineString] = []
    for polygon in _polygon_parts(geometry):
        coordinates = list(polygon.exterior.coords)
        for index in range(len(coordinates) - 1):
            edge = LineString([coordinates[index], coordinates[index + 1]])
            if edge.length > 0.20:
                result.append(edge)
    return result


def _parallel_score(edge: LineString, reference: LineString) -> float:
    ex = edge.coords[-1][0] - edge.coords[0][0]
    ey = edge.coords[-1][1] - edge.coords[0][1]
    rx = reference.coords[-1][0] - reference.coords[0][0]
    ry = reference.coords[-1][1] - reference.coords[0][1]
    denominator = math.hypot(ex, ey) * math.hypot(rx, ry)
    if denominator <= 1e-9:
        return 0.0
    return abs((ex * rx + ey * ry) / denominator)


def _road_facing_edge(
    geometry: Any,
    camera: Point,
    target_frontage: LineString,
) -> Optional[LineString]:
    """Build the complete camera-facing frontage, including split CAD edges."""
    candidates = _edges(geometry)
    if not candidates:
        return None
    start = target_frontage.coords[0]
    end = target_frontage.coords[-1]
    dx, dy = float(end[0]) - float(start[0]), float(end[1]) - float(start[1])
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return None
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    reference_mid = target_frontage.interpolate(0.5, normalized=True)
    if (camera.x - reference_mid.x) * nx + (camera.y - reference_mid.y) * ny < 0.0:
        nx, ny = -nx, -ny

    aligned: List[Tuple[LineString, float, float]] = []
    for edge in candidates:
        parallel = _parallel_score(edge, target_frontage)
        if parallel < 0.78:
            continue
        middle = edge.interpolate(0.5, normalized=True)
        facing_coordinate = middle.x * nx + middle.y * ny
        aligned.append((edge, parallel, facing_coordinate))
    if not aligned:
        return min(candidates, key=lambda edge: edge.distance(camera))

    camera_side = max(item[2] for item in aligned)
    selected = [item for item in aligned if item[2] >= camera_side - 2.0]
    projected: List[Tuple[float, float]] = []
    for edge, parallel, _ in selected:
        for x, y in (edge.coords[0], edge.coords[-1]):
            projected.append((float(x) * ux + float(y) * uy, float(x) * nx + float(y) * ny))
    if len(projected) < 2:
        return min((item[0] for item in aligned), key=lambda edge: edge.distance(camera))
    t0 = min(value[0] for value in projected)
    t1 = max(value[0] for value in projected)
    normal = sum(value[1] for value in projected) / len(projected)
    merged = LineString(
        [
            (t0 * ux + normal * nx, t0 * uy + normal * ny),
            (t1 * ux + normal * nx, t1 * uy + normal * ny),
        ]
    )
    return merged if merged.length > 0.20 else min(
        (item[0] for item in aligned), key=lambda edge: edge.distance(camera)
    )


def _parcel_identifier(row: Any) -> str:
    for key in ("CAPAKEY", "capakey", "CaPaKey", "ID", "id"):
        value = row.get(key) if hasattr(row, "get") else None
        if value is not None and str(value).strip():
            return str(value)
    return str(getattr(row, "name", "unknown"))


def _fetch_adjacent_parcels(
    target: Any,
    *,
    use_api_cache: bool,
) -> gpd.GeoDataFrame:
    min_x, min_y, max_x, max_y = target.bounds
    padding = 30.0
    bbox = f"{min_x-padding},{min_y-padding},{max_x+padding},{max_y+padding}"
    url = f"{WFS_URL}?{WFS_QUERY}&typeNames={urllib.parse.quote(PARCEL_TYPE)}&bbox={bbox}"
    raw = cached_urlopen_bytes(
        url,
        service="flanders-wfs",
        timeout=30,
        headers={"User-Agent": "MailboxPipeline/Agent6"},
        use_cache=use_api_cache,
    )
    parcels = gpd.read_file(io.BytesIO(raw))
    if parcels.crs is None:
        parcels = parcels.set_crs(epsg=LAMBERT72)
    elif parcels.crs.to_epsg() != LAMBERT72:
        parcels = parcels.to_crs(epsg=LAMBERT72)
    return parcels


def build_frontages(
    site: Dict[str, Any],
    image: Dict[str, Any],
    *,
    use_api_cache: bool,
    cached_neighbours: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build target plus adjacent cadastral frontage segments."""
    left = image.get("left_corner") or site.get("frontage_point_a")
    right = image.get("right_corner") or site.get("frontage_point_b")
    target_source = "agent4_target_frontage"
    try:
        _, _, projected_frontage = _parcel_guided_target_geometry(site, image)
        if projected_frontage is not None:
            coordinates = list(projected_frontage.coords)
            left, right = coordinates[0], coordinates[-1]
            target_source = "agent6_camera_sightline_frontage"
    except Exception as exc:
        logger.info("%s: camera-facing frontage fallback unavailable: %s", site.get("site_id"), exc)
    if not left or not right:
        return [], {"status": "missing_target_frontage", "adjacent_count": 0}
    target_line = LineString([left, right])
    camera = Point(float(image["cam_x"]), float(image["cam_y"]))
    target_id = str(site.get("parcel_id") or "target")
    frontages: List[Dict[str, Any]] = [
        {
            "owner": TARGET,
            "parcel_id": target_id,
            "start": [float(left[0]), float(left[1])],
            "end": [float(right[0]), float(right[1])],
            "source": target_source,
        }
    ]

    if cached_neighbours is not None:
        for record in cached_neighbours:
            if record.get("owner") == NEIGHBOUR:
                frontages.append(dict(record))
        return frontages, {
            "status": "cached",
            "adjacent_count": sum(1 for item in frontages if item["owner"] == NEIGHBOUR),
        }

    parcel_wkt = site.get("parcel_geom_wkt")
    if not parcel_wkt:
        return frontages, {"status": "target_only_no_parcel", "adjacent_count": 0}
    try:
        target = wkt.loads(str(parcel_wkt))
        parcels = _fetch_adjacent_parcels(target, use_api_cache=use_api_cache)
        selector = target.buffer(ADJACENT_TOLERANCE_M)
        adjacent = parcels[
            parcels.geometry.apply(
                lambda geometry: not geometry.is_empty
                and geometry.intersects(selector)
                and geometry.intersection(target).area < max(0.5, target.area * 0.50)
                and geometry.distance(target) <= ADJACENT_TOLERANCE_M
            )
        ]
        for _, row in adjacent.iterrows():
            edge = _road_facing_edge(row.geometry, camera, target_line)
            if edge is None:
                continue
            # Touching rear parcels are cadastral neighbours but are not
            # visible frontage competitors in this street-level image.
            if edge.distance(camera) > target_line.distance(camera) + 15.0:
                continue
            coordinates = list(edge.coords)
            frontages.append(
                {
                    "owner": NEIGHBOUR,
                    "parcel_id": _parcel_identifier(row),
                    "start": [float(coordinates[0][0]), float(coordinates[0][1])],
                    "end": [float(coordinates[-1][0]), float(coordinates[-1][1])],
                    "source": "agent6_adjacent_cadgis_frontage",
                }
            )
        return frontages, {
            "status": "ok",
            "adjacent_count": sum(1 for item in frontages if item["owner"] == NEIGHBOUR),
        }
    except Exception as exc:
        logger.warning("%s: adjacent parcel lookup failed: %s", site.get("site_id"), exc)
        return frontages, {
            "status": "adjacent_lookup_failed",
            "adjacent_count": 0,
            "error": str(exc),
        }


def ray_frontage_evidence(
    image: Dict[str, Any],
    x_px: float,
    frontages: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    width = float(image["image_width"])
    bearing = pinhole_bearing(x_px, width, float(image["heading"]), float(image["fov"]))
    camera = [float(image["cam_x"]), float(image["cam_y"])]
    hits: List[Dict[str, Any]] = []
    for frontage in frontages:
        hit = _ray_segment_hit(camera, bearing, frontage["start"], frontage["end"])
        if hit is not None and hit["distance_m"] <= RAY_LENGTH_M:
            hits.append({**frontage, **hit})
    hits.sort(key=lambda hit: hit["distance_m"])
    if not hits:
        return {"owner": MANUAL_REVIEW, "bearing": bearing, "hit": None, "hits": []}

    nearest_distance = hits[0]["distance_m"]
    tied = [hit for hit in hits if abs(hit["distance_m"] - nearest_distance) <= 0.35]
    tied_owners = {hit["owner"] for hit in tied}
    owner = tied[0]["owner"] if len(tied_owners) == 1 else MANUAL_REVIEW
    return {"owner": owner, "bearing": bearing, "hit": hits[0], "hits": hits}


def _column_state(
    image: Dict[str, Any], x_px: float, boundaries: Optional[Dict[str, Any]] = None
) -> str:
    if boundaries and boundaries.get("source") == "parcel_guided_projection":
        try:
            target_lo = float(boundaries["frontage_left"])
            target_hi = float(boundaries["frontage_right"])
            buffer_lo = float(boundaries["left"])
            buffer_hi = float(boundaries["right"])
            if target_hi > target_lo and target_lo <= x_px <= target_hi:
                return TARGET
            if buffer_hi > buffer_lo and buffer_lo <= x_px <= buffer_hi:
                return MANUAL_REVIEW
            return NEIGHBOUR
        except (KeyError, TypeError, ValueError):
            return MANUAL_REVIEW
    try:
        target_lo = float(image["col_target_lo"])
        target_hi = float(image["col_target_hi"])
        buffer_lo = float(image["col_buffer_lo"])
        buffer_hi = float(image["col_buffer_hi"])
    except (KeyError, TypeError, ValueError):
        return MANUAL_REVIEW
    if target_hi > target_lo and target_lo <= x_px <= target_hi:
        return TARGET
    if buffer_hi > buffer_lo and buffer_lo <= x_px <= buffer_hi:
        return MANUAL_REVIEW
    return NEIGHBOUR


def _inside_target_access_envelope(
    image: Dict[str, Any], x_px: float, boundaries: Optional[Dict[str, Any]] = None
) -> bool:
    """Allow for a mailbox beside a detached-house facade or driveway.

    The projected cadastral frontage remains the anchor.  Its buffered pixel
    interval is widened only for the conservative single-mailbox fallback;
    architectural facade seams are deliberately not used as parcel limits.
    """
    try:
        projected = boundaries if boundaries and boundaries.get("source") == "parcel_guided_projection" else image
        target_lo = float(projected.get("frontage_left", projected.get("col_target_lo")))
        target_hi = float(projected.get("frontage_right", projected.get("col_target_hi")))
        buffer_lo = float(projected.get("left", projected.get("col_buffer_lo")))
        buffer_hi = float(projected.get("right", projected.get("col_buffer_hi")))
        width = float(image["image_width"])
    except (KeyError, TypeError, ValueError):
        return False
    if target_hi <= target_lo or buffer_hi <= buffer_lo or width <= 0.0:
        return False
    target_span = target_hi - target_lo
    margin = min(width * 0.12, max(width * 0.025, target_span * 0.35))
    return buffer_lo - margin <= float(x_px) <= buffer_hi + margin


def _target_boundary_uncertain(
    image: Dict[str, Any], x1: float, x2: float, boundaries: Dict[str, Any]
) -> bool:
    """Return true when a mailbox touches the padded target ownership zone."""
    try:
        width = float(image["image_width"])
        projected = boundaries if boundaries.get("source") == "parcel_guided_projection" else image
        target_lo = float(projected.get("frontage_left", projected.get("col_target_lo")))
        target_hi = float(projected.get("frontage_right", projected.get("col_target_hi")))
        buffer_lo = float(projected.get("left", projected.get("col_buffer_lo")))
        buffer_hi = float(projected.get("right", projected.get("col_buffer_hi")))
    except (KeyError, TypeError, ValueError):
        return True
    if width <= 0.0 or target_hi <= target_lo or buffer_hi <= buffer_lo:
        return True
    margin = min(width * 0.10, max(width * 0.04, (target_hi - target_lo) * 0.10))
    if x2 >= buffer_lo - margin and x1 <= buffer_hi + margin:
        return True
    try:
        left = float(boundaries["left"])
        right = float(boundaries["right"])
        confident = (
            float(boundaries.get("left_confidence", 1.0) or 0.0) >= 0.20
            and float(boundaries.get("right_confidence", 1.0) or 0.0) >= 0.20
        )
        if confident and x2 >= left - margin and x1 <= right + margin:
            return True
    except (KeyError, TypeError, ValueError):
        pass
    return False


def _neighbour_ray_consensus(evidence: Sequence[Dict[str, Any]], buffer_m: float) -> bool:
    """Require the same adjacent parcel and safe distance from its endpoints."""
    hits = [item.get("hit") for item in evidence]
    if any(not hit or hit.get("owner") != NEIGHBOUR for hit in hits):
        return False
    parcel_ids = {str(hit.get("parcel_id")) for hit in hits}
    if len(parcel_ids) != 1:
        return False
    clearance_required = max(1.0, float(buffer_m or 0.0))
    for hit in hits:
        start, end = hit.get("start"), hit.get("end")
        if not start or not end:
            return False
        length = math.hypot(float(end[0]) - float(start[0]), float(end[1]) - float(start[1]))
        along = float(hit.get("along_m") or 0.0)
        if min(along, length - along) < clearance_required:
            return False
    return True


def _valid_front_door_box(box: Dict[str, Any], picture: Image.Image) -> bool:
    """Reject low, square Grounding-DINO boxes that commonly cover cars."""
    try:
        x1 = float(box.get("xmin") if box.get("xmin") is not None else box.get("x1"))
        y1 = float(box.get("ymin") if box.get("ymin") is not None else box.get("y1"))
        x2 = float(box.get("xmax") if box.get("xmax") is not None else box.get("x2"))
        y2 = float(box.get("ymax") if box.get("ymax") is not None else box.get("y2"))
    except (TypeError, ValueError):
        return False
    box_width = x2 - x1
    box_height = y2 - y1
    if box_width <= 0.0 or box_height <= 0.0:
        return False
    # A front door/entrance is vertically oriented. Cars and other foreground
    # objects responsible for the observed false fallback are usually square
    # or horizontal and sit unusually low in the frame.
    return box_height >= box_width * 1.15 and y2 <= picture.height * 0.94


def _load_mask2former_building_mask(image: Image.Image) -> Optional[Any]:
    """Return Mapillary building/wall context when the checkpoint is available."""
    global _MASK2FORMER, _MASK2FORMER_PROCESSOR, _MASK2FORMER_MODEL_ID, _MASK2FORMER_UNAVAILABLE
    if _MASK2FORMER_UNAVAILABLE:
        return None
    try:
        import numpy as np
        import torch
        from transformers import AutoImageProcessor, Mask2FormerForUniversalSegmentation

        if _MASK2FORMER is None:
            allow_download = os.environ.get("MAILBOX_AGENT6_ALLOW_MODEL_DOWNLOAD", "0") == "1"
            model_ids = [
                "facebook/mask2former-swin-large-mapillary-vistas-semantic",
                # An already-cached Cityscapes checkpoint still supplies useful
                # building/wall context when the larger Mapillary model is not
                # available. It never replaces the cadastral ownership test.
                "facebook/mask2former-swin-tiny-cityscapes-semantic",
            ]
            load_errors = []
            for model_id in model_ids:
                try:
                    may_download = allow_download and "mapillary" in model_id
                    processor = AutoImageProcessor.from_pretrained(
                        model_id, local_files_only=not may_download
                    )
                    model = Mask2FormerForUniversalSegmentation.from_pretrained(
                        model_id, local_files_only=not may_download
                    )
                    _MASK2FORMER_PROCESSOR = processor
                    _MASK2FORMER = model
                    _MASK2FORMER_MODEL_ID = model_id
                    break
                except Exception as model_error:
                    load_errors.append(f"{model_id}: {model_error}")
            if _MASK2FORMER is None:
                raise RuntimeError("; ".join(load_errors))
            device = "cuda" if torch.cuda.is_available() else "cpu"
            _MASK2FORMER = _MASK2FORMER.to(device).eval()
        device = next(_MASK2FORMER.parameters()).device
        inputs = _MASK2FORMER_PROCESSOR(images=image, return_tensors="pt")
        inputs = {key: value.to(device) for key, value in inputs.items()}
        with torch.inference_mode():
            outputs = _MASK2FORMER(**inputs)
        segmentation = _MASK2FORMER_PROCESSOR.post_process_semantic_segmentation(
            outputs, target_sizes=[(image.height, image.width)]
        )[0]
        labels = _MASK2FORMER.config.id2label
        ids = [
            int(label_id)
            for label_id, label in labels.items()
            if any(name in str(label).lower() for name in ("building", "wall"))
        ]
        mask = np.zeros((image.height, image.width), dtype=np.uint8)
        array = segmentation.detach().cpu().numpy()
        for label_id in ids:
            mask[array == label_id] = 1
        return mask if mask.any() else None
    except Exception as exc:
        logger.info("Mask2Former context unavailable; using gradient seams: %s", exc)
        _MASK2FORMER_UNAVAILABLE = True
        return None


def detect_parcel_guided_boundaries(
    site: Dict[str, Any], image: Dict[str, Any]
) -> Dict[str, Any]:
    """Project parcel geometry after a conservative image-based yaw calibration."""
    parcel_wkt = site.get("parcel_geom_wkt")
    building_wkt = site.get("building_geom_wkt")
    if not parcel_wkt or not building_wkt:
        return {
            "status": "missing_parcel_or_building",
            "source": "parcel_guided_projection",
            "left": None,
            "right": None,
        }
    try:
        parcel, target_building, frontage = _parcel_guided_target_geometry(site, image)
        if parcel is None or target_building is None:
            raise ValueError("parcel-guided target geometry is unavailable")

        camera = Point(float(image["cam_x"]), float(image["cam_y"]))
        target = target_building.centroid
        facade = _camera_facing_edge(target_building, camera, target)
        if facade is None:
            raise ValueError("target building has no projectable facade edge")
        if frontage is None:
            raise ValueError("target parcel has no projectable frontage edge")

        width = float(image.get("image_width") or 1.0)
        height = float(image.get("image_height") or 1.0)
        source_path = Path(str(image.get("enhanced_file_path") or image.get("file_path") or ""))
        picture: Optional[Image.Image] = None
        building_mask = None
        mask_model = None
        if source_path.is_file():
            with Image.open(source_path) as opened:
                picture = opened.convert("RGB")
            building_mask = _load_mask2former_building_mask(picture)

        initial_facade_columns = [_project_world_column(image, point) for point in facade.coords]
        if any(value is None for value in initial_facade_columns):
            raise ValueError("target facade falls outside the image FOV")
        initial_facade_left, initial_facade_right = sorted(
            float(value) for value in initial_facade_columns
        )
        calibration = (
            _estimate_horizontal_calibration(
                image,
                initial_facade_left,
                initial_facade_right,
                picture,
                building_mask,
            )
            if picture is not None
            else {"status": "unverified", "reason": "missing_image", "reliable": False}
        )
        projection_image = dict(image)
        if calibration.get("reliable") and calibration.get("calibrated_heading") is not None:
            projection_image["heading"] = float(calibration["calibrated_heading"])

        facade_columns = [_project_world_column(projection_image, point) for point in facade.coords]
        frontage_columns = [_project_world_column(projection_image, point) for point in frontage.coords]
        if any(value is None for value in facade_columns + frontage_columns):
            raise ValueError("target facade/frontage falls outside the image FOV")
        facade_left, facade_right = sorted(float(value) for value in facade_columns)
        frontage_left, frontage_right = sorted(float(value) for value in frontage_columns)

        base_uncertainty = max(1.0, float(image.get("buffer_m") or 1.0))
        if calibration.get("reliable"):
            confidence = min(
                1.0,
                max(0.0, (float(calibration.get("edge_strength") or 1.0) - 1.0) / 2.0),
            )
            uncertainty_m = max(base_uncertainty, 1.0 + (1.0 - confidence) * 0.75)
        else:
            # A single uncalibrated panorama cannot support a precise hard
            # boundary. Widen the safe zone and disable automatic rejection.
            uncertainty_m = max(base_uncertainty, 2.5)
        padded_frontage = _extend_edge(frontage, uncertainty_m)
        padded_columns = [_project_world_column(projection_image, point) for point in padded_frontage.coords]
        if any(value is None for value in padded_columns):
            raise ValueError("padded target frontage falls outside the image FOV")
        left, right = sorted(float(value) for value in padded_columns)
        if right - left < max(2.0, width * 0.005):
            raise ValueError("projected target frontage is too narrow")

        house_bbox = {
            "x1": facade_left,
            "y1": height * 0.10,
            "x2": facade_right,
            "y2": height * 0.92,
            "source": "calibrated_projected_facade_with_default_vertical_extent",
        }
        if building_mask is not None and picture is not None:
            import numpy as np

            x0 = max(0, int(math.floor(facade_left)))
            x1 = min(picture.width, int(math.ceil(facade_right)) + 1)
            ys, _ = np.where(building_mask[:, x0:x1] > 0)
            if ys.size:
                house_bbox["y1"] = float(max(0, int(np.percentile(ys, 2))))
                house_bbox["y2"] = float(min(picture.height, int(np.percentile(ys, 98)) + 1))
                house_bbox["source"] = "calibrated_projected_facade_with_semantic_vertical_extent"
                mask_model = _MASK2FORMER_MODEL_ID

        return {
            "status": "ok",
            "source": "parcel_guided_projection",
            "left": left,
            "right": right,
            "left_confidence": float(calibration.get("edge_strength") or 0.0),
            "right_confidence": float(calibration.get("edge_strength") or 0.0),
            "facade_left": facade_left,
            "facade_right": facade_right,
            "frontage_left": frontage_left,
            "frontage_right": frontage_right,
            "uncertainty_m": uncertainty_m,
            "projection_reliable": bool(calibration.get("reliable")),
            "calibration": calibration,
            "effective_heading": float(projection_image["heading"]),
            "target_building_wkt": target_building.wkt,
            "visible_facade": [list(point) for point in facade.coords],
            "target_frontage": [list(point) for point in frontage.coords],
            "house_bbox": house_bbox,
            "segmentation_model": mask_model,
        }
    except Exception as exc:
        logger.warning("%s: parcel-guided projection failed: %s", site.get("site_id"), exc)
        return {
            "status": "failed",
            "source": "parcel_guided_projection",
            "left": None,
            "right": None,
            "error": str(exc),
        }


def detect_vertical_boundaries(image: Dict[str, Any]) -> Dict[str, Any]:
    """Refine projected property sides using vertical architectural seams."""
    source = Path(str(image.get("enhanced_file_path") or image.get("file_path") or ""))
    if not source.is_file():
        return {"status": "missing_image", "left": None, "right": None}
    try:
        import cv2
        import numpy as np

        with Image.open(source) as opened:
            rgb = opened.convert("RGB")
        array = np.asarray(rgb)
        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        height, width = gray.shape
        building_mask = _load_mask2former_building_mask(rgb)
        y0, y1 = int(height * 0.15), int(height * 0.88)
        gradient = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        roi = gradient[y0:y1]
        if building_mask is not None:
            mask_roi = building_mask[y0:y1].astype(bool)
            roi = np.where(mask_roi, roi, roi * 0.25)
        score = roi.mean(axis=0)
        score = cv2.GaussianBlur(score.reshape(1, -1), (0, 1), sigmaX=5).reshape(-1)
        expected_left = float(image.get("col_target_lo") or 0.0)
        expected_right = float(image.get("col_target_hi") or width)
        projected_span = max(1.0, expected_right - expected_left)
        radius = max(15, min(int(width * 0.08), int(projected_span * 0.35)))

        def refine(expected: float) -> Tuple[float, float]:
            lo = max(0, int(expected) - radius)
            hi = min(width, int(expected) + radius + 1)
            if hi <= lo:
                return expected, 0.0
            local = score[lo:hi]
            index = int(np.argmax(local)) + lo
            baseline = float(np.median(local)) + 1e-6
            confidence = min(1.0, float(score[index]) / (baseline * 4.0))
            return float(index), confidence

        left, left_confidence = refine(expected_left)
        right, right_confidence = refine(expected_right)
        if left >= right:
            left, right = expected_left, expected_right
            left_confidence = right_confidence = 0.0
        return {
            "status": "ok",
            "left": left,
            "right": right,
            "left_confidence": left_confidence,
            "right_confidence": right_confidence,
            "mask2former_used": building_mask is not None,
            "mask2former_model": _MASK2FORMER_MODEL_ID if building_mask is not None else None,
        }
    except Exception as exc:
        logger.warning("Vertical boundary detection failed: %s", exc)
        return {"status": "failed", "left": None, "right": None, "error": str(exc)}


def detect_target_doors(image: Dict[str, Any], boundaries: Dict[str, Any]) -> List[Dict[str, float]]:
    """Detect front doors for ambiguous-ray fallback using cached Grounding DINO."""
    global _DOOR_DETECTOR, _DOOR_DETECTOR_UNAVAILABLE
    if _DOOR_DETECTOR_UNAVAILABLE:
        return []
    source = Path(str(image.get("enhanced_file_path") or image.get("file_path") or ""))
    if not source.is_file():
        return []
    try:
        import torch
        from transformers import pipeline

        if _DOOR_DETECTOR is None:
            model_id = "IDEA-Research/grounding-dino-base"
            device = 0 if torch.cuda.is_available() else -1
            _DOOR_DETECTOR = pipeline(
                "zero-shot-object-detection",
                model=model_id,
                device=device,
                local_files_only=True,
            )
        with Image.open(source) as opened:
            picture = opened.convert("RGB")
        predictions = _DOOR_DETECTOR(
            picture,
            candidate_labels=["front door", "house entrance"],
            threshold=0.25,
        )
        left = float(boundaries.get("left") if boundaries.get("left") is not None else 0.0)
        right = float(
            boundaries.get("right")
            if boundaries.get("right") is not None
            else image.get("image_width") or picture.width
        )
        doors = []
        for prediction in predictions:
            box = prediction.get("box") or {}
            if not _valid_front_door_box(box, picture):
                continue
            x1 = float(box.get("xmin") or box.get("x1") or 0.0)
            x2 = float(box.get("xmax") or box.get("x2") or 0.0)
            center = (x1 + x2) / 2.0
            if left <= center <= right:
                doors.append(
                    {
                        "x1": x1,
                        "y1": float(box.get("ymin") or box.get("y1") or 0.0),
                        "x2": x2,
                        "y2": float(box.get("ymax") or box.get("y2") or 0.0),
                        "cx": center,
                        "score": float(prediction.get("score") or 0.0),
                    }
                )
        return doors
    except Exception as exc:
        logger.info("Grounding-DINO door fallback unavailable: %s", exc)
        _DOOR_DETECTOR_UNAVAILABLE = True
        return []


def _three_ray_owners(
    detection: Dict[str, Any],
    image: Dict[str, Any],
    frontages: Sequence[Dict[str, Any]],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    box = detection.get("box") or {}
    x1 = float(box.get("x1") or 0.0)
    x2 = float(box.get("x2") or x1)
    center = (x1 + x2) / 2.0
    inset = min(max((x2 - x1) * 0.20, 1.0), 5.0)
    columns = [x1 + inset, center, x2 - inset]
    evidence = [ray_frontage_evidence(image, column, frontages) for column in columns]
    return [item["owner"] for item in evidence], evidence


def _compact_group(detections: Sequence[Dict[str, Any]], width: float) -> bool:
    if len(detections) < 2:
        return False
    centers = []
    for detection in detections:
        box = detection.get("box") or {}
        centers.append((float(box.get("x1") or 0.0) + float(box.get("x2") or 0.0)) / 2.0)
    return max(centers) - min(centers) <= max(40.0, width * 0.12)


def classify_image_detections(
    detections: Sequence[Dict[str, Any]],
    image: Dict[str, Any],
    frontages: Sequence[Dict[str, Any]],
    boundaries: Optional[Dict[str, Any]] = None,
    doors: Optional[Sequence[Dict[str, float]]] = None,
) -> List[Dict[str, Any]]:
    """Assign detections using agreement between GIS rays and image boundaries."""
    boundaries = boundaries or {}
    doors = list(doors or [])
    width = float(image.get("image_width") or 1.0)
    has_neighbours = any(item.get("owner") == NEIGHBOUR for item in frontages)
    compact = _compact_group(detections, width)
    projection_reliable = bool(boundaries.get("projection_reliable", True))

    preliminary: List[Dict[str, Any]] = []
    for detection in detections:
        box = detection.get("box") or {}
        x1 = float(box.get("x1") or 0.0)
        x2 = float(box.get("x2") or x1)
        center_x = (x1 + x2) / 2.0
        base_y = float(box.get("y2") or 0.0)
        owners, ray_evidence = _three_ray_owners(detection, image, frontages)
        center_ray_owner = owners[1]
        unanimous = len(set(owners)) == 1
        column_state = _column_state(image, center_x, boundaries)
        refined_left = boundaries.get("left")
        refined_right = boundaries.get("right")
        boundary_confident = (
            float(boundaries.get("left_confidence", 1.0) or 0.0) >= 0.20
            and float(boundaries.get("right_confidence", 1.0) or 0.0) >= 0.20
        )
        in_refined_slice = (
            boundary_confident
            and
            refined_left is not None
            and refined_right is not None
            and float(refined_left) <= center_x <= float(refined_right)
        )
        boundary_uncertain = _target_boundary_uncertain(image, x1, x2, boundaries)
        same_safe_neighbour = _neighbour_ray_consensus(
            ray_evidence,
            float(boundaries.get("uncertainty_m") or image.get("buffer_m") or 1.0),
        )

        ownership = MANUAL_REVIEW
        reason = "gis_and_image_evidence_unresolved"
        # Cadastral target frontage is primary. A facade seam can describe the
        # visible building, but it must never veto a mailbox in the same
        # parcel's garden or driveway.
        if unanimous and center_ray_owner == TARGET:
            ownership, reason = TARGET, "three_rays_intersect_target_frontage"
        elif (
            unanimous
            and center_ray_owner == NEIGHBOUR
            and column_state == NEIGHBOUR
            and has_neighbours
            and not in_refined_slice
            and not boundary_uncertain
            and same_safe_neighbour
            and projection_reliable
        ):
            ownership, reason = NEIGHBOUR, "three_rays_intersect_adjacent_frontage"
        elif center_ray_owner == TARGET and in_refined_slice:
            ownership, reason = TARGET, "target_ray_confirmed_by_architectural_slice"
        elif center_ray_owner == TARGET:
            ownership, reason = TARGET, "target_frontage_overrides_facade_slice"
        elif center_ray_owner == NEIGHBOUR and in_refined_slice:
            ownership, reason = MANUAL_REVIEW, "gis_conflicts_with_architectural_slice"

        # For a lone mailbox, retain an unresolved detection that is close to
        # the target parcel/access envelope. This covers detached houses where
        # the mailbox sits beside a wide garden or driveway. A unanimous
        # adjacent-parcel result remains rejectable and is never overridden.
        if (
            len(detections) == 1
            and ownership == MANUAL_REVIEW
            and not doors
            and not (unanimous and center_ray_owner == NEIGHBOUR)
            and _inside_target_access_envelope(image, center_x, boundaries)
        ):
            ownership, reason = TARGET, "single_mailbox_within_target_access_envelope"

        preliminary.append(
            {
                **detection,
                "ownership": ownership,
                "rejection_reason": reason,
                "ground_xy": (
                    [ray_evidence[1]["hit"]["x"], ray_evidence[1]["hit"]["y"]]
                    if ray_evidence[1].get("hit")
                    else None
                ),
                "mailbox_base_px": [center_x, base_y],
                "agent6_evidence": {
                    "ray_owners": owners,
                    "center_bearing": ray_evidence[1]["bearing"],
                    "center_hit": ray_evidence[1].get("hit"),
                    "column_state": column_state,
                    "inside_architectural_slice": in_refined_slice,
                    "architectural_slice_confident": boundary_confident,
                    "target_boundary_uncertain": boundary_uncertain,
                    "same_safe_neighbour_parcel": same_safe_neighbour,
                    "adjacent_frontages_available": has_neighbours,
                    "projection_reliable": projection_reliable,
                },
            }
        )

    # A compact roadside bank follows the ownership of its centroid only when
    # all members were otherwise target/manual; never pull a neighbour box in.
    if compact and preliminary:
        non_manual = {item["ownership"] for item in preliminary if item["ownership"] != MANUAL_REVIEW}
        if non_manual == {TARGET}:
            for item in preliminary:
                if item["ownership"] == MANUAL_REVIEW:
                    item["ownership"] = TARGET
                    item["rejection_reason"] = "compact_bank_with_target_frontage_consensus"

    # Door proximity can promote one ambiguous box, but can never reject.
    ambiguous = [item for item in preliminary if item["ownership"] == MANUAL_REVIEW]
    if doors and ambiguous:
        candidates = []
        for item in ambiguous:
            center_x = float(item["mailbox_base_px"][0])
            nearest = min(abs(center_x - float(door["cx"])) for door in doors)
            candidates.append((nearest, item))
        candidates.sort(key=lambda pair: pair[0])
        if candidates[0][0] <= width * 0.12:
            # Require a useful separation from the next mailbox when present.
            if len(candidates) == 1 or candidates[1][0] - candidates[0][0] >= width * 0.04:
                candidates[0][1]["ownership"] = TARGET
                candidates[0][1]["rejection_reason"] = "nearest_to_target_front_door_fallback"

    return preliminary


def _match_image(assign_site: Dict[str, Any], detection: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    detection_file = str(detection.get("image_file") or "")
    images = assign_site.get("images") or []
    for image in images:
        if detection_file and detection_file in {
            str(image.get("enhanced_file_path") or ""),
            str(image.get("file_path") or ""),
        }:
            return image
    pano_id = detection.get("pano_id")
    return next((image for image in images if image.get("pano_id") == pano_id), None)


def _geometry_cache_signature(site: Dict[str, Any], image: Dict[str, Any]) -> str:
    """Fingerprint every input that can change the ownership projection."""
    source = Path(str(image.get("enhanced_file_path") or image.get("file_path") or ""))
    try:
        stat = source.stat()
        source_state: Any = [str(source.resolve()), stat.st_size, stat.st_mtime_ns]
    except OSError:
        source_state = [str(source), None, None]
    payload = {
        "version": GEOMETRY_CACHE_VERSION,
        "site": {
            key: site.get(key)
            for key in ("site_id", "parcel_id", "x", "y", "parcel_geom_wkt", "building_geom_wkt")
        },
        "image": {
            key: image.get(key)
            for key in (
                "pano_id",
                "cam_x",
                "cam_y",
                "heading",
                "fov",
                "image_width",
                "image_height",
                "buffer_m",
                "left_corner",
                "right_corner",
            )
        },
        "source": source_state,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_geometry_cache(path: Path, expected_signature: str) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            payload.get("version") == GEOMETRY_CACHE_VERSION
            and payload.get("input_signature") == expected_signature
        ):
            return payload
    except (OSError, ValueError):
        pass
    return None


def _annotate(
    image: Dict[str, Any],
    detections: Sequence[Dict[str, Any]],
    boundaries: Dict[str, Any],
    doors: Sequence[Dict[str, float]],
    output_path: Path,
) -> Optional[str]:
    source = Path(str(image.get("enhanced_file_path") or image.get("file_path") or ""))
    if not source.is_file():
        return None
    with Image.open(source) as opened:
        canvas = opened.convert("RGB")
    draw = ImageDraw.Draw(canvas, "RGBA")
    height = canvas.height
    for key, color in (("left", (59, 130, 246, 220)), ("right", (59, 130, 246, 220))):
        if boundaries.get(key) is not None:
            x = float(boundaries[key])
            draw.line((x, 0, x, height), fill=color, width=3)
    house_bbox = boundaries.get("house_bbox") or {}
    if all(house_bbox.get(key) is not None for key in ("x1", "y1", "x2", "y2")):
        draw.rectangle(
            tuple(float(house_bbox[key]) for key in ("x1", "y1", "x2", "y2")),
            outline=(6, 182, 212, 235),
            width=3,
        )
    for key in ("facade_left", "facade_right"):
        if boundaries.get(key) is not None:
            x = float(boundaries[key])
            draw.line((x, 0, x, height), fill=(6, 182, 212, 150), width=2)
    for door in doors:
        draw.rectangle(
            (door["x1"], door["y1"], door["x2"], door["y2"]),
            outline=(168, 85, 247, 255),
            width=3,
        )
    colors = {
        TARGET: (34, 197, 94, 255),
        NEIGHBOUR: (239, 68, 68, 255),
        MANUAL_REVIEW: (245, 158, 11, 255),
    }
    for detection in detections:
        box = detection.get("box") or {}
        try:
            coordinates = tuple(float(box[key]) for key in ("x1", "y1", "x2", "y2"))
        except (KeyError, TypeError, ValueError):
            continue
        ownership = str(detection.get("ownership") or MANUAL_REVIEW)
        color = colors.get(ownership, colors[MANUAL_REVIEW])
        draw.rectangle(coordinates, outline=color, width=4)
        base_x = (coordinates[0] + coordinates[2]) / 2.0
        draw.ellipse((base_x - 4, coordinates[3] - 4, base_x + 4, coordinates[3] + 4), fill=color)
        draw.text((coordinates[0], max(0.0, coordinates[1] - 14.0)), ownership, fill=color)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="JPEG", quality=92)
    return str(output_path.resolve())


def process_site(
    raw_site: Dict[str, Any],
    assign_site: Dict[str, Any],
    parcel_site: Dict[str, Any],
    output_dir: Path,
    *,
    architectural_cues_enabled: bool,
    use_cache: bool,
) -> Dict[str, Any]:
    site_id = str(raw_site.get("site_id"))
    raw_detections = list(raw_site.get("all_detections") or raw_site.get("mailboxes") or [])
    grouped: Dict[str, Tuple[Dict[str, Any], List[Dict[str, Any]]]] = {}
    classified: List[Dict[str, Any]] = []
    for detection in raw_detections:
        image = _match_image(assign_site, detection)
        if image is None:
            classified.append(
                {**detection, "ownership": MANUAL_REVIEW, "rejection_reason": "missing_image_geometry"}
            )
            continue
        key = str(image.get("enhanced_file_path") or image.get("file_path") or image.get("pano_id"))
        grouped.setdefault(key, (image, []))[1].append(detection)

    review_images: List[str] = []
    geometry_records: List[Dict[str, Any]] = []
    for index, (_, (image, detections)) in enumerate(grouped.items()):
        review_dir = output_dir / "agent6_review" / site_id
        cache_path = review_dir / f"geometry_{index}.json"
        cache_signature = _geometry_cache_signature(parcel_site, image)
        cached = _read_geometry_cache(cache_path, cache_signature) if use_cache else None
        cached_frontages = cached.get("frontages") if cached else None
        frontages, frontage_status = build_frontages(
            parcel_site,
            image,
            use_api_cache=use_cache,
            cached_neighbours=cached_frontages,
        )
        boundaries = (
            cached.get("boundaries")
            if cached and cached.get("boundaries")
            else detect_parcel_guided_boundaries(parcel_site, image)
            if architectural_cues_enabled
            else {"status": "disabled", "left": None, "right": None}
        )
        doors = (
            cached.get("doors")
            if cached and isinstance(cached.get("doors"), list)
            else detect_target_doors(image, boundaries)
            if architectural_cues_enabled
            else []
        )
        geometry_record = {
            "version": GEOMETRY_CACHE_VERSION,
            "input_signature": cache_signature,
            "method": "image_calibrated_parcel_projection_with_cadastral_rays",
            "frontage_status": frontage_status,
            "frontages": frontages,
            "boundaries": boundaries,
            "doors": doors,
        }
        review_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(geometry_record, indent=2, ensure_ascii=False), encoding="utf-8")
        geometry_records.append(geometry_record)

        effective_image = dict(image)
        if boundaries.get("effective_heading") is not None:
            effective_image["heading"] = float(boundaries["effective_heading"])
        if boundaries.get("uncertainty_m") is not None:
            effective_image["buffer_m"] = float(boundaries["uncertainty_m"])
        image_results = classify_image_detections(
            detections,
            effective_image,
            frontages,
            boundaries=boundaries,
            doors=doors,
        )
        classified.extend(image_results)
        annotated = _annotate(
            image,
            image_results,
            boundaries,
            doors,
            review_dir / f"rejection_{index}.jpg",
        )
        if annotated:
            review_images.append(annotated)

    targets = [item for item in classified if item.get("ownership") == TARGET]
    neighbours = [item for item in classified if item.get("ownership") == NEIGHBOUR]
    ambiguous = [item for item in classified if item.get("ownership") == MANUAL_REVIEW]
    flags = list(raw_site.get("flags") or [])
    upstream_manual = bool(raw_site.get("manual_review")) or "MANUAL_REVIEW" in flags
    if ambiguous:
        flags.append("MANUAL_REVIEW")
    manual_review = bool(ambiguous) or upstream_manual
    manual_review_count = max(
        len(ambiguous), len(classified) if upstream_manual else 0
    )
    return {
        "site_id": site_id,
        "postcode": raw_site.get("postcode"),
        "street_norm": raw_site.get("street_norm"),
        "hnr_base": raw_site.get("hnr_base"),
        "x": raw_site.get("x"),
        "y": raw_site.get("y"),
        "parcel_id": raw_site.get("parcel_id"),
        "detection_fov": raw_site.get("detection_fov"),
        "fallback_used": bool(raw_site.get("fallback_used")),
        "status": MANUAL_REVIEW if manual_review else raw_site.get("status", "OK"),
        "flags": sorted(set(flags)),
        "raw_vision_count": len(raw_detections),
        "vision_count": len(targets),
        "rejected_count": len(neighbours),
        "manual_review_count": manual_review_count,
        "confidence": "Low" if manual_review else ("High" if targets else "Medium"),
        "mailboxes": targets,
        "neighbours_rejected": neighbours,
        "ambiguous": ambiguous,
        "all_detections": classified,
        "manual_review": manual_review,
        "manual_review_reason": raw_site.get("manual_review_reason"),
        "remarks": raw_site.get("remarks"),
        "ownership_geometry": geometry_records,
        "review_images": review_images,
    }


def run(
    detect_json: Path,
    assign_json: Path,
    site_json: Path,
    out_dir: Path,
    *,
    vision_enabled: bool = True,
    use_cache: bool = True,
    max_sites: Optional[int] = None,
    cancel_check: Optional[Callable[[], None]] = None,
    progress_callback: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
) -> List[Dict[str, Any]]:
    """Run geometry-first rejection; vision_enabled now controls local cues only."""
    raw_sites = json.loads(Path(detect_json).read_text(encoding="utf-8"))
    assign_sites = json.loads(Path(assign_json).read_text(encoding="utf-8"))
    parcel_sites = json.loads(Path(site_json).read_text(encoding="utf-8"))
    if max_sites is not None:
        raw_sites = raw_sites[:max_sites]
    assign_by_id = {str(site["site_id"]): site for site in assign_sites}
    parcel_by_id = {str(site["site_id"]): site for site in parcel_sites}
    output_dir = Path(out_dir)
    results: List[Dict[str, Any]] = []
    for index, raw_site in enumerate(raw_sites, 1):
        if cancel_check:
            cancel_check()
        site_id = str(raw_site["site_id"])
        result = process_site(
            raw_site,
            assign_by_id.get(site_id, {"site_id": site_id, "images": []}),
            parcel_by_id.get(site_id, {"site_id": site_id}),
            output_dir,
            architectural_cues_enabled=vision_enabled,
            use_cache=use_cache,
        )
        results.append(result)
        if progress_callback:
            progress_callback(index, len(raw_sites), {"site_id": site_id})
        print(
            f"  [{index:>5}/{len(raw_sites)}] {site_id} status={result['status']:16} "
            f"kept={result['vision_count']} rejected={result['rejected_count']} "
            f"review={result['manual_review_count']}"
        )
    output_path = output_dir / "agent6_reject.json"
    output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent 6 - geometry-first neighbour rejection")
    parser.add_argument("--detect-json", required=True, help="agent5_detect.json")
    parser.add_argument("--assign-json", required=True, help="agent4_assign.json")
    parser.add_argument("--site-json", required=True, help="agent2_site.json")
    parser.add_argument("--out-dir", "-o", default=".")
    parser.add_argument("--max-sites", type=int, default=None)
    parser.add_argument("--no-vision", action="store_true", help="Disable local architectural cues")
    parser.add_argument("--no-cache", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    run(
        detect_json=Path(args.detect_json).resolve(),
        assign_json=Path(args.assign_json).resolve(),
        site_json=Path(args.site_json).resolve(),
        out_dir=Path(args.out_dir).resolve(),
        vision_enabled=not args.no_vision,
        use_cache=not args.no_cache,
        max_sites=args.max_sites,
    )


if __name__ == "__main__":
    main()
