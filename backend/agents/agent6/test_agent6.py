"""Tests for Agent 6 geometry-first ownership rules."""

import math

import geopandas as gpd
from PIL import Image, ImageDraw
from shapely.geometry import Polygon

from . import agent6_reject
from .agent6_reject import (
    MANUAL_REVIEW,
    NEIGHBOUR,
    TARGET,
    classify_image_detections,
    build_frontages,
    detect_parcel_guided_boundaries,
    pinhole_bearing,
    ray_frontage_evidence,
)


def _image():
    return {
        "image_width": 100,
        "image_height": 100,
        "cam_x": 0.0,
        "cam_y": 0.0,
        "heading": 0.0,
        "fov": 90.0,
        "left_corner": [-4.0, 10.0],
        "right_corner": [4.0, 10.0],
        "buffer_m": 1.0,
        "col_target_lo": 30.0,
        "col_target_hi": 70.0,
        "col_buffer_lo": 25.0,
        "col_buffer_hi": 75.0,
    }


def _frontages(include_neighbours=True):
    result = [
        {
            "owner": TARGET,
            "parcel_id": "TARGET_PARCEL",
            "start": [-4.0, 10.0],
            "end": [4.0, 10.0],
            "source": "test",
        }
    ]
    if include_neighbours:
        result.extend(
            [
                {
                    "owner": NEIGHBOUR,
                    "parcel_id": "RIGHT_PARCEL",
                    "start": [4.5, 10.0],
                    "end": [12.0, 10.0],
                    "source": "test",
                },
                {
                    "owner": NEIGHBOUR,
                    "parcel_id": "LEFT_PARCEL",
                    "start": [-12.0, 10.0],
                    "end": [-4.5, 10.0],
                    "source": "test",
                },
            ]
        )
    return result


def _boundaries():
    return {"status": "ok", "left": 30.0, "right": 70.0}


def _det(x1, y1, x2, y2):
    return {
        "det_id": f"box-{x1}",
        "pano_id": "P1",
        "box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        "score": 0.9,
    }


def test_pinhole_bearing_uses_camera_projection():
    assert pinhole_bearing(50, 100, 0, 90) == 0.0
    assert math.isclose(pinhole_bearing(100, 100, 0, 90), 45.0, abs_tol=1e-6)


def test_horizontal_calibration_finds_one_global_facade_shift():
    picture = Image.new("RGB", (200, 100), "gray")
    draw = ImageDraw.Draw(picture)
    draw.rectangle((70, 10, 72, 90), fill="white")
    draw.rectangle((130, 10, 132, 90), fill="white")
    image = {**_image(), "image_width": 200, "image_height": 100}
    result = agent6_reject._estimate_horizontal_calibration(
        image, 60.0, 120.0, picture
    )
    assert result["reliable"] is True
    assert 9.0 <= result["pixel_shift"] <= 12.0
    assert result["heading_correction_deg"] < 0.0


def test_center_ray_hits_target_frontage():
    evidence = ray_frontage_evidence(_image(), 50.0, _frontages())
    assert evidence["owner"] == TARGET
    assert evidence["hit"]["parcel_id"] == "TARGET_PARCEL"


def test_parcel_guided_projection_marks_camera_facing_target_house():
    site = {
        "site_id": "S",
        "x": 0.0,
        "y": 15.0,
        "parcel_geom_wkt": "POLYGON ((-5 10, 5 10, 5 25, -5 25, -5 10))",
        "building_geom_wkt": "POLYGON ((-2 12, 2 12, 2 20, -2 20, -2 12))",
    }
    result = detect_parcel_guided_boundaries(site, _image())
    assert result["status"] == "ok"
    assert result["source"] == "parcel_guided_projection"
    assert result["left"] < result["facade_left"] < result["facade_right"] < result["right"]
    assert {tuple(point) for point in result["visible_facade"]} == {
        (-2.0, 12.0),
        (2.0, 12.0),
    }
    assert result["house_bbox"]["source"] == "calibrated_projected_facade_with_default_vertical_extent"
    assert result["projection_reliable"] is False
    assert result["uncertainty_m"] == 2.5


def test_three_target_rays_keep_mailbox():
    result = classify_image_detections(
        [_det(47, 45, 53, 65)], _image(), _frontages(), _boundaries()
    )
    assert result[0]["ownership"] == TARGET
    assert result[0]["rejection_reason"] == "three_rays_intersect_target_frontage"
    assert result[0]["mailbox_base_px"] == [50.0, 65.0]


