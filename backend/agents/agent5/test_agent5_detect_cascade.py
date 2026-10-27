"""Tests for the Agent 5 FOV-90/FOV-110 detection cascade."""

from pathlib import Path

from PIL import Image

from .agent5_detect import Box, process_site


def _site(tmp_path: Path):
    images = []
    for fov in (90.0, 110.0):
        path = tmp_path / f"enhanced_fov{fov:g}.jpg"
        Image.new("RGB", (100, 100), "white").save(path)
        images.append(
            {
                "pano_id": "P1",
                "fov": fov,
                "enhanced_file_path": str(path),
                "image_width": 100,
                "image_height": 100,
            }
        )
    return {"site_id": "SITE_1", "status": "OK", "flags": [], "images": images}


class FakeDetector:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def detect(self, path, threshold):
        self.calls.append(Path(path).name)
        return self.results.get(Path(path).name, [])


class FakeVerifier:
    def __init__(self, score):
        self.score = score
        self.calls = []

    def verify(self, path, detection):
        self.calls.append((Path(path).name, detection["score"]))
        return {
            "accepted": self.score > 0.80,
            "florence_verification_score": self.score,
            "model": "fake-florence",
            "revision": "test",
            "evidence": {},
        }


def test_fov90_detection_stops_before_fov110(tmp_path):
    detector = FakeDetector({"enhanced_fov90.jpg": [Box(1, 2, 10, 20, 0.9)]})
    result = process_site(
        _site(tmp_path),
        detections_map=None,
        demo_boxes=False,
        score_threshold=0.4,
        detector=detector,
        detected_root=tmp_path / "detected",
    )
    assert detector.calls == ["enhanced_fov90.jpg"]
    assert result["vision_count"] == 1
    assert result["detection_fov"] == 90.0
    assert result["manual_review"] is False


def test_fov110_is_used_only_after_empty_fov90_and_marks_review(tmp_path):
    detector = FakeDetector({"enhanced_fov110.jpg": [Box(1, 2, 10, 20, 0.9), Box(20, 2, 30, 20, 0.74)]})
    verifier = FakeVerifier(0.81)
    result = process_site(
        _site(tmp_path),
        detections_map=None,
        demo_boxes=False,
        score_threshold=0.4,
        detector=detector,
        detected_root=tmp_path / "detected",
        verifier=verifier,
    )
    assert detector.calls == ["enhanced_fov90.jpg", "enhanced_fov110.jpg"]
    assert result["vision_count"] == 2
    assert result["detection_fov"] == 110.0
    assert result["manual_review"] is True
    assert result["status"] == "MANUAL_REVIEW"
    assert "FOV110_FALLBACK" in result["flags"]
    assert "2 verified mailbox(es)" in result["remarks"]
    assert verifier.calls == [("enhanced_fov110.jpg", 0.74)]


def test_accepts_only_mailbox_class_strictly_above_seventy_percent(tmp_path):
    detector = FakeDetector(
        {
            "enhanced_fov90.jpg": [
                Box(1, 2, 10, 20, 0.99, "street_sign"),
                Box(12, 2, 20, 20, 0.95, "utility_box"),
                Box(22, 2, 30, 20, 0.70, "mailbox"),
                Box(32, 2, 40, 20, 0.69, "mailbox"),
                Box(42, 2, 50, 20, 0.75, "mailbox"),
                Box(52, 2, 60, 20, 0.7499, "mailbox"),
            ]
        }
    )
    result = process_site(
        _site(tmp_path),
        detections_map=None,
        demo_boxes=False,
        # A stale lower setting must not weaken the production minimum.
        score_threshold=0.4,
        detector=detector,
        detected_root=tmp_path / "detected",
        verifier=FakeVerifier(0.80),
    )
    assert detector.calls == ["enhanced_fov90.jpg"]
    assert result["vision_count"] == 1
    assert result["mailboxes"][0]["score"] == 0.75
    assert result["mailboxes"][0]["decision_source"] == "rfdetr_direct"
    assert result["agent5b_reviewed"] == 1
    assert result["agent5b_accepted"] == 0


def test_florence_above_eighty_accepts_mid_confidence_fov90(tmp_path):
    detector = FakeDetector({"enhanced_fov90.jpg": [Box(1, 2, 10, 20, 0.7499)]})
    verifier = FakeVerifier(0.8001)
    result = process_site(
        _site(tmp_path), detections_map=None, demo_boxes=False,
        score_threshold=0.70, detector=detector,
        detected_root=tmp_path / "detected", verifier=verifier,
    )
    assert detector.calls == ["enhanced_fov90.jpg"]
    assert result["vision_count"] == 1
    assert result["mailboxes"][0]["decision_source"] == "florence_verified"
    assert result["mailboxes"][0]["florence_verification_score"] == 0.8001


