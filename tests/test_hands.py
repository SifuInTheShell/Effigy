"""M70 fingers from close-up hand renders: the views round the forearm, the rays back from a view, the points met
from the views that saw the hand, a view that disagrees dropped, and the checks that hand it back to DWPose."""

import numpy as np
import pytest

pytest.importorskip("torch")

from steps import _hands
from steps._views import ortho

WRIST, ELBOW = (
    np.array([0.78, 1.43, -0.05]),
    np.array([0.52, 1.43, -0.05]),
)  # a T-pose's left forearm


def _hand():
    """21 points of a flat hand past the wrist (the wrist first, four per finger), and a box of faces round them."""
    pts = [WRIST]
    for k in range(5):
        side = (k - 2) * 0.02
        for j in range(1, 5):
            pts.append(WRIST + [0.03 + 0.025 * j, -0.01 * (k == 0) * j, side])
    pts = np.array(pts)
    lo, hi = pts.min(0) - 0.02, pts.max(0) + 0.02
    v = np.array(
        [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    )
    f = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1]])
    f = np.r_[f, [[2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]]]
    return pts, v, f


class _Seen:
    """A detector that sees `pts` in the views it is told to, through each view's camera in order."""

    def __init__(self, pts, see, lie=None):
        self.cams = [
            ortho(y, p, *_hands.views(WRIST, ELBOW)[:2]) for y, p in _hands.views(WRIST, ELBOW)[2]
        ]
        self.pts, self.see, self.lie, self.k = pts, see, lie, -1

    def __call__(self, img):
        self.k += 1
        assert img.shape == (_hands.SIZE, _hands.SIZE, 3) and img.dtype == np.uint8
        if self.k not in self.see:
            return None
        pix = self.cams[self.k]["project"](self.pts)[0]
        if self.k == self.lie:
            pix = pix + [0.2, 0.0]  # a misread hand
        return pix


def test_the_views_go_round_the_forearm_and_none_looks_straight_down():
    centre, ext, cams = _hands.views(WRIST, ELBOW)
    assert len(cams) == _hands.VIEWS and centre == pytest.approx(WRIST + [0.4 * 0.26, 0, 0])
    assert ext == pytest.approx(1.6 * 0.26)
    u = (WRIST - ELBOW) / np.linalg.norm(WRIST - ELBOW)
    for yaw, pitch in cams:
        d = ortho(yaw, pitch, centre, ext)["towards"](np.zeros((1, 3)))[0]
        assert (
            abs(d @ u) < 1e-9 and abs(pitch) < 89
        )  # across the forearm, never straight up or down


def test_a_pixel_goes_back_onto_its_ray():
    cam = ortho(30, -40, [0.1, 1.0, 0.2], 0.5)
    p = np.array([[0.15, 1.1, 0.05], [0.0, 0.9, 0.3]])
    back = cam["unproject"](cam["project"](p)[0])
    d = cam["towards"](np.zeros((1, 3)))[0]
    assert np.linalg.norm(np.cross(back - p, d), axis=1) == pytest.approx([0, 0], abs=1e-12)


def test_the_points_are_met_from_the_views_that_saw_the_hand():
    pts, v, f = _hand()
    got, info = _hands.points(v, f, WRIST, ELBOW, _Seen(pts, see={1, 2, 5}))
    assert info == {"views": 8, "used": 3} and got == pytest.approx(pts, abs=1e-9)


def test_a_view_that_misreads_the_hand_is_dropped():
    pts, v, f = _hand()
    got, info = _hands.points(v, f, WRIST, ELBOW, _Seen(pts, see={1, 2, 3}, lie=2))
    assert info["used"] == 2 and got == pytest.approx(pts, abs=1e-9)


@pytest.mark.parametrize(
    "see, wrist, why",
    [
        ({3}, WRIST, "fewer than two views"),
        (set(), WRIST, "fewer than two views"),
        ({1, 3}, WRIST + [0, 0.2, 0], "far from the fitted wrist"),
        ({1, 5}, WRIST, "along one line"),  # opposite views: their rays are parallel
    ],
)
def test_too_little_or_the_wrong_place_goes_back_to_dwpose(see, wrist, why):
    pts, v, f = _hand()
    seen = _Seen(pts - WRIST + wrist, see=see)
    got, info = _hands.points(v, f, WRIST, ELBOW, seen)
    assert got is None and why in info["why"]


def test_no_forearm_no_hand():
    pts, v, f = _hand()
    got, info = _hands.points(v, f, WRIST, WRIST, _Seen(pts, see={1, 3}))
    assert got is None and "no forearm" in info["why"]


def test_rig_fit_takes_mediapipe_then_dwpose_then_the_proportions():
    pts, v, f = _hand()
    markers = {"LeftWrist": WRIST, "LeftElbow": ELBOW, "RightWrist": WRIST * [-1, 1, 1]}
    markers["RightElbow"] = ELBOW * [-1, 1, 1]
    dwpose = {"Right": np.zeros((21, 3))}
    # no model: DWPose's right hand stands, the left gets the proportions
    hands, by, seen = _hands.found(v, f, markers, dwpose, None)
    assert by["Left"] == "proportions" and hands.keys() == {"Right"} and seen == {}

    def broken(img):
        raise RuntimeError("the model would not run")

    hands, by, seen = _hands.found(v, f, markers, dwpose, broken)  # a failure falls back, said why
    assert (
        by == {"Left": "proportions", "Right": "dwpose"} and "RuntimeError" in seen["Left"]["why"]
    )
    hands, by, seen = _hands.found(v, f, markers, dwpose, _Seen(pts, see={1, 3}))
    assert by["Left"] == "mediapipe" and hands["Left"] == pytest.approx(pts, abs=1e-9)
    assert seen["Left"] == {"views": 8, "used": 2}


def test_the_credit_has_a_licence_row():
    from pathlib import Path

    rows = (Path(__file__).parents[1] / "licenses.yaml").read_text(encoding="utf-8")
    assert f"name: {_hands.CREDIT}," in rows


def test_no_model_no_finder(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFIGY_MODELS", str(tmp_path))
    assert _hands.finder() is None


def test_the_skin_render_shows_the_hand_on_near_white():
    _, v, f = _hand()
    centre, ext, cams = _hands.views(WRIST, ELBOW)
    img = _hands.shaded(v, f, ortho(*cams[0], centre, ext), 64)
    assert img[0, 0].tolist() == [242, 242, 242]  # the background
    mid = img[32, 32].astype(int)
    assert mid[0] > mid[1] > mid[2] and 0.45 * 224 - 1 <= mid[0] <= 224  # skin, lit
