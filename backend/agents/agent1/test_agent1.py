"""
Unit tests for Agent 1 — Input + Deduplication.

Run with:
    cd agent1
    python -m pytest test_agent1.py -v
or:
    python test_agent1.py
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point

from agent1_input_dedup import (
    normalise_street,
    parse_house_number,
    detect_columns,
    deduplicate,
    load_input,
    write_sites_gpkg,
    write_mapping_json,
)

EPSG_LB72 = 31370


def _make_gdf(rows):
    """Helper: build a minimal GeoDataFrame in EPSG:31370."""
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=f"EPSG:{EPSG_LB72}")


class TestNormalisation(unittest.TestCase):
    def test_strip_accents(self):
        self.assertEqual(normalise_street("Aarschotsesteenweg"), "aarschotsesteenweg")
        self.assertEqual(normalise_street("Rue de l'Église"), "rue de l'eglise")

    def test_collapse_whitespace(self):
        self.assertEqual(normalise_street("  Kerk  straat  "), "kerk straat")


class TestParseHouseNumber(unittest.TestCase):
    def test_plain(self):
        r = parse_house_number("42")
        self.assertEqual(r, {"base": "42", "letter": "", "bus": ""})

    def test_with_letter(self):
        r = parse_house_number("12A")
        self.assertEqual(r, {"base": "12", "letter": "A", "bus": ""})

    def test_with_bus(self):
        r = parse_house_number("12 bus 3")
        self.assertEqual(r, {"base": "12", "letter": "", "bus": "3"})

    def test_with_slash_bus(self):
        r = parse_house_number("12/3")
        self.assertEqual(r, {"base": "12", "letter": "", "bus": "3"})

    def test_none_input(self):
        r = parse_house_number(None)
        self.assertEqual(r["base"], "")


class TestDeduplication(unittest.TestCase):
    def _base_gdf(self):
        """Three row-house addresses on the same street; 12A and 12B share base 12."""
        return _make_gdf([
            {
                "street": "Kerkstraat", "housenr": "10", "postcode": "1700",
                "geometry": Point(150004, 170003),
            },
            {
                "street": "Kerkstraat", "housenr": "12A", "postcode": "1700",
                "geometry": Point(150012, 170003),
            },
            {
                "street": "Kerkstraat", "housenr": "12B", "postcode": "1700",
                "geometry": Point(150013, 170003),
            },
        ])

    def test_groups_correctly(self):
        gdf = self._base_gdf()
        col_map = detect_columns(gdf)
        sites_gdf, mapping = deduplicate(gdf, col_map, max_radius=30.0)
        # 10 = one site, 12A+12B = one site -> 2 sites total
        self.assertEqual(len(sites_gdf), 2)

    def test_multi_address_site(self):
        gdf = self._base_gdf()
        col_map = detect_columns(gdf)
        sites_gdf, mapping = deduplicate(gdf, col_map, max_radius=30.0)
        multi = sites_gdf[sites_gdf["member_count"] > 1]
        self.assertEqual(len(multi), 1)
        self.assertEqual(multi.iloc[0]["member_count"], 2)

    def test_spatial_split(self):
        """Two addresses share base '12' but are 200 m apart -> should be split."""
        gdf = _make_gdf([
            {
                "street": "Dorpstraat", "housenr": "12A", "postcode": "9000",
                "geometry": Point(200000, 200000),
            },
            {
                "street": "Dorpstraat", "housenr": "12B", "postcode": "9000",
                "geometry": Point(200200, 200000),  # 200 m away
            },
        ])
        col_map = detect_columns(gdf)
        sites_gdf, mapping = deduplicate(gdf, col_map, max_radius=30.0)
        # Should be split into 2 sites
        self.assertEqual(len(sites_gdf), 2)

    def test_representative_point_is_centroid(self):
        """Rep point should be at the centroid of the group."""
        gdf = _make_gdf([
            {
                "street": "Marktplein", "housenr": "5A", "postcode": "2000",
                "geometry": Point(100000, 100000),
            },
            {
                "street": "Marktplein", "housenr": "5B", "postcode": "2000",
                "geometry": Point(100004, 100000),
            },
        ])
        col_map = detect_columns(gdf)
        sites_gdf, _ = deduplicate(gdf, col_map, max_radius=30.0)
        self.assertEqual(len(sites_gdf), 1)
        pt = sites_gdf.iloc[0].geometry
        self.assertAlmostEqual(pt.x, 100002.0, places=1)
        self.assertAlmostEqual(pt.y, 100000.0, places=1)


class TestOutputFiles(unittest.TestCase):
    def test_write_sites_gpkg(self):
        gdf = _make_gdf([
            {
                "site_id": "SITE_000001", "postcode": "1000",
                "street_norm": "kerkstraat", "hnr_base": "10",
                "member_count": 1, "geometry": Point(150000, 170000),
            }
        ])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "sites.gpkg"
            write_sites_gpkg(gdf, out)
            self.assertTrue(out.exists())
            loaded = gpd.read_file(str(out), layer="sites")
            self.assertEqual(len(loaded), 1)

    def test_write_mapping_json(self):
        mapping = {
            "SITE_000001": [
                {"street": "Kerkstraat", "housenr": "10", "_orig_idx": 0,
                 "hnr_base": "10", "hnr_letter": "", "hnr_bus": ""},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "mapping.json"
            write_mapping_json(mapping, out)
            self.assertTrue(out.exists())
            with open(out, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertIn("SITE_000001", data)
            self.assertEqual(len(data["SITE_000001"]), 1)


class TestLoadInput(unittest.TestCase):
    def test_load_gpkg(self):
        """Write a minimal GPKG, load it, verify reproject."""
        gdf = _make_gdf([
            {
                "street": "Testlaan", "housenr": "1",
                "postcode": "3000",
                "geometry": Point(160000, 190000),
            }
        ])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "test.gpkg"
            gdf.to_file(str(p), layer="addresses", driver="GPKG")
            loaded = load_input(p)
            self.assertEqual(loaded.crs.to_epsg(), EPSG_LB72)
            self.assertEqual(len(loaded), 1)


if __name__ == "__main__":
    unittest.main()
