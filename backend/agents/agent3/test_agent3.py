"""Tests for the single best-panorama Agent 3 flow."""

import json
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
from shapely.geometry import LineString, Point

from agent3_image import DEFAULT_WORKERS, run
import frontage_downloader as frontage
from frontage_downloader import FOVS, angular_difference, road_probe_points


def test_agent3_configuration_is_fov90_110_with_ten_workers():
    assert FOVS == (90.0, 110.0)
    assert DEFAULT_WORKERS == 10


def test_angular_difference_wraps_north():
    assert angular_difference(355.0, 5.0) == 10.0


def test_road_probe_points_span_nearest_road():
    roads = gpd.GeoDataFrame(
        geometry=[LineString([(-100, 0), (100, 0)])], crs="EPSG:31370"
    )
    probes = road_probe_points(roads, Point(0, 20), half_span_m=20, spacing_m=10)
    assert [(round(point.x), round(point.y)) for point in probes] == [
        (-20, 0), (-10, 0), (0, 0), (10, 0), (20, 0)
    ]


def test_panorama_ranking_is_independent_of_facade_normal_sign(monkeypatch):
    geometry = SimpleNamespace(
        lambert_to_wgs84=lambda x, y: (y, x),
        bearing_deg=lambda _lat, _lng, _target_lat, target_lng: target_lng % 360,
    )
    monkeypatch.setattr(frontage, "_implementation", lambda: geometry)
    panoramas = [{
        "pano_id": "P1", "cam_lambert_x": 0.0, "cam_lambert_y": -10.0,
        "cam_lat": -10.0, "cam_lng": 0.0,
    }]
    facade = LineString([(-5.0, 0.0), (5.0, 0.0)])

    positive = frontage.rank_panoramas(
        panoramas, facade, Point(0.0, 0.0), Point(0.0, 0.0), (0.0, 1.0)
    )
    negative = frontage.rank_panoramas(
        panoramas, facade, Point(0.0, 0.0), Point(0.0, 0.0), (0.0, -1.0)
    )

    assert len(positive) == len(negative) == 1
    assert positive[0]["selection_score"] == negative[0]["selection_score"]
    assert positive[0]["frontality"] == negative[0]["frontality"] == 1.0
    assert positive[0]["signed_frontality"] == -1.0
    assert negative[0]["signed_frontality"] == 1.0


def test_dry_run_writes_both_fovs(tmp_path: Path):
    site_json = tmp_path / "agent2_site.json"
    site_json.write_text(
        json.dumps(
            [
                {
                    "site_id": "SITE_1",
                    "x": 10.0,
                    "y": 20.0,
                    "frontage_point_a": [5.0, 10.0],
                    "frontage_point_b": [15.0, 10.0],
                    "status": "OK",
                }
            ]
        ),
        encoding="utf-8",
    )
    results = run(site_json, tmp_path, dry_run=True)
    assert [image["fov"] for image in results[0]["images"]] == [90.0, 110.0]
    assert all(Path(image["file_path"]).is_file() for image in results[0]["images"])
    assert results[0]["images"][1]["fallback_only"] is True
    assert (tmp_path / "agent3_images.json").is_file()
