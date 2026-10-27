#!/usr/bin/env python3
"""
Download the exact Google Street View image for any Belgian address following
the 9-step spatial pipeline:

1. Address
   â†“
2. Geocode Address
   â†“
3. Get Building Footprint (CadGIS)
   â†“
4. Derive the building's street-facing facade from its footprint and parcel frontage
   â†“
5. Find Nearby Street View Panoramas
   â†“
6. Score Panoramas
   â†“
7. Select Best Panorama
   â†“
8. Calculate Heading
   â†“
9. Download a facade-aligned Street View image (up to the API's 640x640 limit)
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import math
import os
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import geopandas as gpd
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, box
from shapely.ops import nearest_points

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("exact_streetview")

ROOT = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[3]
LEGACY_IMAGE_DOWNLOAD_ROOT = REPO_ROOT / "image downlaod"
if str(LEGACY_IMAGE_DOWNLOAD_ROOT) not in sys.path:
    sys.path.insert(0, str(LEGACY_IMAGE_DOWNLOAD_ROOT))
BELGIAN_LAMBERT_72 = 31370
TO_WGS84 = Transformer.from_crs(BELGIAN_LAMBERT_72, 4326, always_xy=True)
TO_LAMBERT = Transformer.from_crs(4326, BELGIAN_LAMBERT_72, always_xy=True)
PANORAMA_SELECTION_VERSION = 5


def lambert_to_wgs84(x: float, y: float) -> Tuple[float, float]:
    """Convert Belgian Lambert 72 (x, y) to WGS84 (lat, lng)."""
    lng, lat = TO_WGS84.transform(x, y)
    return float(lat), float(lng)


def wgs84_to_lambert(lat: float, lng: float) -> Tuple[float, float]:
    """Convert WGS84 (lat, lng) to Belgian Lambert 72 (x, y)."""
    x, y = TO_LAMBERT.transform(lng, lat)
    return float(x), float(y)


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from (lat1, lon1) to (lat2, lon2) in degrees (0-360)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two WGS84 points."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def load_api_key() -> str:
    """Load Google Maps API Key from .env or environment."""
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k == "GOOGLE_MAPS_API_KEY" and v and v != "your_api_key_here":
                os.environ[k] = v
                return v

    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not key or key == "your_api_key_here":
        raise SystemExit(
            "Missing GOOGLE_MAPS_API_KEY. Please set it in .env or environment."
        )
    return key


# ==============================================================================
# STEP 2: Geocode Address
# ==============================================================================
def geocode_address(address: str, api_key: str) -> Dict[str, Any]:
    """
    Geocode an address string to WGS84 (lat, lng) and Belgian Lambert 72 (x, y).
    Uses official Flemish Geopunt Geolocation API first for millimeter accuracy on Belgian addresses,
    with Google Geocoding API as universal fallback.
    """
    # 1. Try official Flemish Geopunt Geolocation API
    try:
        q = urllib.parse.quote(address)
        url_geo = f"https://geo.api.vlaanderen.be/geolocation/v4/Location?q={q}"
        req_geo = urllib.request.Request(url_geo, headers={"User-Agent": "cadgis-streetview/1.0"})
        with urllib.request.urlopen(req_geo, timeout=12) as resp:
            data_geo = json.loads(resp.read().decode("utf-8"))
        if data_geo.get("LocationResult") and len(data_geo["LocationResult"]) > 0:
            best_match = data_geo["LocationResult"][0]
            loc = best_match["Location"]
            lat = float(loc["Lat_WGS84"])
            lng = float(loc["Lon_WGS84"])
            lx = float(loc["X_Lambert72"])
            ly = float(loc["Y_Lambert72"])
            return {
                "source": "flemish_geopunt",
                "formatted_address": best_match.get("FormattedAddress", address),
                "lat": lat,
                "lng": lng,
                "lambert_x": lx,
                "lambert_y": ly,
                "location_type": best_match.get("LocationType", "ROOFTOP"),
            }
    except Exception as exc:
        logger.debug("Geopunt geocode fallback to Google: %s", exc)

    # 2. Fallback to Google Geocoding API
    params = urllib.parse.urlencode({"address": address, "key": api_key})
    url = f"https://maps.googleapis.com/maps/api/geocode/json?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "cadgis-streetview/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if data.get("status") != "OK" or not data.get("results"):
        raise RuntimeError(
            f"Geocoding failed for '{address}': status={data.get('status')} {data.get('error_message', '')}"
        )

    res = data["results"][0]
    loc = res["geometry"]["location"]
    lat, lng = float(loc["lat"]), float(loc["lng"])
    lambert_x, lambert_y = wgs84_to_lambert(lat, lng)

    return {
        "source": "google_geocoding",
        "formatted_address": res.get("formatted_address", address),
        "lat": lat,
        "lng": lng,
        "lambert_x": lambert_x,
        "lambert_y": lambert_y,
        "place_id": res.get("place_id"),
        "location_type": res["geometry"].get("location_type"),
    }


# ==============================================================================
# STEP 3: Get Building Footprint (CadGIS / GRB WFS)
# ==============================================================================
def get_building_footprint(
    lambert_x: float,
    lambert_y: float,
    buffer_m: float = 75.0,
) -> Dict[str, Any]:
    """
    Fetch official Flemish CadGIS building footprints (GRB:GBG), cadastral parcels
    (GRB:ADP), and roads (GRB:WBN) around the geocoded address location.
    Identifies the target building footprint and street frontage edge.
    """
    bbox = f"{lambert_x - buffer_m},{lambert_y - buffer_m},{lambert_x + buffer_m},{lambert_y + buffer_m}"
    headers = {"User-Agent": "Mozilla/5.0 (CadGIS-Exact-Streetview)"}
    addr_pt = Point(lambert_x, lambert_y)

    # 1. Fetch Buildings (GRB:GBG)
    url_gbg = (
        f"https://geo.api.vlaanderen.be/GRB/wfs?service=WFS&version=2.0.0"
        f"&request=GetFeature&typeNames=GRB:GBG&outputFormat=application/json&bbox={bbox}"
    )
    req = urllib.request.Request(url_gbg, headers=headers)
    raw = urllib.request.urlopen(req, timeout=30).read()
    gdf_bld = gpd.read_file(io.BytesIO(raw))
    if not gdf_bld.empty:
        if gdf_bld.crs is None:
            gdf_bld = gdf_bld.set_crs(epsg=BELGIAN_LAMBERT_72)
        elif gdf_bld.crs.to_epsg() != BELGIAN_LAMBERT_72:
            gdf_bld = gdf_bld.to_crs(epsg=BELGIAN_LAMBERT_72)

    # 2. Fetch Parcels (GRB:ADP)
    url_adp = (
        f"https://geo.api.vlaanderen.be/GRB/wfs?service=WFS&version=2.0.0"
        f"&request=GetFeature&typeNames=GRB:ADP&outputFormat=application/json&bbox={bbox}"
    )
    req = urllib.request.Request(url_adp, headers=headers)
    raw = urllib.request.urlopen(req, timeout=30).read()
    gdf_parcels = gpd.read_file(io.BytesIO(raw))
    if not gdf_parcels.empty:
        if gdf_parcels.crs is None:
            gdf_parcels = gdf_parcels.set_crs(epsg=BELGIAN_LAMBERT_72)
        elif gdf_parcels.crs.to_epsg() != BELGIAN_LAMBERT_72:
            gdf_parcels = gdf_parcels.to_crs(epsg=BELGIAN_LAMBERT_72)

    # 3. Fetch Roads (GRB:WBN)
    url_wbn = (
        f"https://geo.api.vlaanderen.be/GRB/wfs?service=WFS&version=2.0.0"
        f"&request=GetFeature&typeNames=GRB:WBN&outputFormat=application/json&bbox={bbox}"
    )
    gdf_roads = gpd.GeoDataFrame()
    try:
        req = urllib.request.Request(url_wbn, headers=headers)
        raw = urllib.request.urlopen(req, timeout=20).read()
        gdf_roads = gpd.read_file(io.BytesIO(raw))
        if not gdf_roads.empty:
            if gdf_roads.crs is None:
                gdf_roads = gdf_roads.set_crs(epsg=BELGIAN_LAMBERT_72)
            elif gdf_roads.crs.to_epsg() != BELGIAN_LAMBERT_72:
                gdf_roads = gdf_roads.to_crs(epsg=BELGIAN_LAMBERT_72)
    except Exception as e:
        logger.warning("Could not fetch road elements from WFS: %s", e)

    # Resolve target building
    target_building: Optional[Polygon] = None
    bld_meta: Dict[str, Any] = {}

    if not gdf_bld.empty:
        # Check containing building
        contains_mask = gdf_bld.geometry.contains(addr_pt)
        if contains_mask.any():
            row = gdf_bld[contains_mask].iloc[0]
            target_building = row.geometry
            bld_meta = {"match": "contains", "oidn": row.get("OIDN"), "uidn": row.get("UIDN")}
        else:
            # Nearest building
            dists = gdf_bld.distance(addr_pt)
            idx = dists.idxmin()
            if dists[idx] <= 25.0:
                row = gdf_bld.loc[idx]
                target_building = row.geometry
                bld_meta = {"match": "nearest", "distance_to_point_m": float(dists[idx]), "oidn": row.get("OIDN")}

    # Fallback to parcel if building not found
    target_parcel: Optional[Polygon] = None
    parcel_capakey: Optional[str] = None
    if not gdf_parcels.empty:
        p_mask = gdf_parcels.geometry.contains(addr_pt)
        if p_mask.any():
            p_row = gdf_parcels[p_mask].iloc[0]
            target_parcel = p_row.geometry
            parcel_capakey = str(p_row.get("CAPAKEY", p_row.get("id", "")))
        else:
            p_idx = gdf_parcels.distance(addr_pt).idxmin()
            p_row = gdf_parcels.loc[p_idx]
            target_parcel = p_row.geometry
            parcel_capakey = str(p_row.get("CAPAKEY", p_row.get("id", "")))

    if target_building is None and target_parcel is not None:
        target_building = target_parcel
        bld_meta = {"match": "parcel_fallback", "capakey": parcel_capakey}
    elif target_building is None:
        # Ultimate fallback: small synthetic box around address point
        target_building = box(lambert_x - 5.0, lambert_y - 5.0, lambert_x + 5.0, lambert_y + 5.0)
        bld_meta = {"match": "synthetic_box"}

    return {
        "building_geom": target_building,
        "parcel_geom": target_parcel,
        "capakey": parcel_capakey,
        "parcels_gdf": gdf_parcels,
        "roads_gdf": gdf_roads,
        "bld_meta": bld_meta,
        "total_buildings_fetched": len(gdf_bld),
        "total_parcels_fetched": len(gdf_parcels),
    }


# ==============================================================================
# STEP 4: Compute Building Centroid & Facade Frontage
# ==============================================================================
def compute_building_centroid(
    building_geom: Union[Polygon, MultiPolygon],
    roads_gdf: gpd.GeoDataFrame,
    addr_pt: Point,
    parcel_geom: Optional[Polygon] = None,
) -> Dict[str, Any]:
    """
    Compute building centroid (Lambert 72 and WGS84).
    Determine the street-facing frontage edge and outward street normal vector.
    """
    from draw_parcels import get_frontage

    centroid = building_geom.centroid
    c_lat, c_lng = lambert_to_wgs84(centroid.x, centroid.y)
    area_m2 = float(building_geom.area)

    # Determine frontage edge using parcel (or building fallback)
    ref_geom = parcel_geom if parcel_geom is not None else building_geom
    try:
        frontage_dict = get_frontage(ref_geom, road_geoms=roads_gdf, address_point=addr_pt, mode="road")
        frontage_edge = frontage_dict["edge"]
    except Exception as exc:
        logger.warning("get_frontage fallback: %s", exc)
        # Fallback to closest boundary edge
        exterior = list(ref_geom.exterior.coords) if hasattr(ref_geom, "exterior") else []
        edges = [LineString([exterior[i], exterior[i + 1]]) for i in range(len(exterior) - 1)]
        frontage_edge = edges[0] if edges else LineString([(centroid.x - 5, centroid.y), (centroid.x + 5, centroid.y)])

    # Midpoint of frontage edge
    f_mid = frontage_edge.interpolate(0.5, normalized=True)
    f_lat, f_lng = lambert_to_wgs84(f_mid.x, f_mid.y)

    # Determine road point to orient the normal toward the road network
    toward_xy: Tuple[float, float]
    if roads_gdf is not None and not roads_gdf.empty:
        try:
            nearest_idx = roads_gdf.distance(f_mid).idxmin()
            nearest_road = roads_gdf.loc[nearest_idx].geometry
            if nearest_road.geom_type == "MultiLineString":
                road_pt = nearest_points(nearest_road, f_mid)[0]
            elif nearest_road.geom_type == "LineString":
                road_pt = nearest_road.interpolate(nearest_road.project(f_mid))
            else:
                road_pt = nearest_road.centroid
            toward_xy = (float(road_pt.x), float(road_pt.y))
        except Exception:
            toward_xy = (f_mid.x + (f_mid.x - centroid.x), f_mid.y + (f_mid.y - centroid.y))
    else:
        toward_xy = (f_mid.x + (f_mid.x - centroid.x), f_mid.y + (f_mid.y - centroid.y))

    # Outward normal pointing toward the road
    (x1, y1), (x2, y2) = list(frontage_edge.coords)[0], list(frontage_edge.coords)[-1]
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length
    to_x, to_y = toward_xy[0] - f_mid.x, toward_xy[1] - f_mid.y
    if nx * to_x + ny * to_y < 0:
        nx, ny = -nx, -ny

    facade_edge = compute_building_facade_edge(
        building_geom=building_geom,
        frontage_edge=frontage_edge,
        outward_normal=(nx, ny),
    )
    facade_mid = facade_edge.interpolate(0.5, normalized=True)
    facade_lat, facade_lng = lambert_to_wgs84(facade_mid.x, facade_mid.y)

    return {
        "centroid_lambert": (centroid.x, centroid.y),
        "centroid_wgs84": (c_lat, c_lng),
        "area_m2": area_m2,
        "frontage_edge": frontage_edge,
        "frontage_midpoint_lambert": (f_mid.x, f_mid.y),
        "frontage_midpoint_wgs84": (f_lat, f_lng),
        "facade_edge": facade_edge,
        "facade_source": (
            "parcel_frontage_fallback" if facade_edge is frontage_edge else "building_footprint"
        ),
        "facade_midpoint_lambert": (facade_mid.x, facade_mid.y),
        "facade_midpoint_wgs84": (facade_lat, facade_lng),
        "outward_normal": (nx, ny),
        "frontage_length_m": float(frontage_edge.length),
        "toward_xy": toward_xy,
    }


def compute_building_facade_edge(
    building_geom: Union[Polygon, MultiPolygon],
    frontage_edge: LineString,
    outward_normal: Tuple[float, float],
) -> LineString:
    """Choose the building-footprint edge facing the parcel's street frontage."""
    frontage_coords = list(frontage_edge.coords)
    fx = frontage_coords[-1][0] - frontage_coords[0][0]
    fy = frontage_coords[-1][1] - frontage_coords[0][1]
    frontage_length = math.hypot(fx, fy)
    if frontage_length == 0:
        raise ValueError("The parcel frontage edge must have non-zero length.")
    tx, ty = fx / frontage_length, fy / frontage_length
    frontage_mid = frontage_edge.interpolate(0.5, normalized=True)
    nx, ny = outward_normal

    polygons = list(building_geom.geoms) if isinstance(building_geom, MultiPolygon) else [building_geom]
    candidates: List[Tuple[float, LineString]] = []
    for polygon in polygons:
        if not isinstance(polygon, Polygon) or polygon.is_empty:
            continue
        coords = list(polygon.exterior.coords)
        for start, end in zip(coords, coords[1:]):
            edge = LineString([start, end])
            length = edge.length
            if length < 1.0:
                continue

            ex = (end[0] - start[0]) / length
            ey = (end[1] - start[1]) / length
            parallelism = abs(ex * tx + ey * ty)
            if parallelism < math.cos(math.radians(35.0)):
                continue

            midpoint = edge.interpolate(0.5, normalized=True)
            dx, dy = midpoint.x - frontage_mid.x, midpoint.y - frontage_mid.y
            inward_depth = -(dx * nx + dy * ny)
            lateral_offset = abs(dx * tx + dy * ty)
            outside_penalty = max(0.0, -inward_depth) * 5.0
            depth_penalty = max(0.0, inward_depth) * 0.2
            score = (
                outside_penalty
                + depth_penalty
                + (1.0 - parallelism) * 30.0
                + lateral_offset * 0.05
                - min(length, 20.0) * 0.2
            )
            candidates.append((score, edge))

    if not candidates:
        logger.warning(
            "Could not identify a building edge parallel to its parcel frontage; "
            "using the parcel frontage as the camera reference."
        )
        return frontage_edge

    return min(candidates, key=lambda candidate: candidate[0])[1]


