"""
Unit tests for Agent 2 — Parcel (CadGIS lookup + frontage).

Run with:
    cd agent2
    python -m pytest test_agent2.py -v
or:
    python test_agent2.py
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon

from agent2_parcel import (
    get_target_parcel,
    get_building,
    get_frontage,
    count_neighbours,
    write_site_json,
    write_result_gpkg,
    process_site,
)

EPSG = 31370


def _gdf(rows, crs=EPSG):
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=f"EPSG:{crs}")


# ────────────────────────────────────────────────────────────────
#  Synthetic fixtures (same row-house layout as cadgis tests)
# ────────────────────────────────────────────────────────────────

def _make_parcels():
    p1 = Polygon([(150000, 170000), (150008, 170000), (150008, 170025), (150000, 170025)])
    p2 = Polygon([(150008, 170000), (150016, 170000), (150016, 170025), (150008, 170025)])
    p3 = Polygon([(150016, 170000), (150024, 170000), (150024, 170025), (150016, 170025)])
    return _gdf([
        {"CAPAKEY": "P1", "id": "P1", "geometry": p1},
        {"CAPAKEY": "P2", "id": "P2", "geometry": p2},
        {"CAPAKEY": "P3", "id": "P3", "geometry": p3},
    ])


def _make_buildings():
    b1 = Polygon([(150001, 170003), (150007, 170003), (150007, 170015), (150001, 170015)])
    b2 = Polygon([(150009, 170003), (150015, 170003), (150015, 170015), (150009, 170015)])
    return _gdf([
        {"building_id": "B1", "geometry": b1},
        {"building_id": "B2", "geometry": b2},
    ])


def _make_roads():
    road = LineString([(149990, 169995), (150035, 169995)])
    return _gdf([{"name": "Kerkstraat", "geometry": road}])


def _make_sites():
    return _gdf([
        {"site_id": "SITE_000001", "postcode": "1700", "street_norm": "kerkstraat",
         "hnr_base": "12", "member_count": 1, "geometry": Point(150012, 170010)},
    ])


# ────────────────────────────────────────────────────────────────
#  Tests
# ────────────────────────────────────────────────────────────────

class TestGetTargetParcel(unittest.TestCase):
    def setUp(self):
        self.parcels = _make_parcels()

    def test_contains(self):
        parcel, flag = get_target_parcel(Point(150012, 170010), self.parcels)
        self.assertEqual(flag, "contains")
        self.assertEqual(str(parcel["CAPAKEY"]), "P2")

    def test_nearest_fallback(self):
        parcel, flag = get_target_parcel(
            Point(150012, 169998), self.parcels, fallback_dist=15.0
        )
        self.assertEqual(flag, "NEAREST_PARCEL")
        self.assertEqual(str(parcel["CAPAKEY"]), "P2")

    def test_no_parcel(self):
        parcel, flag = get_target_parcel(
            Point(999999, 999999), self.parcels, fallback_dist=5.0
        )
        self.assertEqual(flag, "NO_PARCEL")
        self.assertIsNone(parcel)


class TestGetBuilding(unittest.TestCase):
    def setUp(self):
        self.buildings = _make_buildings()
        self.parcels = _make_parcels()

    def test_finds_correct_building(self):
        parcel_p2 = self.parcels[self.parcels["id"] == "P2"].iloc[0]
        bld = get_building(parcel_p2.geometry, self.buildings)
        self.assertIsNotNone(bld)
        self.assertEqual(bld["building_id"], "B2")

    def test_no_buildings(self):
        parcel_p2 = self.parcels[self.parcels["id"] == "P2"].iloc[0]
        bld = get_building(parcel_p2.geometry, None)
        self.assertIsNone(bld)


class TestGetFrontage(unittest.TestCase):
    def setUp(self):
        self.parcels = _make_parcels()
        self.roads = _make_roads()
        self.parcel_geom = self.parcels[self.parcels["id"] == "P2"].iloc[0].geometry

    def test_road_frontage_length(self):
        fr = get_frontage(self.parcel_geom, road_geoms=self.roads)
        self.assertTrue(math.isclose(fr["length"], 8.0, abs_tol=1e-2))
        self.assertEqual(fr["method"], "closest_to_road")

    def test_address_point_fallback(self):
        # Place the address point very close to the front (south) edge of P2
        # so that edge wins over the 25m-long side walls.
        fr = get_frontage(
            self.parcel_geom, road_geoms=None, address_point=Point(150012, 170001)
        )
        self.assertTrue(math.isclose(fr["length"], 8.0, abs_tol=1e-2))
        self.assertEqual(fr["method"], "closest_to_address_point")

    def test_no_reference_raises(self):
        with self.assertRaises(ValueError):
            get_frontage(self.parcel_geom, road_geoms=None, address_point=None)


class TestCountNeighbours(unittest.TestCase):
    def test_two_neighbours(self):
        parcels = _make_parcels()
        target = parcels[parcels["id"] == "P2"].iloc[0]
        n = count_neighbours(target, parcels, buffer_dist=1.0)
        self.assertEqual(n, 2)

    def test_corner_parcel(self):
        parcels = _make_parcels()
        target = parcels[parcels["id"] == "P1"].iloc[0]
        n = count_neighbours(target, parcels, buffer_dist=1.0)
        self.assertEqual(n, 1)


class TestProcessSite(unittest.TestCase):
    def setUp(self):
        self.parcels = _make_parcels()
        self.buildings = _make_buildings()
        self.roads = _make_roads()
        self.sites = _make_sites()

    def test_ok_site(self):
        site_row = self.sites.iloc[0]
        res = process_site(
            site_id="SITE_000001",
            site_row=site_row,
            sites_gdf=self.sites,
            parcels_gdf=self.parcels,
            buildings_gdf=self.buildings,
            roads_gdf=self.roads,
        )
        self.assertEqual(res["status"], "OK")
        self.assertIsNotNone(res["frontage_length_m"])
        self.assertTrue(math.isclose(res["frontage_length_m"], 8.0, abs_tol=0.1))
        self.assertEqual(res["neighbour_count"], 2)

    def test_no_parcel_site(self):
        site_row = self.sites.iloc[0].copy()
        site_row["geometry"] = Point(999999, 999999)
        res = process_site(
            site_id="SITE_FARAWAY",
            site_row=site_row,
            sites_gdf=self.sites,
            parcels_gdf=self.parcels,
            buildings_gdf=self.buildings,
            roads_gdf=self.roads,
            fallback_dist=5.0,
        )
        self.assertEqual(res["status"], "NO_PARCEL")
        self.assertIn("NO_PARCEL", res["flags"])

    def test_wide_frontage_flag(self):
        # Shrink wide_frontage threshold so 8m triggers it
        site_row = self.sites.iloc[0]
        res = process_site(
            site_id="SITE_000001",
            site_row=site_row,
            sites_gdf=self.sites,
            parcels_gdf=self.parcels,
            buildings_gdf=None,
            roads_gdf=self.roads,
            wide_frontage_m=5.0,  # 8m > 5m -> flag
        )
        self.assertIn("WIDE_FRONTAGE", res["flags"])


class TestOutputFiles(unittest.TestCase):
    def test_write_site_json(self):
        results = [
            {"site_id": "SITE_000001", "status": "OK", "flags": ["MULTI_ADDRESS"],
             "frontage_length_m": 8.5, "parcel_id": "P2"}
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "agent2_site.json"
            write_site_json(results, out)
            self.assertTrue(out.exists())
            with open(out) as fh:
                data = json.load(fh)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["site_id"], "SITE_000001")

    def test_write_result_gpkg(self):
        sites = _make_sites()
        results = [
            {"site_id": "SITE_000001", "status": "OK", "flags": [],
             "frontage_length_m": 8.0, "parcel_id": "P2",
             "member_count": 1, "neighbour_count": 2,
             "frontage_point_a": (150008, 170000),
             "frontage_point_b": (150016, 170000),
             "frontage_method": "closest_to_road",
             "parcel_geom_wkt": None,
             "building_geom_wkt": None}
        ]
        from shapely.geometry import Point as Pt
        parcels = gpd.GeoDataFrame(geometry=gpd.GeoSeries([], dtype="geometry"), crs="EPSG:31370")
        fe = [{
            "site_id": "SITE_000001",
            "length_m": 8.0,
            "method": "closest_to_road",
            "geometry": LineString([(150008, 170000), (150016, 170000)]),
        }]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "result.gpkg"
            write_result_gpkg(
                sites_gdf=sites,
                results=results,
                parcels_collected=parcels,
                frontage_edges=fe,
                buildings_collected=None,
                roads_collected=None,
                out_path=out,
            )
            self.assertTrue(out.exists())
            import pyogrio
            layers = [l[0] for l in pyogrio.list_layers(out)]
            self.assertIn("sites", layers)
            self.assertIn("frontage_edges", layers)


if __name__ == "__main__":
    unittest.main()
