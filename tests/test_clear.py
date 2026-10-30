"""M67 arm clearance: a wide torso skinned on our 52 joints; a clip that lets the arms hang through it comes out
with them opened just enough, eased in time and round a loop; what clears, or already overlaps at rest, is left."""

import itertools
import math
from pathlib import Path

import numpy as np
import pytest

from steps import _anim, _clear, _connect, anim
from steps._common import StepError
from tests.test_connect import glb, our_rest

SPANS = {"Hips": (0.85, 1.1), "Spine": (1.1, 1.25), "Spine1": (1.25, 1.4), "Spine2": (1.4, 1.55)}


def _rz(deg):
    t = math.radians(deg)
    return [[math.cos(t), -math.sin(t), 0], [math.sin(t), math.cos(t), 0], [0, 0, 1]]


def _arms(deg_left, deg_right, times=(0, 1)):
    """The arms' world turns about +Z over `times` (each a list per time), parents at rest."""
    return [
        ("LeftArm", "rotation", list(times), [_rz(d) for d in deg_left]),
        ("RightArm", "rotation", list(times), [_rz(d) for d in deg_right]),
    ]


def skinned(clips, half=(0.2, 0.12), centre=0.0):
    """`glb(clips)` with a skinned mesh: an elliptic torso (rings round the spine, each on its bone, half-widths
    `half` across and front to back, centred `centre` metres to the left) and thin forearms and hands."""
    doc, blob = glb(clips)
    blob, rest = bytearray(blob), our_rest()
    phi = np.linspace(0, 2 * math.pi, 24, endpoint=False)
    pts, owner, rng = [], [], np.random.default_rng(0)
    for b, (y0, y1) in SPANS.items():  # each ring at a height of its own, as a mesh's vertices fall
        for y in rng.uniform(y0, y1, 6):
            pts += [(centre + half[0] * math.cos(f), y, half[1] * math.sin(f)) for f in phi]
            owner += [b] * len(phi)
    for s in ("Left", "Right"):
        for b, end, rr in (
            (f"{s}ForeArm", f"{s}Hand", 0.035),
            (f"{s}Hand", f"{s}HandMiddle1", 0.02),
        ):
            a, c = rest[b], rest[end]
            d = (c - a) / np.linalg.norm(c - a)
            u = np.cross(d, [0, 0, 1.0])
            u, w = u / np.linalg.norm(u), np.cross(d, u / np.linalg.norm(u))
            for t in np.linspace(0, 1, 6):
                pts += [a + t * (c - a) + rr * (math.cos(f) * u + math.sin(f) * w) for f in phi]
                owner += [b] * len(phi)
    joints = doc["skins"][0]["joints"]
    slot = {doc["nodes"][j]["name"]: k for k, j in enumerate(joints)}
    world = _connect._world(doc, {})
    ibm = [np.linalg.inv(world[j]).T.ravel() for j in joints]  # column-major

    def add(arr, ctype, typ, dt):
        a = np.ascontiguousarray(arr, dt)
        blob.extend(b"\0" * (-len(blob) % 4))
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(blob), "byteLength": a.nbytes})
        blob.extend(a.tobytes())
        doc["accessors"].append(
            {
                "bufferView": len(doc["bufferViews"]) - 1,
                "componentType": ctype,
                "count": len(a),
                "type": typ,
            }
        )
        return len(doc["accessors"]) - 1

    att = {
        "POSITION": add(pts, 5126, "VEC3", "<f4"),
        "JOINTS_0": add([(slot[o], 0, 0, 0) for o in owner], 5123, "VEC4", "<u2"),
        "WEIGHTS_0": add([(1, 0, 0, 0)] * len(owner), 5126, "VEC4", "<f4"),
    }
    doc["skins"][0]["inverseBindMatrices"] = add(ibm, 5126, "MAT4", "<f4")
    doc["meshes"] = [{"primitives": [{"attributes": att}]}]
    doc["nodes"].append({"name": "Body", "mesh": 0, "skin": 0})
    doc["buffers"][0]["byteLength"] = len(blob)
    return doc, bytes(blob)