def edge_to_wgs84(edge: LineString) -> List[List[float]]:
    """Return frontage endpoints as [[lat, lng], [lat, lng]]."""
    coords = list(edge.coords)
    return [
        list(lambert_to_wgs84(*coords[0])),
        list(lambert_to_wgs84(*coords[-1])),
    ]


def camera_facing_boundary_edge(
    geometry: Union[Polygon, MultiPolygon],
    camera: Point,
    target: Point,
) -> LineString:
    """Return the boundary edge crossed when looking from camera to target.

    Road-union proximity can select a side parcel edge when several roads or
    lanes are nearby. Once a real panorama is selected, its sight line gives a
    direct and deterministic definition of the visible frontage.
    """
    polygons = list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
    sight_line = LineString([(camera.x, camera.y), (target.x, target.y)])
    candidates: List[Tuple[float, float, LineString]] = []
    fallback: List[Tuple[float, float, LineString]] = []
    for polygon in polygons:
        if not isinstance(polygon, Polygon) or polygon.is_empty:
            continue
        coords = list(polygon.exterior.coords)
        for start, end in zip(coords, coords[1:]):
            edge = LineString([start, end])
            if edge.length <= 0.20:
                continue
            fallback.append((edge.distance(sight_line), edge.distance(camera), edge))
            intersection = edge.intersection(sight_line)
            if not intersection.is_empty:
                candidates.append((intersection.distance(camera), -edge.length, edge))
    if candidates:
        return min(candidates, key=lambda item: (item[0], item[1]))[2]
    if fallback:
        return min(fallback, key=lambda item: (item[0], item[1]))[2]
    raise ValueError("No usable boundary edge found for camera sight line")


