"""Unit tests for Agent 5 — Cross-Verify."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .agent5_cross_verify import (
    BestLookupResult,
    DwellingResult,
    RegisterResult,
    apply_register,
    cross_verify_vision,
    run,
)


class TestRegisterRules(unittest.TestCase):
    def test_best_primary_sfu(self):
        best = BestLookupResult(1, "ok", "ok")
        fps = DwellingResult(5, "ok", "should be skipped")
        reg = apply_register(best, fps)
        self.assertEqual(reg.register_count, 1)
        self.assertEqual(reg.check_source, "BeSt API")

    def test_cadgis_fallback(self):
        best = BestLookupResult(0, "zero", "none")
        fps = DwellingResult(3, "ok", "ok", source="CadGIS")
        reg = apply_register(best, fps)
        self.assertEqual(reg.register_count, 3)
        self.assertEqual(reg.check_source, "CadGIS/FPS")

    def test_unknown(self):
        best = BestLookupResult(None, "error", "fail")
        fps = DwellingResult(None, "unknown", "none")
        reg = apply_register(best, fps)
        self.assertEqual(reg.predicted_type, "UNKNOWN")


class TestCrossVerifyMatrix(unittest.TestCase):
    def _reg(self, n):
        return RegisterResult(
            n, "SFU" if n == 1 else "MDU", "BeSt API", n, None, "ok", "ok", "skipped"
        )

    def test_match(self):
        d = cross_verify_vision(1, "OK", self._reg(1))
        self.assertEqual(d["action"], "accept_match")
        self.assertEqual(d["confidence"], "High")

    def test_from_register(self):
        d = cross_verify_vision(0, "OK", self._reg(2))
        self.assertIn("FROM_REGISTER", d["flags"])
        self.assertEqual(d["final_count"], 2)

    def test_mismatch(self):
        d = cross_verify_vision(3, "OK", self._reg(1))
        self.assertIn("MISMATCH", d["flags"])

    def test_no_streetview(self):
        d = cross_verify_vision(None, "NO_STREETVIEW", self._reg(1))
        self.assertIn("NO_IMAGERY", d["flags"])
        self.assertEqual(d["final_count"], 1)

    def test_register_unknown(self):
        reg = RegisterResult(None, "UNKNOWN", "none", None, None, "x", "zero", "unknown")
        d = cross_verify_vision(2, "OK", reg)
        self.assertEqual(d["final_count"], 2)
        self.assertEqual(d["count_source"], "vision")


class TestOfflineRun(unittest.TestCase):
    def test_offline_with_seeded_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            detect = [
                {
                    "site_id": "SITE_1",
                    "postcode": "1700",
                    "street_norm": "kerkstraat",
                    "hnr_base": "12",
                    "vision_count": 1,
                    "confidence": "Medium",
                    "status": "OK",
                    "flags": [],
                    "mailboxes": [],
                }
            ]
            detect_path = td / "agent4_detect.json"
            detect_path.write_text(json.dumps(detect), encoding="utf-8")

            best_cache = td / "best.json"
            best_cache.write_text(
                json.dumps(
                    {
                        "1700|kerkstraat|12": {
                            "record_count": 1,
                            "status": "ok",
                            "message": "seeded",
                        }
                    }
                ),
                encoding="utf-8",
            )
            cadgis_cache = td / "cadgis.json"
            cadgis_cache.write_text("{}", encoding="utf-8")

            results = run(
                detect_path,
                td,
                best_cache=best_cache,
                cadgis_cache=cadgis_cache,
                offline=True,
            )
            self.assertEqual(results[0]["final_count"], 1)
            self.assertEqual(results[0]["action"], "accept_match")
            self.assertTrue((td / "agent5_cross_verify.json").exists())


if __name__ == "__main__":
    unittest.main()
