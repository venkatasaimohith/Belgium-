"""Unit tests for Agent 4 — Assign then Detect."""

from __future__ import annotations

import json
import hashlib
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from image_enhancement import ImageEnhancer, RealESRGAN

from agent4_assign import (
    build_visual_target_geometry,
    classify_s,
    col_to_bearing,
    project_frontage_columns,
    ray_hit_frontage,
    run as run_assign,
    select_target_building_geometry,
    transform_bbox_columns,
)
from agent4_detect import (
    Box,
    DEFAULT_RFDETR_LARGE_CHECKPOINT,
    DEFAULT_SCORE_THRESHOLD,
    MAILBOX_BUNDLE_MANIFEST,
    build_banks,
    detect_boxes,
    fuse_site_detections,
    process_site,
    run as run_detect,
)


class TestGeometry(unittest.TestCase):
    def test_preferred_mailbox_bundle_checkpoint_is_configured(self):
        manifest = json.loads(MAILBOX_BUNDLE_MANIFEST.read_text(encoding="utf-8"))
        expected = manifest["weights"]["last_ema.pth"]
        digest = hashlib.sha256(DEFAULT_RFDETR_LARGE_CHECKPOINT.read_bytes()).hexdigest()
        self.assertEqual(DEFAULT_RFDETR_LARGE_CHECKPOINT.name, "last_ema.pth")
        self.assertEqual(DEFAULT_RFDETR_LARGE_CHECKPOINT.stat().st_size, expected["bytes"])
        self.assertEqual(digest, expected["sha256"])
        self.assertEqual(DEFAULT_SCORE_THRESHOLD, manifest["default_confidence"])

    def test_requested_cuda_fails_explicitly_when_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            weights = Path(td) / "weights.pth"
            weights.touch()
            with patch("torch.cuda.is_available", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "GPU enhancement was requested"):
                    RealESRGAN(weights, "cuda")

    def test_auto_selects_cuda_when_available(self):
        from spandrel import ImageModelDescriptor
        from unittest.mock import MagicMock

        descriptor = MagicMock(spec=ImageModelDescriptor)
        with tempfile.TemporaryDirectory() as td:
            weights = Path(td) / "weights.pth"
            weights.touch()
            with patch("torch.cuda.is_available", return_value=True), patch(
                "torch.cuda.get_device_name", return_value="Test GPU"
            ), patch("spandrel.ModelLoader") as loader:
                loader.return_value.load_from_file.return_value = descriptor
                model = RealESRGAN(weights, "auto")
            self.assertEqual(model.device.type, "cuda")
            descriptor.to.assert_called_once_with(model.device)

    def test_basic_enhancement_reports_cpu_without_loading_ai_model(self):
        with patch("image_enhancement.RealESRGAN") as model:
            enhancer = ImageEnhancer(mode="enhance", device="cuda")
        self.assertEqual(enhancer.device, "cpu")
        model.assert_not_called()

    def test_classify_s(self):
        self.assertEqual(classify_s(2.0, 8.0, 1.0), "TARGET")
        self.assertEqual(classify_s(-0.5, 8.0, 1.0), "AMBIGUOUS")
        self.assertEqual(classify_s(-2.0, 8.0, 1.0), "NEIGHBOUR")
        self.assertEqual(classify_s(9.5, 8.0, 1.0), "NEIGHBOUR")

    def test_ray_hit(self):
        cam = (0.0, 0.0)
        A, B = (-6.0, 12.0), (6.0, 12.0)
        hit = ray_hit_frontage(cam, 0.0, A, B)
        self.assertIsNotNone(hit)
        t, s = hit
        self.assertAlmostEqual(t, 12.0, places=3)
        self.assertAlmostEqual(s, 6.0, places=3)

    def test_col_bearing_roundtrip(self):
        W, heading, fov = 640, 0.0, 90.0
        for x in (160, 320, 480):
            th = col_to_bearing(x, heading, fov, W)
            # centre column → heading
            if x == 320:
                self.assertAlmostEqual(th % 360, heading % 360, places=3)

    def test_banks(self):
        pts = [(0, 0), (0.5, 0), (10, 0)]
        banks = build_banks(pts, max_gap=1.5)
        self.assertEqual(len(banks), 2)


