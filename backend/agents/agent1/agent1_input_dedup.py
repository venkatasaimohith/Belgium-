#!/usr/bin/env python3
"""
Agent 1 — Input + Deduplication
================================
Reads a GPKG or KML file containing Belgian address points, normalises text,
deduplicates addresses that share the same physical dwelling (e.g. 1A/1B/1C),
performs a spatial coherence check, assigns a unique site_id to each group, and
writes two output files consumed by Agent 2:

  agent1_sites.gpkg     – one row per site (representative point, EPSG:31370)
  agent1_mapping.json   – mapping of site_id → list of member address records

Usage
-----
    python agent1_input_dedup.py --input addresses.gpkg --out-dir ./output
    python agent1_input_dedup.py --input addresses.kml  --out-dir ./output

    # Inspect the input without writing anything
    python agent1_input_dedup.py --input addresses.gpkg --inspect

Run with --help for all options.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely.geometry import Point

# ─────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────
BELGIAN_LAMBERT_72 = 31370
WGS84 = 4326

# Maximum allowed spread (metres) within a site group.
# Groups whose members are further apart than this are split.
MAX_SITE_RADIUS_M = 30.0

# Logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agent1")


# ─────────────────────────────────────────────
#  Text normalisation helpers
# ─────────────────────────────────────────────

def _strip_accents(text: str) -> str:
    """Remove diacritics / accents from a string."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalise_street(raw: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    if not isinstance(raw, str):
        return ""
    return re.sub(r"\s+", " ", _strip_accents(raw).lower().strip())


# Pattern: digits (base), optional letter suffix, optional bus/box suffix
_HOUSENR_RE = re.compile(
    r"^\s*(?P<base>\d+)\s*(?P<letter>[a-zA-Z]?)\s*"
    r"(?:bus\s*(?P<bus>\d+)|/\s*(?P<bus2>\d+))?\s*$",
    re.IGNORECASE,
)


def parse_house_number(raw: Any) -> Dict[str, str]:
    """
    Split a house number into base, letter and bus components.

    Examples:
        "12"       -> {'base': '12', 'letter': '',  'bus': ''}
        "12A"      -> {'base': '12', 'letter': 'A', 'bus': ''}
        "12 bus 3" -> {'base': '12', 'letter': '',  'bus': '3'}
    """
    raw_str = str(raw).strip() if raw is not None else ""
    m = _HOUSENR_RE.match(raw_str)
    if m:
        bus = m.group("bus") or m.group("bus2") or ""
        return {
            "base": m.group("base"),
            "letter": (m.group("letter") or "").upper(),
            "bus": bus,
        }
    return {"base": raw_str, "letter": "", "bus": ""}


# ─────────────────────────────────────────────
#  Input loading
# ─────────────────────────────────────────────

def load_input(path: Path, target_crs: int = BELGIAN_LAMBERT_72) -> gpd.GeoDataFrame:
    """
    Load address points from a GPKG or KML file.

    KML is assumed WGS84 on read; both formats are reprojected to target_crs.
    Rows without valid geometry are logged and removed.
    """
    suffix = path.suffix.lower()

    if suffix == ".kml":
        logger.info("Reading KML file: %s", path)
        gdf = gpd.read_file(str(path), driver="KML")
        if gdf.crs is None:
            gdf = gdf.set_crs(epsg=WGS84)
    elif suffix == ".gpkg":
        layers = [item[0] for item in pyogrio.list_layers(path)]
        logger.info("Layers in %s: %s", path.name, layers)
        candidates = [
            l for l in layers
            if any(k in l.lower() for k in ("adres", "address", "ssv", "locatie"))
        ]
        layer = candidates[0] if candidates else layers[0]
        logger.info("Loading layer '%s'", layer)
        gdf = gpd.read_file(str(path), layer=layer)
        if gdf.crs is None:
            logger.warning("No CRS on layer; assuming EPSG:%d", target_crs)
            gdf = gdf.set_crs(epsg=target_crs)
    else:
        raise ValueError(f"Unsupported file format: {suffix!r}. Expected .gpkg or .kml")

    # Remove rows with no / empty geometry
    original_count = len(gdf)
    null_mask = gdf.geometry.isna() | gdf.geometry.is_empty
    if null_mask.any():
        logger.warning(
            "%d row(s) have missing/empty geometry and will be skipped.",
            null_mask.sum(),
        )
        gdf = gdf[~null_mask].copy()

    # Ensure points (convert anything non-point to centroid)
    non_point = gdf[~gdf.geometry.geom_type.isin(["Point", "MultiPoint"])]
    if not non_point.empty:
        logger.warning(
            "%d row(s) have non-Point geometry; converting to centroid.",
            len(non_point),
        )
        gdf.loc[non_point.index, "geometry"] = non_point.geometry.centroid

    # Reproject
    if gdf.crs.to_epsg() != target_crs:
        logger.info("Reprojecting from %s to EPSG:%d", gdf.crs, target_crs)
        gdf = gdf.to_crs(epsg=target_crs)

    logger.info(
        "Loaded %d address rows (dropped %d without geometry)",
        len(gdf),
        original_count - len(gdf),
    )
    return gdf.reset_index(drop=True)