def test_target_frontage_overrides_incorrect_facade_and_column_slice():
    image = _image()
    image.update(
        {
            # Deliberately stale/narrow image columns: GIS still proves that
            # the mailbox ray intersects the wider target frontage.
            "col_target_lo": 45.0,
            "col_target_hi": 55.0,
            "col_buffer_lo": 43.0,
            "col_buffer_hi": 57.0,
        }
    )
    frontages = _frontages(include_neighbours=False)
    frontages[0]["start"] = [-20.0, 10.0]
    frontages[0]["end"] = [20.0, 10.0]
    result = classify_image_detections(
        [_det(27, 45, 33, 65)],
        image,
        frontages,
        {"status": "ok", "left": 45.0, "right": 55.0},
    )
    assert result[0]["agent6_evidence"]["column_state"] == NEIGHBOUR
    assert result[0]["agent6_evidence"]["inside_architectural_slice"] is False
    assert result[0]["ownership"] == TARGET
    assert result[0]["rejection_reason"] == "three_rays_intersect_target_frontage"


def test_single_unresolved_mailbox_near_driveway_is_kept():
    image = _image()
    # No ray hits are available, but this lone mailbox is just beside the
    # parcel frontage/buffer where detached-house mailboxes normally sit.
    result = classify_image_detections(
        [_det(19, 45, 23, 65)],
        image,
        [],
        {"status": "ok", "left": 30.0, "right": 70.0},
    )
    assert result[0]["ownership"] == TARGET
    assert result[0]["rejection_reason"] == "single_mailbox_within_target_access_envelope"


def test_mailbox_near_target_boundary_goes_to_manual_review():
    result = classify_image_detections(
        [_det(76, 45, 80, 65)], _image(), _frontages(), _boundaries()
    )
    assert result[0]["ownership"] == MANUAL_REVIEW
    assert result[0]["agent6_evidence"]["target_boundary_uncertain"] is True


def test_three_neighbour_rays_reject_mailbox():
    result = classify_image_detections(
        [_det(87, 45, 93, 65)], _image(), _frontages(), _boundaries()
    )
    assert result[0]["ownership"] == NEIGHBOUR
    assert result[0]["rejection_reason"] == "three_rays_intersect_adjacent_frontage"


def test_unverified_projection_cannot_reject_mailbox():
    boundaries = {
        **_boundaries(),
        "projection_reliable": False,
        "uncertainty_m": 2.5,
    }
    result = classify_image_detections(
        [_det(87, 45, 93, 65)], _image(), _frontages(), boundaries
    )
    assert result[0]["ownership"] == MANUAL_REVIEW
    assert result[0]["agent6_evidence"]["projection_reliable"] is False


def test_neighbour_rays_must_hit_same_adjacent_parcel(monkeypatch):
    evidence = []
    for parcel_id in ("LEFT", "RIGHT", "RIGHT"):
        evidence.append(
            {
                "owner": NEIGHBOUR,
                "bearing": 30.0,
                "hit": {
                    "owner": NEIGHBOUR,
                    "parcel_id": parcel_id,
                    "start": [4.5, 10.0],
                    "end": [12.0, 10.0],
                    "along_m": 3.0,
                    "x": 7.5,
                    "y": 10.0,
                },
            }
        )
    monkeypatch.setattr(
        agent6_reject,
        "_three_ray_owners",
        lambda *args, **kwargs: ([NEIGHBOUR, NEIGHBOUR, NEIGHBOUR], evidence),
    )
    result = classify_image_detections(
        [_det(87, 45, 93, 65)], _image(), _frontages(), _boundaries()
    )
    assert result[0]["ownership"] == MANUAL_REVIEW
    assert result[0]["agent6_evidence"]["same_safe_neighbour_parcel"] is False


def test_boundary_disagreement_goes_to_manual_review():
    boundaries = {"status": "ok", "left": 20.0, "right": 95.0}
    result = classify_image_detections(
        [_det(87, 45, 93, 65)], _image(), _frontages(), boundaries
    )
    assert result[0]["ownership"] == MANUAL_REVIEW
    assert result[0]["rejection_reason"] == "gis_conflicts_with_architectural_slice"


