from .agent7_floor_count import estimate_floor_rows


def _object(kind, cy, height=20, y2=None):
    y1 = cy - height / 2
    bottom = cy + height / 2 if y2 is None else y2
    return {
        "kind": kind,
        "cy": float(cy),
        "height": float(height),
        "box": [0.0, float(y1), 20.0, float(bottom)],
    }


def test_window_rows_are_counted_as_floors():
    result = estimate_floor_rows(
        [_object("window", 30), _object("window", 33), _object("window", 90), _object("window", 92)],
        140,
    )
    assert result["floor_count"] == 2
    assert len(result["rows"]) == 2


def test_door_only_ground_floor_is_added_below_upper_windows():
    result = estimate_floor_rows(
        [_object("window", 30), _object("window", 70), _object("door", 105, 50, 135)],
        140,
    )
    assert result["floor_count"] == 3
    assert result["door_only_ground_added"] is True


def test_dormer_row_counts_as_attic_floor():
    result = estimate_floor_rows(
        [_object("dormer", 20), _object("window", 65), _object("window", 110)],
        140,
    )
    assert result["floor_count"] == 3
    assert result["attic_detected"] is True