def compute_neighbor_frontages(
    parcels_gdf: gpd.GeoDataFrame,
    target_parcel: Optional[Union[Polygon, MultiPolygon]],
    roads_gdf: gpd.GeoDataFrame,
    target_mid: Point,
    target_normal: Tuple[float, float],
) -> List[Dict[str, Any]]:
    """Find nearby touching parcels and serialize their road-facing frontages."""
    from draw_parcels import get_frontage

    frontages: List[Dict[str, Any]] = []
    if parcels_gdf is None or parcels_gdf.empty or target_parcel is None:
        return frontages

    probe = target_parcel.buffer(0.5)
    for _, row in parcels_gdf.iterrows():
        geometry = row.geometry
        if (
            geometry is None
            or geometry.is_empty
            or geometry.equals(target_parcel)
            or not geometry.intersects(probe)
        ):
            continue

        try:
            frontage = get_frontage(
                geometry,
                road_geoms=roads_gdf,
                address_point=geometry.representative_point(),
                mode="road",
            )
        except Exception as exc:
            logger.warning("Could not compute frontage for neighboring parcel: %s", exc)
            continue

        edge = frontage["edge"]
        midpoint = edge.interpolate(0.5, normalized=True)
        nx, ny = target_normal
        dx, dy = midpoint.x - target_mid.x, midpoint.y - target_mid.y
        depth = dx * nx + dy * ny
        if abs(depth) > 8.0:
            continue

        frontages.append({
            "capakey": str(row.get("CAPAKEY", row.get("id", ""))),
            "frontage_wgs84": edge_to_wgs84(edge),
        })

    return frontages