class TestAssignDetectPipeline(unittest.TestCase):
    def _fixture(self, td: Path):
        # tiny JPEG
        img_dir = td / "images" / "SITE_X"
        img_dir.mkdir(parents=True)
        img_path = img_dir / "pano.jpg"
        Image.new("RGB", (640, 640), (80, 80, 80)).save(img_path, "JPEG")

        site = {
            "site_id": "SITE_X",
            "x": 0.0,
            "y": 5.0,
            "postcode": "1700",
            "street_norm": "kerkstraat",
            "hnr_base": "12",
            "status": "OK",
            "flags": [],
            "frontage_point_a": [-6.0, 12.0],
            "frontage_point_b": [6.0, 12.0],
            "frontage_length_m": 12.0,
            "parcel_id": "P1",
        }
        images = [
            {
                "site_id": "SITE_X",
                "postcode": "1700",
                "street_norm": "kerkstraat",
                "hnr_base": "12",
                "x": 0.0,
                "y": 5.0,
                "status": "OK",
                "flags": [],
                "images": [
                    {
                        "pano_id": "PANO1",
                        "lat": 50.8,
                        "lon": 4.3,
                        "cam_x": 0.0,
                        "cam_y": 0.0,
                        "heading": 0.0,
                        "fov": 90.0,
                        "pitch": 0.0,
                        "image_width": 640,
                        "image_height": 640,
                        "file_path": str(img_path),
                        "left_corner": [-6.0, 12.0],
                        "right_corner": [6.0, 12.0],
                    }
                ],
            }
        ]
        site_json = td / "agent2_site.json"
        images_json = td / "agent3_images.json"
        site_json.write_text(json.dumps([site]), encoding="utf-8")
        images_json.write_text(json.dumps(images), encoding="utf-8")
        return site_json, images_json

    def test_assign_then_detect_demo(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            site_json, images_json = self._fixture(td)
            assign_results = run_assign(
                images_json, site_json, td, buffer_m=1.0,
                enhancement_mode="enhance", enhancement_scale=1.0,
            )
            self.assertEqual(assign_results[0]["status"], "OK")
            enhanced = assign_results[0]["images"][0]["enhanced_file_path"]
            self.assertTrue(Path(enhanced).is_file())
            self.assertEqual(assign_results[0]["images"][0]["enhancement_device"], "cpu")

            detect_results = run_detect(
                td / "agent4_assign.json",
                td,
                demo_boxes=True,
                skip_plausibility=True,
            )
            self.assertEqual(detect_results[0]["status"], "OK")
            self.assertEqual(detect_results[0]["vision_count"], 2)
            self.assertEqual(detect_results[0]["neighbours_rejected"], [])
            self.assertEqual(detect_results[0]["ambiguous"], [])
            self.assertTrue(all(
                box["ownership"] == "TARGET"
                for box in detect_results[0]["all_detections"]
            ))
            self.assertEqual(len(detect_results[0]["detected_images"]), 1)
            detected_image = Path(detect_results[0]["detected_images"][0])
            self.assertTrue(detected_image.is_file())
            self.assertEqual(detected_image.parent, td / "detected" / "SITE_X")

    def test_camera_sightline_frontage_overrides_agent2_and_is_not_expanded(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            site_json, images_json = self._fixture(td)
            sites = json.loads(site_json.read_text(encoding="utf-8"))
            sites[0]["parcel_geom_wkt"] = (
                "POLYGON ((-20 10, 20 10, 20 30, -20 30, -20 10))"
            )
            site_json.write_text(json.dumps(sites), encoding="utf-8")
            payload = json.loads(images_json.read_text(encoding="utf-8"))
            image = payload[0]["images"][0]
            image["left_corner"] = [-4.0, 10.0]
            image["right_corner"] = [4.0, 10.0]
            image["frontage_source"] = "agent3_camera_sightline"
            images_json.write_text(json.dumps(payload), encoding="utf-8")

            assigned = run_assign(
                images_json,
                site_json,
                td,
                buffer_m=1.0,
                enhancement_mode="enhance",
                enhancement_scale=1.0,
            )[0]["images"][0]

            self.assertEqual(assigned["frontage_source"], "agent3_camera_sightline")
            self.assertFalse(assigned["frontage_refined_from_parcel"])
            self.assertAlmostEqual(assigned["frontage_length_m"], 8.0, places=3)
            self.assertEqual(
                {tuple(assigned["left_corner"]), tuple(assigned["right_corner"])},
                {(-4.0, 10.0), (4.0, 10.0)},
            )

    def test_detector_reads_enhanced_image(self):
        seen = []

        class Detector:
            def detect(self, path, threshold):
                seen.append((Path(path), threshold))
                return []

        record = {
            "pano_id": "PANO1",
            "file_path": "original.jpg",
            "enhanced_file_path": "enhanced.jpg",
        }
        detect_boxes(
            record,
            "SITE_X",
            detections_map=None,
            demo_boxes=False,
            score_threshold=0.4,
            detector=Detector(),
        )
        self.assertEqual(seen, [(Path("enhanced.jpg"), 0.7)])

    def test_enhancement_writes_every_view_from_same_panorama(self):
        """Views sharing a pano_id must receive distinct enhanced files."""
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            site_json, images_json = self._fixture(td)
            payload = json.loads(images_json.read_text(encoding="utf-8"))

            first = payload[0]["images"][0]
            first["image_type"] = "discovery_front"
            first["rotation_label"] = "front"
            first["rotation_offset_deg"] = 0.0

            right_path = Path(first["file_path"]).with_name("pano_right_45.jpg")
            Image.new("RGB", (640, 640), (100, 100, 100)).save(right_path, "JPEG")
            payload[0]["images"].append(
                {
                    **first,
                    "file_path": str(right_path),
                    "heading": 45.0,
                    "image_type": "discovery_right_45",
                    "rotation_label": "right_45",
                    "rotation_offset_deg": 45.0,
                }
            )
            images_json.write_text(json.dumps(payload), encoding="utf-8")

            assigned = run_assign(
                images_json, site_json, td, buffer_m=1.0,
                enhancement_mode="enhance", enhancement_scale=1.0,
            )
            output_images = assigned[0]["images"]
            enhanced_paths = [Path(item["enhanced_file_path"]) for item in output_images]

            self.assertEqual(len(enhanced_paths), 2)
            self.assertEqual(len(set(enhanced_paths)), 2)
            self.assertTrue(all(path.is_file() for path in enhanced_paths))
            self.assertEqual(output_images[1]["rotation_label"], "right_45")
            self.assertEqual(output_images[1]["image_type"], "discovery_right_45")

    def test_project_columns(self):
        proj = project_frontage_columns(
            cam=(0.0, 0.0),
            left=(-6.0, 12.0),
            right=(6.0, 12.0),
            heading=0.0,
            fov=90.0,
            W=640,
            buffer_m=1.0,
        )
        self.assertLess(proj["col_target_lo"], proj["col_target_hi"])
        self.assertLessEqual(proj["col_buffer_lo"], proj["col_target_lo"])

    def test_visual_mask_uses_building_not_whole_parcel(self):
        site = {
            "site_id": "SITE_BUILDING",
            "x": 0.0,
            "y": 12.0,
            "building_geom_wkt": "POLYGON ((-2 10, 2 10, 2 15, -2 15, -2 10))",
            "parcel_geom_wkt": "POLYGON ((-10 8, 10 8, 10 25, -10 25, -10 8))",
        }
        geometry, source = build_visual_target_geometry(
            site, (-3.0, 8.0), (3.0, 8.0), padding_m=0.5
        )
        self.assertIsNotNone(geometry)
        self.assertIn("cadgis_building", source)
        self.assertNotIn("parcel_fallback", source)
        # Gate context remains, but the full 20 metre parcel is excluded.
        self.assertGreaterEqual(geometry.bounds[0], -3.6)
        self.assertLessEqual(geometry.bounds[2], 3.6)

    def test_house_bbox_columns_expand_for_narrow_view(self):
        house_box = {
            "bbox": {"x1": 240.0, "x2": 400.0, "y1": 100.0, "y2": 500.0},
            "width": 640,
            "source_fov": 100.0,
            "source_heading": 180.0,
        }
        columns = transform_bbox_columns(
            house_box,
            {"image_width": 640, "fov": 45.0, "heading": 180.0},
        )
        self.assertIsNotNone(columns)
        self.assertLess(columns[0], 240.0)
        self.assertGreater(columns[1], 400.0)

    def test_shared_building_is_selected_by_target_parcel(self):
        site = {
            "site_id": "SITE_ROW_HOUSE",
            "x": 0.0,
            "y": 12.0,
            "building_geom_wkt": "POLYGON ((-10 10, 10 10, 10 15, -10 15, -10 10))",
            "parcel_geom_wkt": "POLYGON ((-3 8, 3 8, 3 20, -3 20, -3 8))",
        }
        geometry, source = select_target_building_geometry(site)
        self.assertIsNotNone(geometry)
        self.assertIn("parcel_selected", source)
        self.assertGreaterEqual(geometry.bounds[0], -3.11)
        self.assertLessEqual(geometry.bounds[2], 3.11)


class TestFuse(unittest.TestCase):
    def test_high_confidence_two_panos(self):
        dets = [
            {
                "ownership": "TARGET",
                "pano_id": "A",
                "ground_xy": [1.0, 2.0],
                "score": 0.9,
            },
            {
                "ownership": "TARGET",
                "pano_id": "B",
                "ground_xy": [1.2, 2.1],
                "score": 0.9,
            },
        ]
        fused = fuse_site_detections(dets)
        self.assertEqual(fused["confidence"], "High")
        self.assertEqual(fused["vision_count"], 2)


if __name__ == "__main__":
    unittest.main()