def _world_at(glb_bytes, path, clip, f, fps=30):
    path.write_bytes(glb_bytes)
    doc, blob = _anim.read_glb(path)
    P, _ = _connect.pose(doc, blob, _connect.rig(doc), clip, [f / fps])
    return {n: p[0] for n, p in P.items()}


def test_hanging_arms_open_just_enough(tmp_path):
    # the A-pose's arms (45° down) turned 45° more: straight down, through a torso 0.4 m wide
    doc, blob = skinned({"Hang": _arms([-45, -45], [45, 45])})
    new, rep = _clear.clear(doc, blob, 30)
    # the elbow (the forearm's radius 0.035, the margin 1 % of 1.63 m) clears the torso's side at 0.193 m
    # (the rings' 5–95 % range) when 0.18 + 0.28 sin θ ≥ 0.245: θ just over 13°, the next whole degree
    assert rep == {"Hang": {"Left": 14.0, "Right": 14.0}}
    p = _world_at(new, tmp_path / "out.glb", "Hang", 10)
    for s, x in (("Left", 1), ("Right", -1)):
        assert p[f"{s}Hand"] == pytest.approx(
            [
                x * (0.18 + 0.53 * math.sin(math.radians(14))),
                1.5 - 0.53 * math.cos(math.radians(14)),
                0,
            ],
            abs=1e-4,
        )
    assert p["Head"] == pytest.approx(our_rest()["Head"], abs=1e-6)  # the rest as it was
    doc2, blob2 = _anim.read_glb(tmp_path / "out.glb")
    assert _clear.clear(doc2, blob2, 30) == (None, {})  # now clear


def test_clear_arms_and_the_rest_are_left_alone():
    doc, blob = skinned({"Stand": _arms([0, 0], [0, 0]), "Up": _arms([60, 60], [-60, -60])})
    assert _clear.clear(doc, blob, 30) == (None, {})
    # a torso 0.6 m wide, its centre 6 cm to the left: the left elbow is inside it at rest, which is allowed
    doc, blob = skinned({"Stand": _arms([0, 0], [0, 0])}, half=(0.3, 0.12), centre=0.06)
    assert _clear.clear(doc, blob, 30) == (None, {})


def test_the_torso_is_one_column_and_float_noise_at_rest_is_no_overlap():
    doc, blob = skinned({"Stand": _arms([0, 0], [0, 0])}, half=(0.3, 0.12), centre=0.06)
    r = _connect.rig(doc)
    B = _clear.body(doc, blob, r)
    up = np.array([0, 1.0, 0])
    spans = sorted(
        (r["pos"][c["bone"]] @ up + c["y"][0], r["pos"][c["bone"]] @ up + c["y"][1])
        for c in B["cyl"]
    )
    assert all(
        hi >= nxt_lo for (_, hi), (nxt_lo, _) in itertools.pairwise(spans)
    )  # no gap between two
    # the left elbow overlaps it at rest; arms a millionth of a degree in from the rest are no deeper
    doc, blob = skinned(
        {"Stand": _arms([-1e-6, -1e-6], [1e-6, 1e-6])}, half=(0.3, 0.12), centre=0.06
    )
    assert _clear.clear(doc, blob, 30) == (None, {})


def test_a_torso_too_wide_to_clear_is_reported():
    doc, blob = skinned(
        {"Hang": _arms([-45, -45], [45, 45])}, half=(0.42, 0.12)
    )  # 20 % of the height at most
    _, rep = _clear.clear(doc, blob, 30)
    assert rep == {
        "Hang": {"Left": 40.0, "Right": 40.0, "uncleared": 31}
    }  # every frame, counted once


def test_the_opening_is_eased_and_wraps_round_a_loop():
    need = np.zeros(31)
    need[14:17] = 10.0
    out = _clear._ease(need, 30, loop=False)
    assert (out >= need).all() and out[10] > 5 and out[0] < 0.5 and out[20] > 5
    assert out == pytest.approx(out[::-1])  # held and blurred the same before and after
    need = np.zeros(31)
    need[:2] = need[-1] = 10.0  # a loop's last frame is its first
    out = _clear._ease(need, 30, loop=True)
    assert out[-1] == out[0] and out[-3] > 5 and out[15] < 0.5