# ==============================================================================
# STEP 5: Find Nearby Street View Panoramas
# ==============================================================================
def find_nearby_panoramas(
    f_mid: Point,
    outward_normal: Tuple[float, float],
    frontage_edge: LineString,
    api_key: str,
    search_radius_m: int = 30,
    preferred_dist_m: float = 24.0,
    roads_gdf: Optional[gpd.GeoDataFrame] = None,
) -> List[Dict[str, Any]]:
    """
    Search along the street in front of the house at multiple distances and lateral
    offsets using Google Street View Metadata API.
    """
    nx, ny = outward_normal
    coords = list(frontage_edge.coords)
    tx = coords[-1][0] - coords[0][0]
    ty = coords[-1][1] - coords[0][1]
    tl = math.hypot(tx, ty) or 1.0
    tx, ty = tx / tl, ty / tl

    # Candidate probes retain the facade-normal search because GRB sometimes
    # omits private/access lanes. Road-foot probes use a small snap radius so
    # they cannot silently jump to a parallel road or side lane.
    probe_points: List[Tuple[float, float, float, int]] = []
    if roads_gdf is not None and not roads_gdf.empty:
        try:
            nearest_idx = roads_gdf.distance(f_mid).idxmin()
            road_geom = roads_gdf.loc[nearest_idx].geometry
            road_foot = nearest_points(road_geom, f_mid)[0]
            for offset in range(-36, 37, 4):
                probe_points.append(
                    (
                        road_foot.x + tx * offset,
                        road_foot.y + ty * offset,
                        abs(offset) * 0.2,
                        8,
                    )
                )
        except Exception as exc:
            logger.info("Road-constrained panorama probes unavailable: %s", exc)

    lateral_offsets = (0.0, -3.0, 3.0, -6.0, 6.0, -9.0, 9.0, -12.0, 12.0, -15.0, 15.0)
    distances = sorted(
        set(
            [8.0, 12.0, 16.0, 20.0, 24.0, 28.0, 32.0]
            + [max(6.0, preferred_dist_m - 4.0), preferred_dist_m, preferred_dist_m + 4.0]
        )
    )
    for dist in distances:
        for lateral in lateral_offsets:
            cx = f_mid.x + nx * dist + tx * lateral
            cy = f_mid.y + ny * dist + ty * lateral
            prior = abs(lateral) * 0.3 + abs(dist - preferred_dist_m) * 0.1
            probe_points.append((cx, cy, prior, search_radius_m))

    # Always ask at the facade itself. This is essential when the official
    # road layer lacks a driveway/access street but Street View contains it.
    probe_points.append((f_mid.x, f_mid.y, 0.0, min(search_radius_m, 25)))

    discovered_panos: List[Dict[str, Any]] = []
    seen_ids = set()

    for cx, cy, prior, radius in probe_points:
        lat, lng = lambert_to_wgs84(cx, cy)
        params = urllib.parse.urlencode({
            "location": f"{lat},{lng}",
            "radius": radius,
            "source": "outdoor",
            "key": api_key,
        })
        url = f"https://maps.googleapis.com/maps/api/streetview/metadata?{params}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cadgis-streetview/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                meta = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue

        if meta.get("status") != "OK":
            continue

        pano_id = meta.get("pano_id")
        if not pano_id or pano_id in seen_ids:
            continue

        seen_ids.add(pano_id)
        cam_lat = float(meta["location"]["lat"])
        cam_lng = float(meta["location"]["lng"])
        cam_x, cam_y = wgs84_to_lambert(cam_lat, cam_lng)

        discovered_panos.append({
            "pano_id": pano_id,
            "cam_lat": cam_lat,
            "cam_lng": cam_lng,
            "cam_lambert_x": cam_x,
            "cam_lambert_y": cam_y,
            "date": meta.get("date"),
            "copyright": meta.get("copyright"),
            "prior": prior,
        })

    return discovered_panos


# ==============================================================================
# STEP 6: Score Panoramas
# ==============================================================================
def score_panoramas(
    panos: List[Dict[str, Any]],
    f_mid: Point,
    outward_normal: Tuple[float, float],
    preferred_dist_m: float = 14.0,
    building_geom: Optional[Union[Polygon, MultiPolygon]] = None,
    roads_gdf: Optional[gpd.GeoDataFrame] = None,
) -> List[Dict[str, Any]]:
    """
    Score candidate panoramas based on:
    - Distance to facade (ideal ~12-16m)
    - Frontal alignment (must be in front of the house, not behind)
    - Lateral offset (perpendicular straight-on view)
    """
    nx, ny = outward_normal
    body = None
    if building_geom is not None and not building_geom.is_empty:
        body = building_geom.buffer(-0.5)
        if body.is_empty:
            body = building_geom

    road_geom = None
    if roads_gdf is not None and not roads_gdf.empty:
        try:
            road_geom = roads_gdf.loc[roads_gdf.distance(f_mid).idxmin()].geometry
        except Exception:
            road_geom = None

    scored: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for p in panos:
        vx = p["cam_lambert_x"] - f_mid.x
        vy = p["cam_lambert_y"] - f_mid.y
        dist = math.hypot(vx, vy) or 1.0

        # Cosine alignment with the inferred facade normal. The sign of this
        # normal is not reliable when CadGIS exposes roads on both sides of a
        # parcel (or an access lane is missing from GRB). Perpendicularity is
        # sign-independent; the heading is oriented toward the facade later.
        align = (vx * nx + vy * ny) / dist
        frontal_alignment = abs(align)

        # Perpendicular lateral distance off normal axis
        lateral = abs(vx * (-ny) + vy * nx)
        camera = Point(p["cam_lambert_x"], p["cam_lambert_y"])
        sight_line = LineString([(camera.x, camera.y), (f_mid.x, f_mid.y)])
        blocked = bool(body is not None and sight_line.intersects(body))
        road_gap = float(road_geom.distance(camera)) if road_geom is not None else 0.0

        # Distance penalty: ideal is ~14m
        if dist < 6.0:
            dist_pen = (6.0 - dist) * 4.0
        elif dist > 25.0:
            dist_pen = (dist - 25.0) * 8.0
        else:
            dist_pen = abs(dist - preferred_dist_m) * 0.6

        # Score formula: lower is better (less penalty)
        # Strongly penalize lateral offset and non-frontal alignment to pick
        # the camera parked directly in front of the house facade (perpendicular view)
        penalty_score = (
            lateral * 4.0
            + dist_pen
            + p.get("prior", 0.0)
            + (1.0 - frontal_alignment) * 40.0
            + min(road_gap, 20.0) * 1.5
        )

        entry = {
            **p,
            "distance_m": round(dist, 2),
            "lateral_m": round(lateral, 2),
            "alignment": round(align, 3),
            "frontal_alignment": round(frontal_alignment, 3),
            "road_gap_m": round(road_gap, 2),
            "blocked_by_target_building": blocked,
            "penalty_score": round(penalty_score, 2),
        }
        if not blocked and frontal_alignment >= math.cos(math.radians(40.0)):
            scored.append(entry)
        else:
            rejected.append(entry)

    scored.sort(key=lambda item: item["penalty_score"])
    rejected.sort(key=lambda item: item["penalty_score"])
    if not scored:
        logger.warning("No panorama passed strict facade visibility filters; relaxing.")
        scored = [item for item in rejected if not item["blocked_by_target_building"]]
        if not scored:
            scored = rejected
    return scored


