from types import SimpleNamespace

from PIL import Image

from . import agent5b_florence as florence


def test_verifier_uses_full_image_and_only_matching_rf_grounding(tmp_path, monkeypatch):
    image_path = tmp_path / "source.jpg"
    Image.new("RGB", (100, 100), "white").save(image_path)

    verifier = object.__new__(florence.FlorenceMailboxVerifier)
    verifier.backend = SimpleNamespace(model_name="fake", revision="test")
    candidate = {
        "bbox": [40, 40, 60, 60],
        "support": 2,
        "sources": [
            {"phrase": "mailbox"},
            {"phrase": "letter slot"},
        ],
    }
    unrelated = {
        "bbox": [0, 0, 10, 10],
        "support": 2,
        "sources": [{"phrase": "mailbox"}, {"phrase": "letter slot"}],
    }
    seen_sizes = []

    def fake_detect(image, *args, **kwargs):
        seen_sizes.append(image.size)
        return [candidate, unrelated], []

    monkeypatch.setattr(florence, "detect", fake_detect)
    result = verifier.verify(
        image_path,
        {
            "det_id": "fov90_test",
            "score": 0.75,
            "box": {"x1": 40, "y1": 40, "x2": 60, "y2": 60},
        },
        tmp_path / "debug",
    )

    assert seen_sizes == [(100, 100)]
    assert result["accepted"] is True
    assert "additional_mailboxes" not in result
    assert "review_crop_box" not in result
    assert result["florence_verification_score"] == 1.0
    assert len(result["scored_mailboxes"]) == 1
    assert result["verification"][1]["considered"] is False
    assert (tmp_path / "debug" / "fov90_test_accepted.jpg").is_file()
    assert (tmp_path / "debug" / "fov90_test_accepted.json").is_file()
