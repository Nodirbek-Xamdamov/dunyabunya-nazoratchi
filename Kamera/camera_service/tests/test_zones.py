from camsvc.zones import DeskMatcher, Detection, anchor, point_in_polygon, roi_for

SQUARE = [[0.5, 0.5], [0.9, 0.5], [0.9, 0.9], [0.5, 0.9]]
W, H = 1000, 1000


def test_point_in_polygon():
    assert point_in_polygon(0.7, 0.7, SQUARE)
    assert not point_in_polygon(0.3, 0.7, SQUARE)
    assert not point_in_polygon(0.7, 0.95, SQUARE)


def test_anchor_is_not_the_feet():
    box = (100, 100, 200, 500)
    assert anchor(box) == (150, 300)
    assert anchor(box, "upper") == (150, 220)


def test_roi_around_zones():
    # zone 0.5..0.9: sides +max(0.1, 0.08), top +max(0.1, 0.15), bottom +max(0.1, 0.05)
    assert roi_for([SQUARE], 0.25, W, H) == (400, 350, 1000, 1000)
    assert roi_for([], 0.25, W, H) is None
    assert roi_for([SQUARE], None, W, H) is None


def test_box_cut_by_the_top_of_the_crop_is_not_seated():
    m = DeskMatcher([{"id": 1, "zone": SQUARE}], desk_conf=0.35)
    roi = (400, 350, 1000, 1000)
    standing_cut = Detection((600, 351, 800, 1000), 0.9)  # head outside the crop: anchor would fall in the zone
    seated = Detection((600, 600, 800, 800), 0.9)
    assert m.match(0.0, [standing_cut], W, H, roi) == {1: None}
    assert m.match(0.2, [seated], W, H, roi) == {1: seated}
    assert m.match(0.4, [standing_cut], W, H, None) == {1: standing_cut}  # whole frame: nothing is cut


def test_confidence_and_zone():
    m = DeskMatcher([{"id": 1, "zone": SQUARE}], desk_conf=0.35)
    inside_weak = Detection((600, 600, 800, 800), 0.2)
    outside = Detection((100, 100, 300, 300), 0.9)
    inside = Detection((600, 600, 800, 800), 0.8)
    assert m.match(0.0, [inside_weak, outside], W, H) == {1: None}
    assert m.match(0.2, [inside], W, H) == {1: inside}


def test_walking_person_does_not_count():
    m = DeskMatcher([{"id": 1, "zone": SQUARE}], desk_conf=0.35)
    seen = []
    for i in range(10):  # 2 s at 5 fps, the box moves 40 px per frame (box height 200 px)
        x = 520 + 40 * i
        seen.append(m.match(i * 0.2, [Detection((x - 50, 600, x + 50, 800), 0.9, track_id=7)], W, H)[1])
    assert seen[0] is not None and all(s is None for s in seen[4:])


def test_sitting_person_with_small_moves_counts():
    m = DeskMatcher([{"id": 1, "zone": SQUARE}], desk_conf=0.35)
    seen = [m.match(i * 0.2, [Detection((600 + i % 3 * 5, 600, 700, 800), 0.9, track_id=3)], W, H)[1]
            for i in range(25)]
    assert all(s is not None for s in seen)