# ==============================================================================
# STEP 7: Select Best Panorama
# ==============================================================================
def select_best_panorama(
    scored_panos: List[Dict[str, Any]],
    f_mid_lat: float,
    f_mid_lng: float,
    api_key: str,
) -> Dict[str, Any]:
    """Select the highest-scoring (lowest penalty) panorama, with fallback."""
    if scored_panos:
        return scored_panos[0]

    # Fallback: Query nearest panorama directly to facade midpoint
    logger.warning("No candidate passed frontal filtering. Querying direct fallback panorama.")
    params = urllib.parse.urlencode({
        "location": f"{f_mid_lat},{f_mid_lng}",
        "radius": 50,
        "source": "outdoor",
        "key": api_key,
    })
    url = f"https://maps.googleapis.com/maps/api/streetview/metadata?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "cadgis-streetview/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        meta = json.loads(resp.read().decode("utf-8"))

    if meta.get("status") != "OK":
        raise RuntimeError(f"No Street View available near this location: {meta.get('status')}")

    cam_lat = float(meta["location"]["lat"])
    cam_lng = float(meta["location"]["lng"])
    cam_x, cam_y = wgs84_to_lambert(cam_lat, cam_lng)

    return {
        "pano_id": meta.get("pano_id"),
        "cam_lat": cam_lat,
        "cam_lng": cam_lng,
        "cam_lambert_x": cam_x,
        "cam_lambert_y": cam_y,
        "date": meta.get("date"),
        "distance_m": haversine_m(cam_lat, cam_lng, f_mid_lat, f_mid_lng),
        "lateral_m": 0.0,
        "alignment": 1.0,
        "penalty_score": 99.0,
        "fallback": True,
    }


def perpendicular_facade_heading(
    frontage_edge: LineString,
    cam_lat: float,
    cam_lng: float,
) -> float:
    """
    True face-on compass heading: perpendicular to the frontage in WGS84
    (not Lambert grid - grid north skews and creates diagonal views).
    Points from the street camera directly into the facade.
    """
    a_xy = list(frontage_edge.coords)[0]
    b_xy = list(frontage_edge.coords)[-1]
    a_lat, a_lng = lambert_to_wgs84(a_xy[0], a_xy[1])
    b_lat, b_lng = lambert_to_wgs84(b_xy[0], b_xy[1])
    mid = frontage_edge.interpolate(0.5, normalized=True)
    mid_lat, mid_lng = lambert_to_wgs84(mid.x, mid.y)

    facade_bearing = bearing_deg(a_lat, a_lng, b_lat, b_lng)
    cand_a = (facade_bearing + 90.0) % 360.0
    cand_b = (facade_bearing - 90.0) % 360.0
    # Pick the perpendicular that points from street camera toward the facade
    look = bearing_deg(cam_lat, cam_lng, mid_lat, mid_lng)
    return cand_a if abs((cand_a - look + 180.0) % 360.0 - 180.0) <= abs((cand_b - look + 180.0) % 360.0 - 180.0) else cand_b


# ==============================================================================
# STEP 8: Calculate Heading
# ==============================================================================
def calculate_heading(
    cam_lat: float,
    cam_lng: float,
    target_lat: float,
    target_lng: float,
    frontage_edge: Optional[LineString] = None,
    pitch: float = 0.0,
    fov: float = 100.0,
    heading_mode: str = "perpendicular",
) -> Dict[str, Any]:
    """
    Calculate camera heading angle in degrees (0-360).
    Modes:
    - 'center_facade': Angular bisector between left and right facade corners.
      Mathematically guarantees the target house is EXACTLY AT THE MIDPOINT (x=50%)
      of the downloaded image with balanced left and right margins, while maintaining
      the most perpendicular perspective from the street panorama.
    - 'perpendicular': Strict normal vector to the facade wall plane.
    - 'look_at_midpoint': Direct line of sight to facade midpoint.
    """
    look = bearing_deg(cam_lat, cam_lng, target_lat, target_lng)
    perp = None
    centered = look
    span: Optional[float] = None

    if frontage_edge is not None:
        coords = list(frontage_edge.coords)
        if len(coords) >= 2:
            a_xy = coords[0]
            b_xy = coords[-1]
            a_lat, a_lng = lambert_to_wgs84(a_xy[0], a_xy[1])
            b_lat, b_lng = lambert_to_wgs84(b_xy[0], b_xy[1])

            bearing_a = bearing_deg(cam_lat, cam_lng, a_lat, a_lng)
            bearing_b = bearing_deg(cam_lat, cam_lng, b_lat, b_lng)

            # Angular midpoint between corner A and corner B
            diff = (bearing_b - bearing_a + 180.0) % 360.0 - 180.0
            span = abs(diff)
            centered = (bearing_a + diff / 2.0) % 360.0

            perp = perpendicular_facade_heading(frontage_edge, cam_lat, cam_lng)

    off_axis = None
    if heading_mode == "perpendicular" and perp is not None:
        off_axis = abs((perp - look + 180.0) % 360.0 - 180.0)
        # A strict wall-normal heading misses the house from an oblique camera.
        # Use it only near the facade axis; otherwise aim at the actual facade.
        heading = perp if off_axis <= 20.0 else look
    elif heading_mode == "look_at_midpoint":
        heading = look
    else:  # "center_facade"
        heading = centered

    return {
        "heading": round(heading, 2),
        "look_heading": round(look, 2),
        "perp_heading": round(perp, 2) if perp is not None else round(look, 2),
        "off_axis_deg": round(off_axis, 2) if off_axis is not None else None,
        "heading_strategy": (
            "perpendicular" if heading_mode == "perpendicular" and off_axis is not None and off_axis <= 20.0
            else "look_at_midpoint" if heading_mode == "perpendicular"
            else heading_mode
        ),
        "pitch": round(pitch, 1),
        "fov": round(fov, 1),
        "frontage_span_deg": round(span, 1) if span is not None else None,
        "required_fov": round(min(120.0, span + 10.0), 1) if span is not None else None,
        "requires_multiple_headings": span is not None and span > 110.0,
    }


