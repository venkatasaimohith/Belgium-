#!/usr/bin/env python3
"""
Agent 2 — Parcel (CadGIS lookup + frontage)
============================================
Reads Agent 1's output (agent1_sites.gpkg + agent1_mapping.json) and, for
each site:

  1. Point-in-polygon to find the cadastral parcel (fallback: nearest ≤15 m,
     flagged NEAREST_PARCEL; nothing found → NO_PARCEL).
  2. Finds the building footprint that overlaps the parcel.
  3. Picks the street-facing edge (closest to a road layer; falls back to the
     edge closest to the address point when no road layer is present).
  4. Computes frontage length; flags WIDE_FRONTAGE (>12 m) and MULTI_ADDRESS.
  5. Counts how many sites share the parcel.

Writes:
  agent2_site.json  – per-site parcel geometry, frontage endpoints, flags
  agent2_result.gpkg – layers: sites, parcels, frontage_edges, buildings, roads

Parcels can come from:
  a) A local GPKG file (--parcel-gpkg / --parcel-layer)
  b) Live Flemish CadGIS WFS (--fetch-wfs)

Usage
-----
    # With a local parcel file
    python agent2_parcel.py \\
        --sites-gpkg ../output/agent1_sites.gpkg \\
        --mapping-json ../output/agent1_mapping.json \\
        --parcel-gpkg /path/to/cadgis_parcels.gpkg \\
        --out-dir ./output

    # With live WFS (Flanders only)
    python agent2_parcel.py \\
        --sites-gpkg ../output/agent1_sites.gpkg \\
        --mapping-json ../output/agent1_mapping.json \\
        --fetch-wfs \\
        --out-dir ./output

Run with --help for all options.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import math
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import geopandas as gpd
import pyogrio
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from api_cache import cached_urlopen_bytes

# ─────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────
BELGIAN_LAMBERT_72 = 31370

WIDE_FRONTAGE_M = 12.0   # flag when frontage exceeds this value
FALLBACK_DIST_M = 15.0   # nearest-parcel fallback distance
NEIGHBOUR_BUFFER_M = 1.0  # buffer for finding touching parcels

# WFS URLs (Flanders / GRB)
_WFS_BASE = "https://geo.api.vlaanderen.be/GRB/wfs"
_WFS_PARAMS = "service=WFS&version=2.0.0&request=GetFeature&outputFormat=application/json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agent2")


# ─────────────────────────────────────────────
#  WFS helpers (Flanders)
# ─────────────────────────────────────────────

def _wfs_fetch(
    typename: str,
    bbox: str,
    timeout: int = 25,
    use_api_cache: bool = True,
) -> gpd.GeoDataFrame:
    url = f"{_WFS_BASE}?{_WFS_PARAMS}&typeNames={typename}&bbox={bbox}"
    raw = cached_urlopen_bytes(
        url,
        service="flanders-wfs",
        timeout=timeout,
        headers={"User-Agent": "MailboxPipeline/Agent2"},
        use_cache=use_api_cache,
    )
    gdf = gpd.read_file(io.BytesIO(raw))
    return gdf


def fetch_flanders_wfs(
    point: Point,
    buffer_m: float = 200.0,
    target_crs: int = BELGIAN_LAMBERT_72,
    use_api_cache: bool = True,
) -> Dict[str, gpd.GeoDataFrame]:
    """
    Fetch parcels (GRB:ADP), buildings (GRB:GBG) and roads (GRB:WBN) from the
    Flemish WFS for the area around *point*.
    """
    bbox = (
        f"{point.x - buffer_m},{point.y - buffer_m},"
        f"{point.x + buffer_m},{point.y + buffer_m}"
    )
    result: Dict[str, gpd.GeoDataFrame] = {}

    for key, typename in [("parcels", "GRB:ADP"), ("buildings", "GRB:GBG"), ("roads", "GRB:WBN")]:
        try:
            gdf = _wfs_fetch(typename, bbox, use_api_cache=use_api_cache)
            if gdf.empty:
                continue
            if gdf.crs is None:
                gdf = gdf.set_crs(epsg=target_crs)
            elif gdf.crs.to_epsg() != target_crs:
                gdf = gdf.to_crs(epsg=target_crs)
            result[key] = gdf
            logger.info("WFS %s: fetched %d features", typename, len(gdf))
        except Exception as exc:
            logger.warning("WFS %s failed: %s", typename, exc)

    return result


# ─────────────────────────────────────────────
#  Local GPKG helpers
# ─────────────────────────────────────────────

def load_gpkg_layer(
    gpkg_path: Path,
    preferred_layer: Optional[str],
    keywords: List[str],
    target_crs: int = BELGIAN_LAMBERT_72,
) -> gpd.GeoDataFrame:
    """Load a layer from a GPKG, auto-detecting by keyword if needed."""
    available = [item[0] for item in pyogrio.list_layers(gpkg_path)]
    if preferred_layer and preferred_layer in available:
        layer = preferred_layer
    else:
        candidates = [l for l in available if any(k in l.lower() for k in keywords)]
        layer = candidates[0] if candidates else available[0]

    logger.info("Loading layer '%s' from %s", layer, gpkg_path.name)
    gdf = gpd.read_file(str(gpkg_path), layer=layer)
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=target_crs)
    elif gdf.crs.to_epsg() != target_crs:
        gdf = gdf.to_crs(epsg=target_crs)
    return gdf


# ─────────────────────────────────────────────
#  Parcel lookup
# ─────────────────────────────────────────────

def get_target_parcel(
    point: Point,
    parcels_gdf: gpd.GeoDataFrame,
    fallback_dist: float = FALLBACK_DIST_M,
) -> Tuple[Optional[gpd.GeoSeries], str]:
    """
    Find the parcel that contains *point*.

    Returns (parcel_row, flag) where flag is one of:
        'contains', 'intersects', 'nearest', 'NO_PARCEL'
    """
    # 1. Exact contains
    containing = parcels_gdf[parcels_gdf.geometry.contains(point)]
    if not containing.empty:
        if len(containing) > 1:
            containing = containing.loc[
                containing.geometry.area == containing.geometry.area.min()
            ]
        return containing.iloc[0], "contains"

    # 2. Boundary touch
    touching = parcels_gdf[parcels_gdf.geometry.intersects(point)]
    if not touching.empty:
        return touching.iloc[0], "intersects"

    # 3. Nearest fallback
    search_box = point.buffer(fallback_dist).envelope
    candidates = parcels_gdf[parcels_gdf.geometry.intersects(search_box)].copy()
    if not candidates.empty:
        candidates["_dist"] = candidates.geometry.distance(point)
        nearest = candidates.sort_values("_dist").iloc[0]
        if nearest["_dist"] <= fallback_dist:
            logger.warning(
                "NEAREST_PARCEL fallback used (dist=%.2f m).", nearest["_dist"]
            )
            return nearest, "NEAREST_PARCEL"

    return None, "NO_PARCEL"


# ─────────────────────────────────────────────
#  Building footprint lookup
# ─────────────────────────────────────────────

def get_building(
    parcel_geom: Union[Polygon, MultiPolygon],
    buildings_gdf: Optional[gpd.GeoDataFrame],
) -> Optional[gpd.GeoSeries]:
    """Return the building footprint that overlaps the parcel most."""
    if buildings_gdf is None or buildings_gdf.empty:
        return None
    candidates = buildings_gdf[buildings_gdf.geometry.intersects(parcel_geom)].copy()
    if candidates.empty:
        return None
    candidates["_overlap"] = candidates.geometry.apply(
        lambda g: g.intersection(parcel_geom).area
    )
    return candidates.sort_values("_overlap", ascending=False).iloc[0]


# ─────────────────────────────────────────────
#  Frontage computation
# ─────────────────────────────────────────────

def _extract_edges(geom: Union[Polygon, MultiPolygon]) -> List[LineString]:
    """Extract exterior boundary edges from a Polygon or MultiPolygon."""
    polygons = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    edges = []
    for poly in polygons:
        coords = list(poly.exterior.coords)
        for i in range(len(coords) - 1):
            seg = LineString([coords[i], coords[i + 1]])
            if seg.length > 1e-4:
                edges.append(seg)
    return edges


def get_frontage(
    parcel_geom: Union[Polygon, MultiPolygon],
    road_geoms: Optional[Union[gpd.GeoSeries, gpd.GeoDataFrame]] = None,
    address_point: Optional[Point] = None,
) -> Dict[str, Any]:
    """
    Compute the street-facing edge of the parcel.

    Prefers the edge whose midpoint is closest to the road network; falls back
    to the edge closest to the address point.

    Returns a dict with keys:
        edge, point_a, point_b, length, method, distance_to_ref
    """
    # Pick the sub-polygon most relevant to the address
    if isinstance(parcel_geom, MultiPolygon):
        if address_point is not None:
            sub = min(parcel_geom.geoms, key=lambda p: p.distance(address_point))
        else:
            sub = max(parcel_geom.geoms, key=lambda p: p.area)
        active = sub
    else:
        active = parcel_geom

    edges = _extract_edges(active)
    if not edges:
        raise ValueError("No boundary edges extracted from parcel geometry.")

    # Choose reference geometry (road or address point)
    use_road = False
    if road_geoms is not None:
        if isinstance(road_geoms, gpd.GeoDataFrame):
            road_union = (
                road_geoms[road_geoms.geometry.intersects(active.buffer(100).envelope)]
                .geometry.union_all()
            )
        else:
            road_union = road_geoms[
                road_geoms.intersects(active.buffer(100).envelope)
            ].union_all()

        if not road_union.is_empty:
            ref_geom = road_union
            method = "closest_to_road"
            use_road = True

    if not use_road:
        if address_point is None:
            raise ValueError(
                "Cannot compute frontage: neither road layer nor address point provided."
            )
        ref_geom = address_point
        method = "closest_to_address_point"

    # Score edges: midpoint distance to reference
    edge_metrics = []
    for edge in edges:
        mid = edge.interpolate(0.5, normalized=True)
        mid_d = mid.distance(ref_geom)
        min_d = edge.distance(ref_geom)
        edge_metrics.append((mid_d, min_d, edge))

    min_mid_d = min(m[0] for m in edge_metrics)
    candidates = [e for mid_d, _, e in edge_metrics if mid_d <= min_mid_d + 0.35]
    substantial = [e for e in candidates if e.length >= 0.2]
    best = max(substantial if substantial else candidates, key=lambda e: e.length)

    coords = list(best.coords)
    pt_a = (float(coords[0][0]), float(coords[0][1]))
    pt_b = (float(coords[1][0]), float(coords[1][1]))
    length = float(best.length)

    logger.debug(
        "Frontage (%s): length=%.2f m, A=%s, B=%s", method, length, pt_a, pt_b
    )
    return {
        "edge": best,
        "point_a": pt_a,
        "point_b": pt_b,
        "length": length,
        "method": method,
        "distance_to_ref": float(best.distance(ref_geom)),
    }


# ─────────────────────────────────────────────
#  Neighbour counting
# ─────────────────────────────────────────────

def count_neighbours(
    target_parcel: gpd.GeoSeries,
    parcels_gdf: gpd.GeoDataFrame,
    buffer_dist: float = NEIGHBOUR_BUFFER_M,
) -> int:
    """Return the number of touching neighbour parcels."""
    target_geom = target_parcel.geometry
    target_idx = target_parcel.name
    buffered = target_geom.buffer(buffer_dist)
    candidates = parcels_gdf[parcels_gdf.geometry.intersects(buffered)]
    candidates = candidates[candidates.index != target_idx]
    neighbours = candidates[
        candidates.geometry.apply(
            lambda g: g.touches(target_geom) or g.distance(target_geom) <= buffer_dist
        )
    ]
    return len(neighbours)


# ─────────────────────────────────────────────
#  Per-site processing
# ─────────────────────────────────────────────

def process_site(
    site_id: str,
    site_row: gpd.GeoSeries,
    sites_gdf: gpd.GeoDataFrame,
    parcels_gdf: gpd.GeoDataFrame,
    buildings_gdf: Optional[gpd.GeoDataFrame],
    roads_gdf: Optional[gpd.GeoDataFrame],
    fallback_dist: float = FALLBACK_DIST_M,
    wide_frontage_m: float = WIDE_FRONTAGE_M,
    target_crs: int = BELGIAN_LAMBERT_72,
) -> Dict[str, Any]:
    """
    Process one site and return a result dict for agent2_site.json.
    """
    pt = site_row.geometry
    result: Dict[str, Any] = {
        "site_id": site_id,
        "x": float(pt.x),
        "y": float(pt.y),
        "postcode": str(site_row.get("postcode", "")),
        "street_norm": str(site_row.get("street_norm", "")),
        "hnr_base": str(site_row.get("hnr_base", "")),
        "member_count": int(site_row.get("member_count", 1)),
        "flags": [],
        "parcel_geom_wkt": None,
        "parcel_id": None,
        "building_geom_wkt": None,
        "frontage_point_a": None,
        "frontage_point_b": None,
        "frontage_length_m": None,
        "frontage_method": None,
        "neighbour_count": None,
        "sites_on_parcel": None,
        "status": "OK",
    }

    # Multi-address flag
    if result["member_count"] > 1:
        result["flags"].append("MULTI_ADDRESS")

    # ── 1. Parcel lookup ───────────────────────────────────────────────────
    parcel, parcel_flag = get_target_parcel(pt, parcels_gdf, fallback_dist)

    if parcel_flag == "NO_PARCEL":
        result["flags"].append("NO_PARCEL")
        result["status"] = "NO_PARCEL"
        logger.warning("Site %s: NO_PARCEL", site_id)
        return result

    if parcel_flag == "NEAREST_PARCEL":
        result["flags"].append("NEAREST_PARCEL")

    result["parcel_id"] = str(
        parcel.get("CAPAKEY", parcel.get("capakey", parcel.get("id", parcel.name)))
    )
    result["parcel_geom_wkt"] = parcel.geometry.wkt

    # ── 2. Building footprint ──────────────────────────────────────────────
    building = get_building(parcel.geometry, buildings_gdf)
    if building is not None:
        result["building_geom_wkt"] = building.geometry.wkt

    # ── 3. Frontage edge ──────────────────────────────────────────────────
    try:
        frontage = get_frontage(
            parcel.geometry,
            road_geoms=roads_gdf,
            address_point=pt,
        )
        result["frontage_point_a"] = frontage["point_a"]
        result["frontage_point_b"] = frontage["point_b"]
        result["frontage_length_m"] = round(frontage["length"], 3)
        result["frontage_method"] = frontage["method"]

        if frontage["length"] > wide_frontage_m:
            result["flags"].append("WIDE_FRONTAGE")
    except Exception as exc:
        logger.warning("Site %s: frontage error: %s", site_id, exc)
        result["flags"].append("FRONTAGE_ERROR")

    # ── 4. Neighbours ─────────────────────────────────────────────────────
    result["neighbour_count"] = count_neighbours(parcel, parcels_gdf)

    # ── 5. How many input sites share this parcel ─────────────────────────
    parcel_geom = parcel.geometry
    sites_on_parcel = sites_gdf[sites_gdf.geometry.intersects(parcel_geom.buffer(1.0))]
    result["sites_on_parcel"] = len(sites_on_parcel)

    return result


# ─────────────────────────────────────────────
#  Spatial cache for WFS tiles
# ─────────────────────────────────────────────

class _SpatialCache:
    """Simple grid-based spatial cache to avoid duplicate WFS fetches."""

    def __init__(
        self,
        cell_size: float = 300.0,
        buffer: float = 220.0,
        use_api_cache: bool = True,
    ):
        self.cell_size = cell_size
        self.buffer = buffer
        self.use_api_cache = use_api_cache
        self._parcels: Dict[Tuple[int, int], gpd.GeoDataFrame] = {}
        self._buildings: Dict[Tuple[int, int], Optional[gpd.GeoDataFrame]] = {}
        self._roads: Dict[Tuple[int, int], Optional[gpd.GeoDataFrame]] = {}

    def _key(self, pt: Point) -> Tuple[int, int]:
        return (int(pt.x // self.cell_size), int(pt.y // self.cell_size))

    def _centre(self, key: Tuple[int, int]) -> Point:
        return Point(
            key[0] * self.cell_size + self.cell_size / 2,
            key[1] * self.cell_size + self.cell_size / 2,
        )

    def get(
        self, pt: Point, target_crs: int
    ) -> Tuple[gpd.GeoDataFrame, Optional[gpd.GeoDataFrame], Optional[gpd.GeoDataFrame]]:
        key = self._key(pt)
        if key not in self._parcels:
            centre = self._centre(key)
            data = fetch_flanders_wfs(
                centre,
                buffer_m=self.buffer,
                target_crs=target_crs,
                use_api_cache=self.use_api_cache,
            )
            self._parcels[key] = data.get(
                "parcels", gpd.GeoDataFrame(geometry=[], crs=f"EPSG:{target_crs}")
            )
            self._buildings[key] = data.get("buildings")
            self._roads[key] = data.get("roads")
        return self._parcels[key], self._buildings[key], self._roads[key]


# ─────────────────────────────────────────────
#  Output writers
# ─────────────────────────────────────────────

def _serialise(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, float):
        return None if math.isnan(obj) else obj
    if isinstance(obj, int):
        return obj
    if hasattr(obj, "item"):
        return obj.item()
    if isinstance(obj, (list, tuple)):
        return [_serialise(v) for v in obj]
    return str(obj)


def write_site_json(results: List[Dict], out_path: Path) -> None:
    """Write all site results to agent2_site.json."""
    clean = [
        {k: _serialise(v) for k, v in r.items()}
        for r in results
    ]
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, indent=2, ensure_ascii=False)
    logger.info("Wrote %d site results to %s", len(clean), out_path)


def write_result_gpkg(
    sites_gdf: gpd.GeoDataFrame,
    results: List[Dict],
    parcels_collected: gpd.GeoDataFrame,
    frontage_edges: List[Dict],
    buildings_collected: Optional[gpd.GeoDataFrame],
    roads_collected: Optional[gpd.GeoDataFrame],
    out_path: Path,
    target_crs: int = BELGIAN_LAMBERT_72,
) -> None:
    """Write a multi-layer result GeoPackage."""
    if out_path.exists():
        out_path.unlink()

    # Layer 1: sites with result summary
    site_rows = []
    for r in results:
        site_rows.append({
            "site_id": r["site_id"],
            "parcel_id": r.get("parcel_id"),
            "frontage_m": r.get("frontage_length_m"),
            "flags": ",".join(r.get("flags", [])),
            "status": r.get("status"),
            "member_count": r.get("member_count"),
            "neighbour_count": r.get("neighbour_count"),
        })
    # Merge attribute table (plain DataFrame, no geometry) with sites GeoDataFrame
    import pandas as _pd
    site_attrs = _pd.DataFrame(site_rows)
    sites_merged = sites_gdf[["site_id", "geometry"]].merge(
        site_attrs, on="site_id", how="left"
    )
    sites_merged.to_file(str(out_path), layer="sites", driver="GPKG")

    # Layer 2: parcels
    if not parcels_collected.empty:
        parcels_collected.to_file(str(out_path), layer="parcels", driver="GPKG")

    # Layer 3: frontage edges
    if frontage_edges:
        fe_gdf = gpd.GeoDataFrame(
            [{"site_id": f["site_id"], "length_m": f["length_m"], "method": f["method"]}
             for f in frontage_edges],
            geometry=[f["geometry"] for f in frontage_edges],
            crs=f"EPSG:{target_crs}",
        )
        fe_gdf.to_file(str(out_path), layer="frontage_edges", driver="GPKG")

    # Layer 4: buildings
    if buildings_collected is not None and not buildings_collected.empty:
        buildings_collected.to_file(str(out_path), layer="buildings", driver="GPKG")

    # Layer 5: roads
    if roads_collected is not None and not roads_collected.empty:
        roads_collected.to_file(str(out_path), layer="roads", driver="GPKG")

    layers = [item[0] for item in pyogrio.list_layers(out_path)]
    logger.info("Wrote result GeoPackage: %s (layers: %s)", out_path, layers)


# ─────────────────────────────────────────────
#  Main pipeline
# ─────────────────────────────────────────────

def run(
    sites_gpkg: Path,
    mapping_json: Path,
    parcel_gpkg: Optional[Path],
    parcel_layer: Optional[str],
    building_gpkg: Optional[Path],
    building_layer: Optional[str],
    road_gpkg: Optional[Path],
    road_layer: Optional[str],
    fetch_wfs: bool,
    out_dir: Path,
    fallback_dist: float = FALLBACK_DIST_M,
    wide_frontage_m: float = WIDE_FRONTAGE_M,
    target_crs: int = BELGIAN_LAMBERT_72,
    max_sites: Optional[int] = None,
    use_api_cache: bool = True,
    cancel_check: Optional[Callable[[], None]] = None,
    progress_callback: Optional[Callable[[int, int, Dict], None]] = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Load sites ─────────────────────────────────────────────────────────
    logger.info("Loading sites from %s", sites_gpkg)
    sites_gdf = gpd.read_file(str(sites_gpkg), layer="sites")
    if max_sites is not None and max_sites > 0:
        sites_gdf = sites_gdf.head(max_sites)
        logger.info("Limiting processing to first %d sites (--max-sites)", max_sites)
    if sites_gdf.crs is None:
        sites_gdf = sites_gdf.set_crs(epsg=target_crs)
    elif sites_gdf.crs.to_epsg() != target_crs:
        sites_gdf = sites_gdf.to_crs(epsg=target_crs)
    logger.info("Sites loaded: %d", len(sites_gdf))

    # ── Load local layers (if provided) ────────────────────────────────────
    local_parcels: Optional[gpd.GeoDataFrame] = None
    local_buildings: Optional[gpd.GeoDataFrame] = None
    local_roads: Optional[gpd.GeoDataFrame] = None

    if parcel_gpkg and parcel_gpkg.exists():
        local_parcels = load_gpkg_layer(
            parcel_gpkg, parcel_layer,
            ["parcel", "perceel", "adp", "kadaster"],
            target_crs,
        )
        logger.info("Local parcels: %d", len(local_parcels))

    if building_gpkg and building_gpkg.exists():
        local_buildings = load_gpkg_layer(
            building_gpkg, building_layer,
            ["build", "gebouw", "gbg"],
            target_crs,
        )
        logger.info("Local buildings: %d", len(local_buildings))

    if road_gpkg and road_gpkg.exists():
        local_roads = load_gpkg_layer(
            road_gpkg, road_layer,
            ["road", "weg", "wbn", "street"],
            target_crs,
        )
        logger.info("Local roads: %d", len(local_roads))

    wfs_cache = _SpatialCache(use_api_cache=use_api_cache) if fetch_wfs else None

    # ── Process sites ──────────────────────────────────────────────────────
    results: List[Dict] = []
    parcels_seen: Dict[str, gpd.GeoSeries] = {}
    frontage_edges: List[Dict] = []
    buildings_seen: Dict[str, gpd.GeoSeries] = {}
    roads_collected: Optional[gpd.GeoDataFrame] = local_roads

    total = len(sites_gdf)
    print(f"\nProcessing {total} sites...\n")

    for i, (_, site_row) in enumerate(sites_gdf.iterrows()):
        if cancel_check:
            cancel_check()
        site_id = str(site_row["site_id"])
        pt = site_row.geometry

        # Resolve parcel / building / road data for this site
        if local_parcels is not None:
            parcels_gdf = local_parcels
            buildings_gdf = local_buildings
            roads_gdf = local_roads
        elif wfs_cache is not None:
            parcels_gdf, buildings_gdf, roads_gdf = wfs_cache.get(pt, target_crs)
            if roads_gdf is not None and roads_collected is None:
                roads_collected = roads_gdf
        else:
            logger.error(
                "No parcel source configured. Use --parcel-gpkg or --fetch-wfs."
            )
            sys.exit(1)

        try:
            res = process_site(
                site_id=site_id,
                site_row=site_row,
                sites_gdf=sites_gdf,
                parcels_gdf=parcels_gdf,
                buildings_gdf=buildings_gdf,
                roads_gdf=roads_gdf,
                fallback_dist=fallback_dist,
                wide_frontage_m=wide_frontage_m,
                target_crs=target_crs,
            )
        except Exception as exc:
            logger.error("Site %s failed: %s", site_id, exc)
            res = {
                "site_id": site_id,
                "status": "ERROR",
                "error": str(exc),
                "flags": ["ERROR"],
            }

        results.append(res)
        if progress_callback:
            progress_callback(i + 1, total, {"site_id": site_id})

        # Collect parcel/building geometries for GPKG output
        if res.get("parcel_id") and res.get("parcel_geom_wkt"):
            pid = res["parcel_id"]
            if pid not in parcels_seen:
                from shapely.wkt import loads as wkt_loads
                parcels_seen[pid] = {"parcel_id": pid, "geometry": wkt_loads(res["parcel_geom_wkt"])}

        if res.get("frontage_point_a") and res.get("frontage_point_b"):
            pa = res["frontage_point_a"]
            pb = res["frontage_point_b"]
            frontage_edges.append({
                "site_id": site_id,
                "length_m": res["frontage_length_m"],
                "method": res.get("frontage_method", ""),
                "geometry": LineString([pa, pb]),
            })

        if res.get("building_geom_wkt"):
            from shapely.wkt import loads as wkt_loads
            bkey = res["site_id"]
            if bkey not in buildings_seen:
                buildings_seen[bkey] = {"site_id": bkey, "geometry": wkt_loads(res["building_geom_wkt"])}

        # Progress
        pct = (i + 1) / total * 100
        flags_str = ",".join(res.get("flags", [])) or "OK"
        print(
            f"  [{i+1:>5}/{total}] {site_id}  status={res.get('status','?'):12}  "
            f"flags=[{flags_str}]  frontage={res.get('frontage_length_m') or '-':>7}"
        )

    # ── Build collected GDFs ────────────────────────────────────────────────
    parcels_gdf_out = gpd.GeoDataFrame(
        list(parcels_seen.values()),
        crs=f"EPSG:{target_crs}",
    ) if parcels_seen else gpd.GeoDataFrame(crs=f"EPSG:{target_crs}")

    buildings_gdf_out = gpd.GeoDataFrame(
        list(buildings_seen.values()),
        crs=f"EPSG:{target_crs}",
    ) if buildings_seen else None

    # ── Write outputs ───────────────────────────────────────────────────────
    json_out = out_dir / "agent2_site.json"
    gpkg_out = out_dir / "agent2_result.gpkg"

    write_site_json(results, json_out)
    write_result_gpkg(
        sites_gdf=sites_gdf,
        results=results,
        parcels_collected=parcels_gdf_out,
        frontage_edges=frontage_edges,
        buildings_collected=buildings_gdf_out,
        roads_collected=roads_collected,
        out_path=gpkg_out,
        target_crs=target_crs,
    )

    # ── Summary ─────────────────────────────────────────────────────────────
    ok = sum(1 for r in results if r.get("status") == "OK")
    no_parcel = sum(1 for r in results if "NO_PARCEL" in r.get("flags", []))
    nearest = sum(1 for r in results if "NEAREST_PARCEL" in r.get("flags", []))
    wide = sum(1 for r in results if "WIDE_FRONTAGE" in r.get("flags", []))
    multi = sum(1 for r in results if "MULTI_ADDRESS" in r.get("flags", []))

    print("\n" + "=" * 60)
    print("  Agent 2 complete")
    print(f"  Sites processed   : {total}")
    print(f"  OK                : {ok}")
    print(f"  NO_PARCEL         : {no_parcel}")
    print(f"  NEAREST_PARCEL    : {nearest}")
    print(f"  WIDE_FRONTAGE     : {wide}")
    print(f"  MULTI_ADDRESS     : {multi}")
    print(f"  Site JSON         : {json_out}")
    print(f"  Result GPKG       : {gpkg_out}")
    print("=" * 60 + "\n")


# ─────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Agent 2 - Parcel (CadGIS lookup + frontage)\n"
            "Reads Agent 1 outputs, resolves parcels, computes frontage, "
            "writes agent2_site.json and agent2_result.gpkg."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--sites-gpkg", required=True,
        help="Path to agent1_sites.gpkg (Agent 1 output).",
    )
    p.add_argument(
        "--mapping-json", default=None,
        help="Path to agent1_mapping.json (Agent 1 output, optional for Agent 2).",
    )
    p.add_argument(
        "--parcel-gpkg", default=None,
        help="Path to a local GPKG containing cadastral parcels.",
    )
    p.add_argument(
        "--parcel-layer", default=None,
        help="Layer name within the parcel GPKG (auto-detected if omitted).",
    )
    p.add_argument(
        "--building-gpkg", default=None,
        help="Path to a GPKG containing building footprints (optional).",
    )
    p.add_argument(
        "--building-layer", default=None,
        help="Layer name within the building GPKG.",
    )
    p.add_argument(
        "--road-gpkg", default=None,
        help="Path to a GPKG containing road network (optional).",
    )
    p.add_argument(
        "--road-layer", default=None,
        help="Layer name within the road GPKG.",
    )
    p.add_argument(
        "--fetch-wfs", action="store_true",
        help="Fetch parcels, buildings and roads from the Flemish CadGIS WFS (Flanders only).",
    )
    p.add_argument(
        "--out-dir", "-o", default=".",
        help="Output directory (default: current directory).",
    )
    p.add_argument(
        "--fallback-dist", type=float, default=FALLBACK_DIST_M,
        help=f"Nearest-parcel fallback distance in metres (default: {FALLBACK_DIST_M}).",
    )
    p.add_argument(
        "--wide-frontage", type=float, default=WIDE_FRONTAGE_M,
        help=f"Frontage threshold for WIDE_FRONTAGE flag in metres (default: {WIDE_FRONTAGE_M}).",
    )
    p.add_argument(
        "--max-sites", type=int, default=None,
        help="Process at most N sites (useful for quick testing).",
    )
    p.add_argument("--verbose", "-v", action="store_true", help="DEBUG-level logging.")
    return p


def main(argv: Optional[List[str]] = None) -> None:
    from typing import Optional
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.parcel_gpkg and not args.fetch_wfs:
        parser.error("Provide --parcel-gpkg or --fetch-wfs.")

    run(
        sites_gpkg=Path(args.sites_gpkg).resolve(),
        mapping_json=Path(args.mapping_json).resolve() if args.mapping_json else None,
        parcel_gpkg=Path(args.parcel_gpkg).resolve() if args.parcel_gpkg else None,
        parcel_layer=args.parcel_layer,
        building_gpkg=Path(args.building_gpkg).resolve() if args.building_gpkg else None,
        building_layer=args.building_layer,
        road_gpkg=Path(args.road_gpkg).resolve() if args.road_gpkg else None,
        road_layer=args.road_layer,
        fetch_wfs=args.fetch_wfs,
        out_dir=Path(args.out_dir).resolve(),
        fallback_dist=args.fallback_dist,
        wide_frontage_m=args.wide_frontage,
        max_sites=args.max_sites,
    )


if __name__ == "__main__":
    main()
