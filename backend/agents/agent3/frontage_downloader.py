"""Best-panorama Street View acquisition used by Agent 3.

For each address this implementation probes the nearby road, ranks front-facing
panoramas by visible facade width and frontality, selects one panorama, and
downloads matching FOV-90 and FOV-110 images. No detection happens here.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import geopandas as gpd
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from api_cache import cached_urlopen_bytes


logger = logging.getLogger("agent3_best_pano")
IMAGE_SIZE = "640x640"
FOVS = (90.0, 110.0)
SEARCH_RADII_M = (20, 40, 60, 100)
SELECTION_METHOD = "facade_apparent_width_times_frontality"
PANORAMA_SELECTION_VERSION = 7


def _implementation():
    try:
        from . import download_exact_streetview
    except ImportError:
        import download_exact_streetview
    return download_exact_streetview


def address_from_agent1(
    site: Dict[str, Any], members: Optional[Sequence[Dict[str, Any]]] = None
) -> str:
    """Build a Belgian address, preferring Agent 1's original fields."""
    member = members[0] if members else {}
    street = member.get("aansluitadres_straatnaam") or member.get("street") or site.get("street_norm") or ""
    number = member.get("aansluitadres_huisnummer") or member.get("huisnummer") or site.get("hnr_base") or ""
    postcode = member.get("aansluitadres_postcode") or member.get("postcode") or site.get("postcode") or ""
    municipality = (
        member.get("aansluitadres_gemeente")
        or member.get("aansluitadres_plaatsnaam")
        or member.get("municipality")
        or ""
    )
    locality = " ".join(str(value).strip() for value in (postcode, municipality) if value)
    address = f"{str(street).strip()} {str(number).strip()}, {locality}, Belgium"
    return " ".join(address.replace(", ,", ",").split()).strip(" ,")


def road_probe_points(
    roads_gdf: gpd.GeoDataFrame,
    frontage_midpoint: Point,
    half_span_m: float = 30.0,
    spacing_m: float = 10.0,
) -> List[Point]:
    if roads_gdf is None or roads_gdf.empty:
        return []
    road_lines: List[LineString] = []
    for geometry in roads_gdf.geometry:
        if geometry is None or geometry.is_empty:
            continue
        if geometry.geom_type == "LineString":
            road_lines.append(geometry)
        elif geometry.geom_type == "MultiLineString":
            road_lines.extend(line for line in geometry.geoms if not line.is_empty and line.length > 0)
    if not road_lines:
        return []
    road = min(road_lines, key=lambda line: line.distance(frontage_midpoint))
    closest = nearest_points(road, frontage_midpoint)[0]
    center_distance = road.project(closest)
    return [
        road.interpolate(min(max(center_distance + offset, 0.0), road.length))
        for offset in range(-int(half_span_m), int(half_span_m) + 1, max(1, int(spacing_m)))
    ]


def query_panorama(
    lat: float,
    lng: float,
    radius_m: int,
    api_key: str,
    *,
    use_api_cache: bool,
) -> Optional[Dict[str, Any]]:
    params = urllib.parse.urlencode(
        {"location": f"{lat:.7f},{lng:.7f}", "radius": radius_m, "source": "outdoor", "key": api_key}
    )
    raw = cached_urlopen_bytes(
        f"https://maps.googleapis.com/maps/api/streetview/metadata?{params}",
        service="google-streetview-metadata",
        timeout=20,
        headers={"User-Agent": "MailboxPipeline/Agent3BestPano"},
        use_cache=use_api_cache,
    )
    metadata = json.loads(raw.decode("utf-8"))
    if metadata.get("status") == "ZERO_RESULTS":
        return None
    if metadata.get("status") != "OK":
        raise RuntimeError(
            "Street View metadata request failed: "
            f"{metadata.get('status')} {metadata.get('error_message', '')}".strip()
        )
    if not metadata.get("pano_id") or not metadata.get("location"):
        raise RuntimeError("Street View metadata response is missing pano_id or location")
    return metadata