# ==============================================================================
# STEP 9: Download Street View Image
# ==============================================================================
def download_streetview_image(
    pano_id: str,
    heading: float,
    pitch: float,
    fov: float,
    api_key: str,
    out_path: Path,
    size: str = "640x640",
) -> Dict[str, Any]:
    """Download Google Street View Static image by exact panorama ID and heading."""
    parse_image_size(size)
    params = {
        "size": size,
        "fov": fov,
        "pitch": pitch,
        "heading": heading,
        "pano": pano_id,
        "key": api_key,
        "return_error_code": "true",
    }
    url = f"https://maps.googleapis.com/maps/api/streetview?{urllib.parse.urlencode(params)}"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(url, headers={"User-Agent": "cadgis-streetview/1.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        content_type = resp.headers.get("Content-Type", "")
        payload = resp.read()
        if "image" not in content_type.lower():
            raise RuntimeError(
                f"Street View image download failed (content-type={content_type}). Check API key and billing."
            )
        out_path.write_bytes(payload)

    return {
        "path": str(out_path),
        "bytes": len(payload),
        "size": size,
        "heading": heading,
        "pitch": pitch,
        "fov": fov,
        "pano_id": pano_id,
    }


def fov_variant_name(fov: float, fovs: Sequence[float]) -> str:
    """Return a readable filename label for a field-of-view variant."""
    if len(fovs) == 1:
        return ""
    if fov == min(fovs):
        zoom_label = "zoom_in"
    elif fov == max(fovs):
        zoom_label = "zoom_out"
    else:
        zoom_label = "fov"
    return f"_{zoom_label}_{fov:g}deg"


def parse_fov_list(value: str) -> List[float]:
    """Parse and validate comma-separated Street View field-of-view values."""
    try:
        fovs = [float(item.strip()) for item in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("FOV values must be numbers separated by commas.") from exc
    if not fovs or any(not 0 < fov_value <= 120 for fov_value in fovs):
        raise argparse.ArgumentTypeError("FOV values must be greater than 0 and at most 120.")
    return list(dict.fromkeys(fovs))


def parse_image_size(value: str) -> Tuple[int, int]:
    """Validate Street View image dimensions, which are limited to 640 pixels per side."""
    try:
        width_text, height_text = value.lower().split("x", 1)
        width, height = int(width_text), int(height_text)
    except (AttributeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("Image size must use WIDTHxHEIGHT, for example 640x640.") from exc
    if not 1 <= width <= 640 or not 1 <= height <= 640:
        raise argparse.ArgumentTypeError("Street View image dimensions must be between 1 and 640 pixels.")
    return width, height


# ==============================================================================
# COMPLETE PIPELINE ORCHESTRATOR
# ==============================================================================
def download_exact_streetview(
    address: str,
    out_dir: Optional[Path] = None,
    api_key: Optional[str] = None,
    size: str = "640x640",
    fov: float = 55.0,
    pitch: float = 0.0,
    heading_mode: str = "perpendicular",
    flat_output: bool = False,
    output_index: Optional[int] = None,
    fovs: Optional[Sequence[float]] = None,
    preferred_distance: float = 24.0,
) -> Dict[str, Any]:
    """
    Executes the complete 9-step pipeline to download the exact frontal Street View image.
    """
    if not api_key:
        api_key = load_api_key()

    if out_dir is None:
        out_dir = ROOT / "streetview_exact"
    requested_fovs = list(dict.fromkeys(fovs if fovs is not None else [fov]))
    if not requested_fovs or any(not 0 < value <= 120 for value in requested_fovs):
        raise ValueError("Field-of-view values must be greater than 0 and at most 120 degrees.")

    # Configure UTF-8 on Windows stdout if supported
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    # Step 1: Input Address
    print("\n" + "=" * 70)
    print(f" [STEP 1/9] ADDRESS INPUT: {address}")
    print("=" * 70)

    # Step 2: Geocode Address
    print("\n [STEP 2/9] GEOCODING ADDRESS...")
    geo = geocode_address(address, api_key)
    lat, lng = geo["lat"], geo["lng"]
    lx, ly = geo["lambert_x"], geo["lambert_y"]
    print(f"            -> Formatted: {geo['formatted_address']}")
    print(f"            -> WGS84:     {lat:.6f}, {lng:.6f}")
    print(f"            -> Lambert72: {lx:.2f}, {ly:.2f}")

    # Step 3: Get Building Footprint (CadGIS)
    print("\n [STEP 3/9] FETCHING BUILDING FOOTPRINT (CadGIS/GRB WFS)...")
    cadgis = get_building_footprint(lx, ly, buffer_m=65.0)
    bgeom = cadgis["building_geom"]
    p_capakey = cadgis.get("capakey")
    print(f"            -> Fetched: {cadgis['total_buildings_fetched']} buildings, {cadgis['total_parcels_fetched']} parcels")
    print(f"            -> Match:   {cadgis['bld_meta'].get('match')}")
    if p_capakey:
        print(f"            -> Parcel:  {p_capakey}")

    # Step 4: Compute Building Centroid
    print("\n [STEP 4/9] COMPUTING BUILDING CENTROID & FACADE...")
    geom_info = compute_building_centroid(
        building_geom=bgeom,
        roads_gdf=cadgis["roads_gdf"],
        addr_pt=Point(lx, ly),
        parcel_geom=cadgis["parcel_geom"],
    )
    clat, clng = geom_info["centroid_wgs84"]
    flat, flng = geom_info["facade_midpoint_wgs84"]
    frontage_mid_pt = Point(geom_info["frontage_midpoint_lambert"])
    facade_mid_pt = Point(geom_info["facade_midpoint_lambert"])
    nx, ny = geom_info["outward_normal"]
    neighbors = compute_neighbor_frontages(
        parcels_gdf=cadgis["parcels_gdf"],
        target_parcel=cadgis["parcel_geom"],
        roads_gdf=cadgis["roads_gdf"],
        target_mid=frontage_mid_pt,
        target_normal=(nx, ny),
    )
    print(f"            -> Building Area:     {geom_info['area_m2']:.1f} mÂ²")
    print(f"            -> Centroid WGS84:    {clat:.6f}, {clng:.6f}")
    print(f"            -> Building facade midpoint: {flat:.6f}, {flng:.6f}")
    print(f"            -> Facade reference:   {geom_info['facade_source']}")
    print(f"            -> Outward Normal:    ({nx:.2f}, {ny:.2f})")
    print(f"            -> Neighbor parcels with frontage: {len(neighbors)}")

    # Step 5: Find Nearby Street View Panoramas
    print("\n [STEP 5/9] SEARCHING NEARBY STREET VIEW PANORAMAS...")
    panos = find_nearby_panoramas(
        f_mid=facade_mid_pt,
        outward_normal=(nx, ny),
        frontage_edge=geom_info["facade_edge"],
        api_key=api_key,
        preferred_dist_m=preferred_distance,
        roads_gdf=cadgis["roads_gdf"],
    )
    print(f"            -> Found {len(panos)} unique candidate panoramas along street")

    # Step 6: Score Panoramas
    print("\n [STEP 6/9] SCORING PANORAMAS (distance, frontal alignment, lateral offset)...")
    scored = score_panoramas(
        panos=panos,
        f_mid=facade_mid_pt,
        outward_normal=(nx, ny),
        preferred_dist_m=preferred_distance,
        building_geom=bgeom,
        roads_gdf=cadgis["roads_gdf"],
    )
    for i, s in enumerate(scored[:5]):
        print(
            f"            [{i+1}] Pano: {s['pano_id']} | Dist: {s['distance_m']}m | "
            f"Align: {s['alignment']} | Lateral: {s['lateral_m']}m | Penalty: {s['penalty_score']}"
        )

    # Step 7: Select Best Panorama
    print("\n [STEP 7/9] SELECTING BEST PANORAMA...")
    best = select_best_panorama(scored, flat, flng, api_key)
    print(f"            -> Selected Winner: {best['pano_id']}")
    print(f"            -> Camera Location: {best['cam_lat']:.6f}, {best['cam_lng']:.6f}")
    print(f"            -> Distance:        {best.get('distance_m', '?')}m")

    camera_point = Point(best["cam_lambert_x"], best["cam_lambert_y"])
    building_center = bgeom.centroid
    view_facade_edge = camera_facing_boundary_edge(
        bgeom, camera_point, building_center
    )
    view_frontage_edge = (
        camera_facing_boundary_edge(
            cadgis["parcel_geom"], camera_point, building_center
        )
        if cadgis.get("parcel_geom") is not None
        else view_facade_edge
    )

    # Step 8: Calculate Heading
    print("\n [STEP 8/9] CALCULATING EXACT FRONTAL HEADING...")
    # Aim oblique cameras at the target building itself. A CadGIS frontage
    # edge can occasionally describe a side wall; its normal is still useful
    # for a genuinely face-on camera, but its midpoint must not pull an
    # oblique view away from the house.
    head_info = calculate_heading(
        cam_lat=best["cam_lat"],
        cam_lng=best["cam_lng"],
        target_lat=clat,
        target_lng=clng,
        frontage_edge=view_facade_edge,
        pitch=pitch,
        fov=requested_fovs[0],
        heading_mode=heading_mode,
    )
    heading = head_info["heading"]
    required_fov = head_info["required_fov"]
    if required_fov is not None and required_fov > max(requested_fovs):
        print(
            f"            -> Frontage spans {head_info['frontage_span_deg']}Â°, "
            f"adding FOV {required_fov}Â° image"
        )
        requested_fovs.append(required_fov)
    if head_info["requires_multiple_headings"]:
        logger.warning(
            "Frontage spans %.1fÂ°; a single heading may not cover it reliably. "
            "This address needs multiple headings.",
            head_info["frontage_span_deg"],
        )
    print(f"            -> Calculated Heading: {heading}Â° ({head_info['heading_strategy']})")
    print(f"            -> Facade Normal:     {head_info.get('perp_heading')}Â°")
    print(f"            -> Look Line of Sight: {head_info.get('look_heading')}Â°")
    print(f"            -> Camera Pitch:       {head_info['pitch']}Â°")
    print(f"            -> Field of View(s):    {', '.join(f'{value:g}Â°' for value in requested_fovs)}")

    # Step 9: Download Street View Image
    print("\n [STEP 9/9] DOWNLOADING STREET VIEW IMAGE...")
    safe_slug = "".join(c if c.isalnum() else "_" for c in address)[:50].strip("_")
    if flat_output:
        prefix = f"{output_index:04d}_" if output_index is not None else ""
        target_dir = out_dir
        base_stem = f"{prefix}{safe_slug}"
    else:
        target_dir = out_dir / safe_slug
        base_stem = "streetview"

    image_results = []
    for variant_fov in requested_fovs:
        variant_label = fov_variant_name(variant_fov, requested_fovs)
        img_path = target_dir / f"{base_stem}{variant_label}.jpg"
        dl_info = download_streetview_image(
            pano_id=best["pano_id"],
            heading=heading,
            pitch=head_info["pitch"],
            fov=variant_fov,
            api_key=api_key,
            out_path=img_path,
            size=size,
        )
        image_results.append(dl_info)
        print(f"            [OK] SUCCESS: Downloaded {img_path} ({dl_info['bytes'] // 1024} KB)")

    # Save comprehensive JSON metadata
    meta_payload = {
        "step_1_address": address,
        "step_2_geocode": geo,
        "step_3_cadgis": {
            "match_type": cadgis["bld_meta"].get("match"),
            "capakey": p_capakey,
            "building_geojson": bgeom.__geo_interface__,
        },
        "step_4_centroid": {
            "centroid_wgs84": {"lat": clat, "lng": clng},
            "facade_midpoint_wgs84": {"lat": flat, "lng": flng},
            "facade_source": geom_info["facade_source"],
            "parcel_frontage_midpoint_wgs84": {
                "lat": geom_info["frontage_midpoint_wgs84"][0],
                "lng": geom_info["frontage_midpoint_wgs84"][1],
            },
            "facade_edge_wgs84": edge_to_wgs84(geom_info["facade_edge"]),
            "camera_facing_facade_wgs84": edge_to_wgs84(view_facade_edge),
            "camera_facing_frontage_wgs84": edge_to_wgs84(view_frontage_edge),
            "area_m2": geom_info["area_m2"],
            "outward_normal": [nx, ny],
        },
        "step_5_panoramas_found": len(panos),
        "step_6_scored_candidates": scored,
        "step_7_selected_panorama": best,
        "step_8_heading": head_info,
        "step_9_image": image_results[0],
        "step_9_images": image_results,
        "frontage": {
            "target": {
                "capakey": p_capakey,
                "frontage_wgs84": edge_to_wgs84(view_frontage_edge),
                "frontage_lambert": [list(point) for point in view_frontage_edge.coords],
                "building_facade_wgs84": edge_to_wgs84(view_facade_edge),
                "building_facade_lambert": [list(point) for point in view_facade_edge.coords],
                "selection_method": "camera_sightline_to_target_building",
            },
            "neighbors": neighbors,
        },
        "image_params": {
            "size": size,
            "fovs": requested_fovs,
            "heading_mode": heading_mode,
            "preferred_distance_m": preferred_distance,
            "panorama_selection_version": PANORAMA_SELECTION_VERSION,
        },
    }
    meta_path = target_dir / f"{base_stem}_meta.json"
    meta_path.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")
    print(f"            [OK] METADATA: Saved {meta_path}")
    print("=" * 70 + "\n")

    return meta_payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download exact frontal Street View image via 9-step CadGIS & Panorama Scoring pipeline"
    )
    parser.add_argument("--address", type=str, help="Single street address to geocode and download")
    parser.add_argument("--batch-file", type=Path, help="Text file with one address per line")
    parser.add_argument("--csv", type=Path, help="CSV file containing addresses")
    parser.add_argument("--address-col", type=str, default="address", help="CSV column name containing addresses")
    parser.add_argument("--gpkg", type=Path, help="GeoPackage file containing addresses")
    parser.add_argument("--layer", type=str, default=None, help="Layer name in GeoPackage")
    parser.add_argument(
        "--limit",
        type=int,
        help="Process only the first N addresses from the input file",
    )
    parser.add_argument("--outdir", type=Path, default=ROOT / "streetview_exact", help="Output directory")
    parser.add_argument(
        "--flat",
        action="store_true",
        help="Save every image and metadata JSON directly in --outdir, without per-address subfolders",
    )
    fov_group = parser.add_mutually_exclusive_group()
    fov_group.add_argument(
        "--fov",
        type=float,
        help="Download one field of view only; lower values zoom in",
    )
    fov_group.add_argument(
        "--fovs",
        type=parse_fov_list,
        default=[120.0],
        help="Comma-separated field-of-view values; defaults to 120",
    )
    parser.add_argument("--pitch", type=float, default=0.0, help="Camera pitch degrees (default 0)")
    parser.add_argument(
        "--preferred-distance",
        type=float,
        default=24.0,
        help="Preferred camera-to-facade distance in metres (default 24)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of addresses to process concurrently (default 8)",
    )
    parser.add_argument(
        "--size",
        type=parse_image_size,
        default="640x640",
        help="Image size up to 640x640 pixels; 640x640 is the API maximum",
    )
    parser.add_argument(
        "--heading-mode",
        type=str,
        default="perpendicular",
        choices=["center_facade", "perpendicular", "look_at_midpoint"],
        help="Heading mode: perpendicular (face-on to building facade), center_facade, or look_at_midpoint",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive integer.")
    if args.workers <= 0:
        parser.error("--workers must be a positive integer.")

    addresses: List[str] = []

    if args.address:
        addresses.append(args.address)
    elif args.batch_file and args.batch_file.exists():
        for line in args.batch_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                addresses.append(line)
    elif args.csv and args.csv.exists():
        import csv

        with open(args.csv, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            col = args.address_col if args.address_col in (reader.fieldnames or []) else (reader.fieldnames[0] if reader.fieldnames else "")
            for row in reader:
                val = row.get(col, "").strip()
                if val:
                    addresses.append(val)
    elif args.gpkg and args.gpkg.exists():
        gdf = gpd.read_file(args.gpkg, layer=args.layer)
        for _, row in gdf.iterrows():
            street = str(row.get("aansluitadres_straatnaam", row.get("street_norm", row.get("street", row.get("straat", "")))))
            nr = str(row.get("aansluitadres_huisnummer", row.get("hnr_base", row.get("nr", row.get("huisnummer", "")))))
            mun = str(row.get("aansluitadres_gemeente", row.get("municipality", row.get("gemeente", ""))))
            postcode = str(row.get("aansluitadres_postcode", row.get("postcode", "")))
            locality = " ".join(part for part in (postcode, mun) if part and part.lower() != "nan")
            addr = f"{street} {nr}, {locality}, Belgium".strip(", ")
            if addr:
                addresses.append(addr)
    else:
        parser.error("Please provide --address, --batch-file, --csv, or --gpkg.")

    if args.limit is not None:
        addresses = addresses[:args.limit]

    print(f"\n[INFO] Starting exact Street View downloads for {len(addresses)} address(es)...")
    api_key = load_api_key()

    def process_one(item: Tuple[int, str]) -> bool:
        i, addr = item
        try:
            print(f"\nProcessing [{i}/{len(addresses)}]: {addr}")
            download_exact_streetview(
                address=addr,
                out_dir=args.outdir,
                api_key=api_key,
                fov=args.fov,
                pitch=args.pitch,
                size=f"{args.size[0]}x{args.size[1]}",
                heading_mode=args.heading_mode,
                flat_output=args.flat,
                output_index=i if args.flat else None,
                fovs=[args.fov] if args.fov is not None else args.fovs,
                preferred_distance=args.preferred_distance,
            )
            return True
        except Exception as e:
            logger.error("Failed to process address '%s': %s", addr, e)
            return False

    indexed_addresses = list(enumerate(addresses, start=1))
    if args.workers > 1 and len(indexed_addresses) > 1:
        with ThreadPoolExecutor(max_workers=min(args.workers, len(indexed_addresses))) as executor:
            outcomes = list(executor.map(process_one, indexed_addresses))
    else:
        outcomes = [process_one(item) for item in indexed_addresses]
    success_count = sum(outcomes)
    fail_count = len(outcomes) - success_count

    print(f"\n[SUMMARY] Completed {len(addresses)} addresses: {success_count} succeeded, {fail_count} failed.")
    print(f"Output folder: {args.outdir.resolve()}\n")


if __name__ == "__main__":
    main()

