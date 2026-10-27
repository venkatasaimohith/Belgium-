#!/usr/bin/env python3
"""Agent 3 - select one best panorama and download FOV 90/110 images.

The live flow has one acquisition strategy only: road probes plus facade
apparent-width/frontality ranking implemented in ``frontage_downloader``.
Mailbox detection belongs to Agent 5 and is intentionally absent here.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PIL import Image, ImageDraw

try:
    from .frontage_downloader import FOVS, IMAGE_SIZE, process_site as download_site
except ImportError:
    from frontage_downloader import FOVS, IMAGE_SIZE, process_site as download_site


logger = logging.getLogger("agent3")
DEFAULT_WORKERS = 10


def _dry_run_site(site: Dict[str, Any], out_images_dir: Path) -> Dict[str, Any]:
    """Create contract-compatible placeholders when no API key is configured."""
    site_id = str(site["site_id"])
    site_dir = out_images_dir / site_id
    site_dir.mkdir(parents=True, exist_ok=True)
    width, height = (int(value) for value in IMAGE_SIZE.split("x", 1))
    x, y = float(site.get("x") or 0.0), float(site.get("y") or 0.0)
    point_a = site.get("frontage_point_a") or [x - 5.0, y]
    point_b = site.get("frontage_point_b") or [x + 5.0, y]
    mid_x = (float(point_a[0]) + float(point_b[0])) / 2.0
    mid_y = (float(point_a[1]) + float(point_b[1])) / 2.0
    cam_x, cam_y = mid_x, mid_y - 20.0
    heading = math.degrees(math.atan2(mid_x - cam_x, mid_y - cam_y)) % 360.0
    images = []
    for priority, fov in enumerate(FOVS, 1):
        path = site_dir / f"{site_id}_dry_fov{fov:g}.jpg"
        canvas = Image.new("RGB", (width, height), (203, 213, 225))
        draw = ImageDraw.Draw(canvas)
        draw.text((20, 20), f"DRY RUN {site_id} FOV {fov:g}", fill=(15, 23, 42))
        canvas.save(path, format="JPEG", quality=90)
        images.append(
            {
                "pano_id": f"dry-{site_id}",
                "lat": None,
                "lon": None,
                "cam_x": cam_x,
                "cam_y": cam_y,
                "date": None,
                "heading": heading,
                "fov": fov,
                "pitch": 0.0,
                "image_width": width,
                "image_height": height,
                "file_path": str(path.resolve()),
                "left_corner": point_a,
                "right_corner": point_b,
                "frontage_source": "agent2_parcel",
                "image_type": f"frontage_fov{fov:g}",
                "viewpoint": "center",
                "detection_priority": priority,
                "fallback_only": fov > min(FOVS),
                "rectified": False,
                "source_heading": heading,
            }
        )
    return {
        "site_id": site_id,
        "postcode": site.get("postcode"),
        "street_norm": site.get("street_norm"),
        "hnr_base": site.get("hnr_base"),
        "x": site.get("x"),
        "y": site.get("y"),
        "flags": sorted(set(list(site.get("flags") or []) + ["DRY_RUN"])),
        "images": images,
        "status": "OK",
        "requested_year": "latest",
        "acquisition_flow": "best_front_facing_panorama_fov90_110",
    }


def run(
    site_json: Path,
    out_dir: Path,
    *,
    agent1_mapping_json: Optional[Path] = None,
    api_key: Optional[str] = None,
    dry_run: bool = False,
    max_sites: Optional[int] = None,
    image_size: str = IMAGE_SIZE,
    pitch: float = 0.0,
    capture_year: Optional[int] = None,
    use_api_cache: bool = True,
    workers: int = DEFAULT_WORKERS,
    cancel_check: Optional[Callable[[], None]] = None,
    progress_callback: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
    **_removed_legacy_options: Any,
) -> List[Dict[str, Any]]:
    """Run the single Agent 3 acquisition strategy with at most 10 workers."""
    sites = json.loads(Path(site_json).read_text(encoding="utf-8"))
    if max_sites is not None:
        sites = sites[:max_sites]
    mapping: Dict[str, List[Dict[str, Any]]] = {}
    if agent1_mapping_json and Path(agent1_mapping_json).is_file():
        mapping = json.loads(Path(agent1_mapping_json).read_text(encoding="utf-8"))
    out_dir = Path(out_dir)
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    def process_one(site: Dict[str, Any]) -> Dict[str, Any]:
        if cancel_check:
            cancel_check()
        if dry_run or not api_key:
            return _dry_run_site(site, images_dir)
        try:
            return download_site(
                site,
                api_key=api_key,
                out_images_dir=images_dir,
                agent1_members=mapping.get(str(site.get("site_id"))),
                image_size=image_size,
                pitch=pitch,
                capture_year=capture_year,
                cancel_check=cancel_check,
                use_api_cache=use_api_cache,
            )
        except Exception as exc:
            logger.exception("%s: best-panorama download failed: %s", site.get("site_id"), exc)
            return {
                "site_id": site.get("site_id"),
                "postcode": site.get("postcode"),
                "street_norm": site.get("street_norm"),
                "hnr_base": site.get("hnr_base"),
                "x": site.get("x"),
                "y": site.get("y"),
                "flags": sorted(set(list(site.get("flags") or []) + ["DOWNLOAD_ERROR"])),
                "images": [],
                "status": "NO_STREETVIEW",
                "error": str(exc),
                "acquisition_flow": "best_front_facing_panorama_fov90_110",
            }

    worker_count = min(DEFAULT_WORKERS, max(1, int(workers)), max(1, len(sites)))
    if len(sites) > 1 and worker_count > 1:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            ordered: List[Optional[Dict[str, Any]]] = [None] * len(sites)
            futures = {
                executor.submit(process_one, site): index
                for index, site in enumerate(sites)
            }
            completed = 0
            for future in as_completed(futures):
                ordered[futures[future]] = future.result()
                completed += 1
                if progress_callback:
                    progress_callback(completed, len(sites), {})
            results = [item for item in ordered if item is not None]
    else:
        results = []
        for site in sites:
            results.append(process_one(site))
            if progress_callback:
                progress_callback(len(results), len(sites), {})

    for index, result in enumerate(results, 1):
        if cancel_check:
            cancel_check()
        print(
            f"  [{index:>5}/{len(results)}] {result['site_id']} "
            f"status={result['status']:14} images={len(result.get('images') or [])}"
        )
    output_path = out_dir / "agent3_images.json"
    output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Agent 3 - best front-facing panorama at FOV 90 and 110"
    )
    parser.add_argument("--site-json", required=True)
    parser.add_argument("--out-dir", "-o", default=".")
    parser.add_argument("--agent1-mapping-json")
    parser.add_argument("--api-key")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-sites", type=int)
    parser.add_argument("--pitch", type=float, default=0.0)
    parser.add_argument("--capture-year", type=int)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--no-api-cache", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    run(
        site_json=Path(args.site_json).resolve(),
        out_dir=Path(args.out_dir).resolve(),
        agent1_mapping_json=(Path(args.agent1_mapping_json).resolve() if args.agent1_mapping_json else None),
        api_key=args.api_key,
        dry_run=args.dry_run,
        max_sites=args.max_sites,
        pitch=args.pitch,
        capture_year=args.capture_year,
        workers=max(1, args.workers),
        use_api_cache=not args.no_api_cache,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