def discover_panoramas(
    target_lat: float,
    target_lng: float,
    roads_gdf: gpd.GeoDataFrame,
    frontage_midpoint: Point,
    api_key: str,
    *,
    use_api_cache: bool,
) -> List[Dict[str, Any]]:
    pipeline = _implementation()
    probes: List[Tuple[float, float, int]] = [
        (target_lat, target_lng, radius) for radius in SEARCH_RADII_M
    ]
    for point in road_probe_points(roads_gdf, frontage_midpoint):
        lat, lng = pipeline.lambert_to_wgs84(point.x, point.y)
        probes.append((lat, lng, 20))
    panoramas: Dict[str, Dict[str, Any]] = {}
    for lat, lng, radius in probes:
        metadata = query_panorama(lat, lng, radius, api_key, use_api_cache=use_api_cache)
        if metadata is None:
            continue
        pano_id = str(metadata["pano_id"])
        location = metadata["location"]
        pano_lat, pano_lng = float(location["lat"]), float(location["lng"])
        if pano_id not in panoramas:
            pano_x, pano_y = pipeline.wgs84_to_lambert(pano_lat, pano_lng)
            panoramas[pano_id] = {
                "pano_id": pano_id,
                "cam_lat": pano_lat,
                "cam_lng": pano_lng,
                "cam_lambert_x": pano_x,
                "cam_lambert_y": pano_y,
                "date": metadata.get("date"),
                "copyright": metadata.get("copyright"),
                "queried_radii_m": [],
            }
        panoramas[pano_id]["queried_radii_m"].append(radius)
    return list(panoramas.values())