# ─────────────────────────────────────────────
#  Column detection
# ─────────────────────────────────────────────

def _find_col(columns: List[str], keywords: List[str]) -> Optional[str]:
    """Return first column whose name contains any keyword (case-insensitive)."""
    for col in columns:
        low = col.lower()
        if any(k in low for k in keywords):
            return col
    return None


def detect_columns(gdf: gpd.GeoDataFrame) -> Dict[str, Optional[str]]:
    """
    Auto-detect the street, house-number, postcode and municipality columns.

    Returns a dict with keys:
        'street', 'housenr', 'postcode', 'municipality'
    """
    cols = list(gdf.columns)
    mapping = {
        "street": _find_col(cols, ["straat", "street", "rue", "strasse", "naam"]),
        "housenr": _find_col(cols, ["huis", "huisnr", "number", "nummer", "nr", "num"]),
        "postcode": _find_col(cols, ["post", "zip", "npa", "plz"]),
        "municipality": _find_col(cols, ["gemeente", "city", "stad", "ville", "mun"]),
    }
    logger.info("Detected columns: %s", mapping)
    return mapping


# ─────────────────────────────────────────────
#  Deduplication & site grouping
# ─────────────────────────────────────────────

def _spatial_coherence_ok(group: gpd.GeoDataFrame, max_radius: float) -> bool:
    """Return True when all group members lie within max_radius of the centroid."""
    if len(group) <= 1:
        return True
    cx = group.geometry.x.mean()
    cy = group.geometry.y.mean()
    centre = Point(cx, cy)
    max_dist = group.geometry.apply(lambda g: g.distance(centre)).max()
    return max_dist <= max_radius


def deduplicate(
    gdf: gpd.GeoDataFrame,
    col_map: Dict[str, Optional[str]],
    max_radius: float = MAX_SITE_RADIUS_M,
    target_crs: int = BELGIAN_LAMBERT_72,
    progress_callback: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
) -> Tuple[gpd.GeoDataFrame, Dict[str, List[Dict]]]:
    """
    Group addresses sharing the same physical dwelling.

    Dedup key = (postcode, normalised_street, base_house_number).
    Letter suffixes (A/B/C) and bus numbers are stripped so 12A, 12B and
    12 bus 3 all map to the same group — provided they are spatially close.

    Returns
    -------
    sites_gdf : GeoDataFrame, one row per site (representative point)
    mapping   : dict  site_id -> list of member address dicts
    """
    street_col = col_map["street"]
    housenr_col = col_map["housenr"]
    post_col = col_map["postcode"]

    rows = []
    for idx, row in gdf.iterrows():
        rec: Dict[str, Any] = {"_orig_idx": idx}

        street_raw = str(row[street_col]) if street_col and pd.notna(row[street_col]) else ""
        housenr_raw = row[housenr_col] if housenr_col else ""
        postcode_raw = str(row[post_col]).strip() if post_col and pd.notna(row[post_col]) else ""

        rec["street_norm"] = normalise_street(street_raw)
        rec["postcode"] = postcode_raw

        parsed = parse_house_number(housenr_raw)
        rec["hnr_base"] = parsed["base"]
        rec["hnr_letter"] = parsed["letter"]
        rec["hnr_bus"] = parsed["bus"]

        rec["dedup_key"] = f"{postcode_raw}|{rec['street_norm']}|{parsed['base']}"

        for col in gdf.columns:
            if col != "geometry":
                rec[f"orig_{col}"] = row[col]

        rec["geometry"] = row.geometry
        rows.append(rec)
        if progress_callback:
            progress_callback(len(rows), len(gdf), {})

    work_df = pd.DataFrame(rows)

    sites_list: List[Dict] = []
    mapping: Dict[str, List[Dict]] = {}
    site_counter = 0

    for dedup_key, group_df in work_df.groupby("dedup_key", sort=False):
        group_gdf = gpd.GeoDataFrame(
            group_df, geometry="geometry", crs=f"EPSG:{target_crs}"
        )

        if _spatial_coherence_ok(group_gdf, max_radius):
            sub_groups = [group_gdf]
        else:
            logger.warning(
                "Group '%s' (%d members) exceeds spatial radius %.0f m -> splitting.",
                dedup_key,
                len(group_gdf),
                max_radius,
            )
            sub_groups = [group_gdf.iloc[[i]] for i in range(len(group_gdf))]

        for sub in sub_groups:
            site_counter += 1
            site_id = f"SITE_{site_counter:06d}"

            cx = sub.geometry.x.mean()
            cy = sub.geometry.y.mean()
            rep_pt = Point(cx, cy)

            members = []
            for _, mrow in sub.iterrows():
                member_rec = {
                    k.replace("orig_", ""): v
                    for k, v in mrow.items()
                    if k.startswith("orig_")
                }
                member_rec["_orig_idx"] = int(mrow["_orig_idx"])
                member_rec["hnr_base"] = mrow["hnr_base"]
                member_rec["hnr_letter"] = mrow["hnr_letter"]
                member_rec["hnr_bus"] = mrow["hnr_bus"]
                members.append(member_rec)

            mapping[site_id] = members

            first = sub.iloc[0]
            sites_list.append({
                "site_id": site_id,
                "postcode": first["postcode"],
                "street_norm": first["street_norm"],
                "hnr_base": first["hnr_base"],
                "member_count": len(sub),
                "geometry": rep_pt,
            })

    sites_gdf = gpd.GeoDataFrame(
        sites_list, geometry="geometry", crs=f"EPSG:{target_crs}"
    )
    logger.info(
        "Deduplication: %d input rows -> %d sites.", len(gdf), len(sites_gdf)
    )
    return sites_gdf, mapping