def test_a_swing_through_opens_before_and_after(tmp_path):
    # in the A-pose, down through the torso at 0.5 s, back: the arm opens before it gets there
    doc, blob = skinned({"Swing": _arms([0, -45, 0], [0, 45, 0], times=(0, 0.5, 1))})
    new, rep = _clear.clear(doc, blob, 30)
    assert rep["Swing"]["Left"] == 14.0
    ang = []
    for f in range(31):
        p = _world_at(new, tmp_path / "out.glb", "Swing", f)
        d = p["LeftHand"] - p["LeftArm"]
        ang.append(math.degrees(math.atan2(d[0], -d[1])))  # the arm's angle out from straight down
    was = [
        45 - 3 * min(f, 30 - f) for f in range(31)
    ]  # 45° out, 0° at 0.5 s; inside the torso under 14°
    assert min(ang) == pytest.approx(14.0, abs=0.05)  # at 0.5 s: just clear
    assert all(a >= max(w, 14.0) - 0.05 for a, w in zip(ang, was))  # never less than a frame needs
    assert (
        ang[9] > was[9] + 1 and ang[21] > was[21] + 1
    )  # opening before it gets there, closing after
    assert abs(ang[0] - 45) < 1 and ang[0] == pytest.approx(
        ang[30], abs=1e-3
    )  # a loop: both ends alike


def test_the_anim_step_clears_the_arms_unless_told_not_to(tmp_path, monkeypatch):
    skin = tmp_path / "asset" / "060_rig_skin"
    skin.mkdir(parents=True)
    (skin / "rigged.glb").write_bytes(b"glb")
    # a held pose would be a clip that never moves (the gate before this pass); this one swings
    swing = _arms([0, -45, 0], [0, 45, 0], times=(0, 0.5, 1))
    made = {"glb": skinned({"Swing": swing})}
    monkeypatch.setattr(anim, "catalogue", lambda: [{"name": "Swing", "file": "pack.glb"}])

    def fake_blender(op, inputs, outdir, params, timeout=None):
        Path(outdir).mkdir(parents=True, exist_ok=True)
        (Path(outdir) / "rigged_anim.glb").write_bytes(_anim.pack(*made["glb"]))
        return {"report": {"clips": [{"name": "Swing", "frames": 31, "seconds": 1.0}], "fps": 30}}

    monkeypatch.setattr(anim, "run_blender", fake_blender)
    m = anim.run("run", str(skin), str(tmp_path / "a"), {"clips": ["Swing"]})["metrics"]
    assert m["arms_opened"] == {"Swing": {"Left": 14.0, "Right": 14.0}}
    p = _world_at(
        (tmp_path / "a" / "rigged_anim.glb").read_bytes(), tmp_path / "x.glb", "Swing", 15
    )
    assert p["LeftHand"][0] == pytest.approx(
        0.18 + 0.53 * math.sin(math.radians(14)), abs=1e-4
    )  # written
    m = anim.run("run", str(skin), str(tmp_path / "b"), {"clips": ["Swing"], "clearance": False})[
        "metrics"
    ]
    assert "arms_opened" not in m
    p = _world_at(
        (tmp_path / "b" / "rigged_anim.glb").read_bytes(), tmp_path / "x.glb", "Swing", 15
    )
    assert p["LeftHand"][0] == pytest.approx(0.18, abs=1e-4)  # straight down, as retargeted
    # no skinned mesh: the clips stay as retargeted, and the version says why
    made["glb"] = glb({"Swing": swing})
    m = anim.run("run", str(skin), str(tmp_path / "c"), {"clips": ["Swing"]})["metrics"]
    assert "arms_opened" not in m and m["arms_clearance_skipped"].startswith("no skinned torso")
    with pytest.raises(StepError) as e:
        anim.run("run", str(skin), str(tmp_path / "d"), {"clips": ["Swing"], "clearance": "yes"})
    assert e.value.code == "bad_params"