def angular_difference(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def rank_panoramas(
    panoramas: Sequence[Dict[str, Any]],
    facade_edge: LineString,
    facade_midpoint: Point,
    building_center: Point,
    outward_normal: Tuple[float, float],
) -> List[Dict[str, Any]]:
    pipeline = _implementation()
    start, end = facade_edge.coords[0], facade_edge.coords[-1]
    start_lat, start_lng = pipeline.lambert_to_wgs84(*start)
    end_lat, end_lng = pipeline.lambert_to_wgs84(*end)
    center_lat, center_lng = pipeline.lambert_to_wgs84(building_center.x, building_center.y)
    nx, ny = outward_normal
    ranked: List[Dict[str, Any]] = []
    for panorama in panoramas:
        camera = Point(panorama["cam_lambert_x"], panorama["cam_lambert_y"])
        dx, dy = camera.x - facade_midpoint.x, camera.y - facade_midpoint.y
        distance = math.hypot(dx, dy)
        if distance <= 0.1:
            continue
        # A facade edge has two mathematically valid normal directions. CadGIS
        # does not guarantee which sign is returned, so the sign cannot tell us
        # whether an otherwise valid street camera is in front of the facade.
        # The magnitude measures the perpendicular viewing angle we need.
        signed_frontality = (dx * nx + dy * ny) / distance
        frontality = abs(signed_frontality)
        if frontality <= 0.05:
            continue
        bearing_a = pipeline.bearing_deg(panorama["cam_lat"], panorama["cam_lng"], start_lat, start_lng)
        bearing_b = pipeline.bearing_deg(panorama["cam_lat"], panorama["cam_lng"], end_lat, end_lng)
        apparent_width_deg = angular_difference(bearing_a, bearing_b)
        heading = pipeline.bearing_deg(panorama["cam_lat"], panorama["cam_lng"], center_lat, center_lng)
        apparent_width_px = apparent_width_deg * 640.0 / 70.0
        score = apparent_width_px * max(0.0, frontality) ** 1.5
        ranked.append(
            {
                **panorama,
                "distance_to_facade_m": round(distance, 2),
                "signed_frontality": round(signed_frontality, 4),
                "frontality": round(frontality, 4),
                "heading": round(heading, 2),
                "apparent_facade_width_deg": round(apparent_width_deg, 3),
                "estimated_facade_width_px_at_fov_70": round(apparent_width_px, 1),
                "selection_score": round(score, 2),
            }
        )
    return sorted(ranked, key=lambda item: item["selection_score"], reverse=True)


def filename_slug(address: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", address)).strip("_")[:55]


def _cached_metadata(site_dir: Path) -> Optional[Dict[str, Any]]:
    """Reuse only complete output produced by this exact 90/110 approach."""
    for meta_path in sorted(site_dir.glob("*_meta.json")):
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            params = payload.get("image_params") or {}
            images = payload.get("images") or []
            if params.get("selection_method") != SELECTION_METHOD:
                continue
            if int(params.get("panorama_selection_version", 0)) != PANORAMA_SELECTION_VERSION:
                continue
            if params.get("size") != IMAGE_SIZE:
                continue
            if [float(value) for value in params.get("fovs", [])] != list(FOVS):
                continue
            if len(images) != len(FOVS):
                continue
            resolved = []
            for item in images:
                path = Path(str(item.get("path") or ""))
                if not path.is_absolute():
                    path = site_dir / path.name
                if not path.is_file():
                    break
                resolved.append({**item, "path": str(path.resolve())})
            if len(resolved) == len(FOVS):
                payload["images"] = resolved
                return payload
        except (OSError, TypeError, ValueError):
            continue
    return None


def _to_manifest_record(
    site: Dict[str, Any],
    address: str,
    metadata: Dict[str, Any],
    *,
    from_cache: bool,
    capture_year: Optional[int] = None,
) -> Dict[str, Any]:
    selected = metadata["selected_panorama"]
    width, height = (int(value) for value in metadata["image_params"]["size"].split("x", 1))
    records: List[Dict[str, Any]] = []
    for priority, item in enumerate(
        sorted(metadata.get("images") or [], key=lambda value: float(value["fov"])), start=1
    ):
        fov = float(item["fov"])
        records.append(
            {
                "pano_id": selected["pano_id"],
                "lat": float(selected["cam_lat"]),
                "lon": float(selected["cam_lng"]),
                "cam_x": float(selected["cam_lambert_x"]),
                "cam_y": float(selected["cam_lambert_y"]),
                "date": selected.get("date"),
                "heading": float(item.get("heading", selected["heading"])),
                "fov": fov,
                "pitch": float(item.get("pitch", 0.0)),
                "image_width": width,
                "image_height": height,
                "file_path": str(Path(str(item["path"])).resolve()),
                "left_corner": site.get("frontage_point_a"),
                "right_corner": site.get("frontage_point_b"),
                "frontage_source": "agent2_parcel",
                "image_type": f"frontage_fov{fov:g}",
                "viewpoint": "center",
                "detection_priority": priority,
                "fallback_only": fov > min(FOVS),
                "rectified": False,
                "source_heading": float(selected["heading"]),
            }
        )
    return {
        "site_id": str(site["site_id"]),
        "postcode": site.get("postcode"),
        "street_norm": site.get("street_norm"),
        "hnr_base": site.get("hnr_base"),
        "x": site.get("x"),
        "y": site.get("y"),
        "address": address,
        "flags": list(site.get("flags") or []),
        "images": records,
        "status": "OK" if len(records) == len(FOVS) else "NO_STREETVIEW",
        "requested_year": capture_year if capture_year is not None else "latest",
        "acquisition_flow": "best_front_facing_panorama_fov90_110",
        "from_cache": from_cache,
        "cache_source": "job_output" if from_cache else None,
    }


def process_site(
    site: Dict[str, Any],
    *,
    api_key: str,
    out_images_dir: Path,
    address: Optional[str] = None,
    agent1_members: Optional[Sequence[Dict[str, Any]]] = None,
    image_size: str = IMAGE_SIZE,
    pitch: float = 0.0,
    capture_year: Optional[int] = None,
    cancel_check: Optional[Callable[[], None]] = None,
    use_api_cache: bool = True,
) -> Dict[str, Any]:
    """Download the selected panorama at FOV 90 and FOV 110."""
    if cancel_check:
        cancel_check()
    pipeline = _implementation()
    site_id = str(site["site_id"])
    resolved_address = address or address_from_agent1(site, agent1_members)
    base = {key: site.get(key) for key in ("site_id", "postcode", "street_norm", "hnr_base", "x", "y")}
    if not resolved_address or resolved_address == "Belgium":
        return {
            **base,
            "address": resolved_address,
            "flags": sorted(set(list(site.get("flags") or []) + ["NO_ADDRESS"])),
            "images": [],
            "status": "NO_STREETVIEW",
            "acquisition_flow": "best_front_facing_panorama_fov90_110",
        }
    site_dir = Path(out_images_dir) / site_id
    site_dir.mkdir(parents=True, exist_ok=True)
    metadata = _cached_metadata(site_dir) if use_api_cache else None
    from_cache = metadata is not None
    if metadata is None:
        geocoded = pipeline.geocode_address(resolved_address, api_key)
        cadgis = pipeline.get_building_footprint(geocoded["lambert_x"], geocoded["lambert_y"], buffer_m=75.0)
        geometry = pipeline.compute_building_centroid(
            cadgis["building_geom"],
            cadgis["roads_gdf"],
            Point(geocoded["lambert_x"], geocoded["lambert_y"]),
            cadgis["parcel_geom"],
        )
        facade_midpoint = Point(geometry["facade_midpoint_lambert"])
        panoramas = discover_panoramas(
            geocoded["lat"], geocoded["lng"], cadgis["roads_gdf"], facade_midpoint,
            api_key, use_api_cache=use_api_cache,
        )
        ranked = rank_panoramas(
            panoramas,
            geometry["facade_edge"],
            facade_midpoint,
            Point(geometry["centroid_lambert"]),
            geometry["outward_normal"],
        )
        if capture_year is not None:
            ranked = [item for item in ranked if str(item.get("date") or "").startswith(str(capture_year))]
        if not ranked:
            return {
                **base,
                "address": resolved_address,
                "flags": sorted(set(list(site.get("flags") or []) + ["NO_STREETVIEW"])),
                "images": [],
                "status": "NO_STREETVIEW",
                "requested_year": capture_year if capture_year is not None else "latest",
                "acquisition_flow": "best_front_facing_panorama_fov90_110",
            }
        best = ranked[0]
        stem = filename_slug(resolved_address)
        images = []
        for fov in FOVS:
            if cancel_check:
                cancel_check()
            images.append(
                pipeline.download_streetview_image(
                    pano_id=best["pano_id"],
                    heading=best["heading"],
                    pitch=pitch,
                    fov=fov,
                    api_key=api_key,
                    out_path=site_dir / f"{stem}_fov{fov:g}.jpg",
                    size=IMAGE_SIZE,
                )
            )
        metadata = {
            "address": resolved_address,
            "geocode": geocoded,
            "search_radii_m": list(SEARCH_RADII_M),
            "selected_panorama": best,
            "ranked_candidates": ranked,
            "images": images,
            "image_params": {
                "size": IMAGE_SIZE,
                "fovs": list(FOVS),
                "selection_method": SELECTION_METHOD,
                "panorama_selection_version": PANORAMA_SELECTION_VERSION,
            },
        }
        (site_dir / f"{stem}_meta.json").write_text(
            json.dumps(metadata, indent=2, default=str), encoding="utf-8"
        )
    if cancel_check:
        cancel_check()
    return _to_manifest_record(
        site, resolved_address, metadata, from_cache=from_cache, capture_year=capture_year
    )