def test_missing_neighbour_frontages_cannot_reject():
    result = classify_image_detections(
        [_det(87, 45, 93, 65)],
        _image(),
        _frontages(include_neighbours=False),
        _boundaries(),
    )
    assert result[0]["ownership"] == MANUAL_REVIEW


def test_door_fallback_can_promote_but_never_reject():
    result = classify_image_detections(
        [_det(77, 45, 83, 65)],
        _image(),
        _frontages(include_neighbours=False),
        _boundaries(),
        doors=[{"x1": 75.0, "y1": 30.0, "x2": 85.0, "y2": 80.0, "cx": 80.0, "score": 0.9}],
    )
    assert result[0]["ownership"] == TARGET
    assert result[0]["rejection_reason"] == "nearest_to_target_front_door_fallback"


def test_adjacent_cadastral_frontages_are_built_inside_agent6(monkeypatch):
    target = Polygon([(-4, 10), (4, 10), (4, 20), (-4, 20)])
    right = Polygon([(4, 10), (12, 10), (12, 20), (4, 20)])
    left = Polygon([(-12, 10), (-4, 10), (-4, 20), (-12, 20)])
    parcels = gpd.GeoDataFrame(
        {"CAPAKEY": ["T", "R", "L"]},
        geometry=[target, right, left],
        crs="EPSG:31370",
    )
    monkeypatch.setattr(agent6_reject, "_fetch_adjacent_parcels", lambda *args, **kwargs: parcels)
    site = {"site_id": "S", "parcel_id": "T", "parcel_geom_wkt": target.wkt}
    frontages, status = build_frontages(site, _image(), use_api_cache=True)
    assert status["adjacent_count"] == 2
    assert sum(item["owner"] == TARGET for item in frontages) == 1
    assert sum(item["owner"] == NEIGHBOUR for item in frontages) == 2


def test_split_adjacent_frontage_is_merged_before_casting_rays():
    parcel = Polygon([(4, 10), (8, 10), (12, 10), (12, 20), (4, 20)])
    edge = agent6_reject._road_facing_edge(
        parcel,
        agent6_reject.Point(0, 0),
        agent6_reject.LineString([(-4, 10), (4, 10)]),
    )
    assert edge is not None
    assert math.isclose(edge.length, 8.0, abs_tol=0.01)


def test_geometry_cache_signature_changes_with_camera_heading():
    site = {"site_id": "S", "parcel_geom_wkt": "parcel", "building_geom_wkt": "building"}
    first = agent6_reject._geometry_cache_signature(site, _image())
    changed = {**_image(), "heading": 3.0}
    second = agent6_reject._geometry_cache_signature(site, changed)
    assert first != second


def test_fov110_manual_review_is_preserved_by_agent6(tmp_path):
    result = agent6_reject.process_site(
        {
            "site_id": "S",
            "status": MANUAL_REVIEW,
            "flags": ["FOV110_FALLBACK", "MANUAL_REVIEW"],
            "manual_review": True,
            "manual_review_reason": "mailboxes_detected_only_in_fov110",
            "remarks": "FOV 110 fallback detected 2 mailbox(es)",
            "all_detections": [],
        },
        {"site_id": "S", "images": []},
        {"site_id": "S"},
        tmp_path,
        architectural_cues_enabled=False,
        use_cache=False,
    )
    assert result["status"] == MANUAL_REVIEW
    assert result["manual_review"] is True
    assert result["manual_review_reason"] == "mailboxes_detected_only_in_fov110"
    assert "2 mailbox(es)" in result["remarks"]


def test_frontage_rays_use_camera_facing_parcel_edge_not_stale_agent4_edge():
    image = _image()
    image["left_corner"] = [20.0, 20.0]
    image["right_corner"] = [30.0, 20.0]
    site = {
        "site_id": "S",
        "parcel_id": "T",
        "x": 0.0,
        "y": 15.0,
        "parcel_geom_wkt": "POLYGON ((-5 10, 5 10, 5 25, -5 25, -5 10))",
        "building_geom_wkt": "POLYGON ((-2 12, 2 12, 2 20, -2 20, -2 12))",
    }
    frontages, _ = build_frontages(
        site, image, use_api_cache=True, cached_neighbours=[]
    )
    target = frontages[0]
    assert target["source"] == "agent6_camera_sightline_frontage"
    assert {tuple(target["start"]), tuple(target["end"])} == {
        (-5.0, 10.0),
        (5.0, 10.0),
    }