# ─────────────────────────────────────────────
#  Output writers
# ─────────────────────────────────────────────

def write_sites_gpkg(sites_gdf: gpd.GeoDataFrame, out_path: Path) -> None:
    """Write the site list to a single-layer GeoPackage."""
    if out_path.exists():
        out_path.unlink()
    sites_gdf.to_file(str(out_path), layer="sites", driver="GPKG")
    logger.info("Wrote %d sites to %s", len(sites_gdf), out_path)


def write_mapping_json(mapping: Dict[str, List[Dict]], out_path: Path) -> None:
    """Write the site_id -> member address mapping to JSON."""

    def _serialise(obj: Any) -> Any:
        if isinstance(obj, (int, float, str, bool)) or obj is None:
            return obj
        if hasattr(obj, "item"):          # numpy scalar
            return obj.item()
        if hasattr(obj, "__float__"):
            return float(obj)
        return str(obj)

    clean: Dict[str, List[Dict]] = {
        site_id: [{k: _serialise(v) for k, v in m.items()} for m in members]
        for site_id, members in mapping.items()
    }

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, indent=2, ensure_ascii=False)
    logger.info("Wrote mapping for %d sites to %s", len(clean), out_path)


# ─────────────────────────────────────────────
#  Inspection helper
# ─────────────────────────────────────────────

def inspect(gdf: gpd.GeoDataFrame, col_map: Dict[str, Optional[str]]) -> None:
    """Print a data summary to stdout."""
    print("=" * 70)
    print(f"  Rows loaded  : {len(gdf)}")
    print(f"  CRS          : {gdf.crs}")
    print(f"  Columns      : {list(gdf.columns)}")
    print(f"  Detected     : {col_map}")
    print(f"  Bounding box : {gdf.total_bounds.tolist()}")
    print("=" * 70)
    street_col = col_map["street"]
    housenr_col = col_map["housenr"]
    print(
        f"{'#':>4} | {'Index':>6} | {'Address':<45} | {'X':>12} | {'Y':>12}"
    )
    print("-" * 88)
    for i, (idx, row) in enumerate(gdf.head(20).iterrows()):
        street = str(row[street_col]) if street_col else "-"
        nr = str(row[housenr_col]) if housenr_col else "-"
        addr = f"{street} {nr}"
        print(
            f"{i+1:4d} | {str(idx):>6} | {addr[:45]:<45} | "
            f"{row.geometry.x:12.2f} | {row.geometry.y:12.2f}"
        )
    if len(gdf) > 20:
        print(f"  ... ({len(gdf) - 20} more rows not shown)")
    print("=" * 70)


# ─────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Agent 1 - Input + Deduplication\n"
            "Reads GPKG/KML address file, deduplicates physical dwellings,\n"
            "writes agent1_sites.gpkg and agent1_mapping.json."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--input", "-i", required=True, help="Input GPKG or KML file.")
    p.add_argument(
        "--out-dir", "-o", default=".",
        help="Output directory (default: current directory).",
    )
    p.add_argument(
        "--max-radius", type=float, default=MAX_SITE_RADIUS_M,
        help=f"Max spread (m) before splitting a site group (default: {MAX_SITE_RADIUS_M}).",
    )
    p.add_argument(
        "--inspect", action="store_true",
        help="Print a data summary and exit without writing output.",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return p


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        logger.error("Input file not found: %s", input_path)
        sys.exit(1)

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    gdf = load_input(input_path)
    col_map = detect_columns(gdf)

    if args.inspect:
        inspect(gdf, col_map)
        return

    sites_gdf, mapping = deduplicate(gdf, col_map, max_radius=args.max_radius)

    sites_out = out_dir / "agent1_sites.gpkg"
    mapping_out = out_dir / "agent1_mapping.json"
    write_sites_gpkg(sites_gdf, sites_out)
    write_mapping_json(mapping, mapping_out)

    print("\n" + "=" * 60)
    print("  Agent 1 complete")
    print(f"  Input rows    : {len(gdf)}")
    print(f"  Sites created : {len(sites_gdf)}")
    multi = sites_gdf[sites_gdf["member_count"] > 1]
    print(f"  Multi-address : {len(multi)} site(s) with >1 address")
    print(f"  Sites GPKG    : {sites_out}")
    print(f"  Mapping JSON  : {mapping_out}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
