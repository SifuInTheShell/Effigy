"""steps/_rig.py (M19) on the CPU: the template, rays, the inside test, the snap, markers → 52 joints."""

import numpy as np
import trimesh

from steps import _rig


def _box(size=(1.0, 1.0, 1.0), at=(0, 0, 0)):
    b = trimesh.creation.box(size)
    return np.asarray(b.vertices) + at, np.asarray(b.faces)


def block_figure():
    """Six boxes (torso, head, arms, legs; 1.8 m, facing +Z) and the 52-joint skeleton fitted to them, as
    skeleton.json's joints."""
    parts = [
        _box((0.4, 0.6, 0.25), (0, 1.2, 0)),
        _box((0.22, 0.25, 0.22), (0, 1.62, 0)),
        _box((0.6, 0.1, 0.1), (0.5, 1.4, 0)),
        _box((0.6, 0.1, 0.1), (-0.5, 1.4, 0)),
        _box((0.14, 0.9, 0.14), (0.1, 0.45, 0)),
        _box((0.14, 0.9, 0.14), (-0.1, 0.45, 0)),
    ]
    v = np.concatenate([p[0] for p in parts])
    f = np.concatenate([p[1] + sum(len(q[0]) for q in parts[:i]) for i, p in enumerate(parts)])
    m = {"Chin": [0, 1.52, 0], "Groin": [0, 0.88, 0]}
    for s, x in (("Left", 1), ("Right", -1)):
        m |= {
            f"{s}Shoulder": [x * 0.22, 1.4, 0],
            f"{s}Elbow": [x * 0.5, 1.4, 0],
            f"{s}Wrist": [x * 0.75, 1.4, 0],
        }
        m |= {f"{s}Knee": [x * 0.1, 0.48, 0], f"{s}Ankle": [x * 0.1, 0.08, 0]}
    j = _rig.skeleton(m, v, f)
    parent = dict(_rig.template())
    joints = [{"name": n, "parent": parent[n], "position": list(p)} for n, p in j.items()]
    return parts, joints


def test_nearest_bone_for_rigid_parts():
    """M32: a rigid part's default bone is the body bone passing nearest its centre, never a finger."""
    _, joints = block_figure()
    pos = {j["name"]: np.asarray(j["position"]) for j in joints}
    assert _rig.nearest_bone(np.array([0, 1.66, 0.0]), joints) == "Head"  # a helmet's centre
    assert _rig.nearest_bone(pos["LeftHandMiddle2"], joints) == "LeftHand"  # a held sword
    elbow_to_wrist = (pos["LeftForeArm"] + pos["LeftHand"]) / 2 + [0, 0, 0.08]
    assert _rig.nearest_bone(elbow_to_wrist, joints) == "LeftForeArm"  # a shield on the forearm
    assert _rig.nearest_bone(pos["RightToeBase"] + [0, 0, 0.05], joints) == "RightToeBase"


def test_tail_table_matches_the_blender_skinning():
    import ast
    from pathlib import Path

    src = (Path(__file__).parents[1] / "blender_ops" / "skin_proxy.py").read_text(encoding="utf-8")
    node = next(
        n for n in ast.parse(src).body if isinstance(n, ast.Assign) and n.targets[0].id == "TAIL"
    )
    assert ast.literal_eval(node.value) == _rig.TAIL


def test_template_is_mixamo_52():
    t = _rig.template()
    names = [n for n, _ in t]
    assert len(names) == 52 == len(set(names))
    assert [n for n, p in t if p is None] == ["Hips"]
    assert all(p in names for _, p in t if p)
    assert sum("Hand" in n and n[-1].isdigit() for n in names) == 30  # 5 fingers × 3 × 2


def test_hits_and_mid_depth():
    v, f = _box()
    np.testing.assert_allclose(_rig.hits(v, f, np.array([0.1, 0.2, 0.0])), [-0.5, 0.5])
    assert abs(_rig.mid_depth(v, f, 0.1, 0.2)) < 1e-9
    assert _rig.mid_depth(v, f, 0.9, 0.0) is None  # beside the box


def test_a_hollow_limb_is_one_span():
    """A generated limb can be two 1 mm walls round a hollow: its middle is the limb's, not the front
    wall's, and a joint in the wall reads as on the skin."""
    v, f = _box((0.1, 0.6, 0.1))
    vi, fi = _box((0.098, 0.598, 0.098))
    # the inner wall faces in
    v, f = np.concatenate([v, vi]), np.concatenate([f, fi[:, ::-1] + len(v)])
    assert abs(_rig.mid_depth(v, f, 0.0, 0.0)) < 1e-9
    joints = [
        {"name": "Hips", "parent": None, "position": [0, 0.2, 0]},
        {"name": "Spine", "parent": "Hips", "position": [0, 0, 0.0495]},  # in the front wall
        {"name": "Spine1", "parent": "Spine", "position": [0, -0.2, 0]},
    ]
    c = _rig.fit_quality(v, f, joints)["centred"]
    assert c["Spine"] < 0.05 and c["Hips"] > 0.95
    # air between two limbs stays two spans: the viewer's (+Z) limb first, or the one nearest `near`
    v2, f2 = _box((0.1, 0.6, 0.1), (0, 0, 0.3))
    v, f = np.concatenate([v, v2]), np.concatenate([f, f2 + len(v)])
    assert len(_rig.spans(v, f, np.zeros(3))) == 2
    assert abs(_rig.mid_depth(v, f, 0.0, 0.0) - 0.3) < 1e-9
    assert abs(_rig.mid_depth(v, f, 0.0, 0.0, near=0.0)) < 1e-9


def test_inside_survives_flipped_and_open_meshes():
    v, f = _box()
    pts = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 2.0]])
    np.testing.assert_allclose(_rig.winding(v, f, pts), [1, 0], atol=1e-6)
    assert _rig.inside(v, f, pts).tolist() == [True, False]
    assert _rig.inside(v, f[:, ::-1], pts).tolist() == [True, False]  # wound inward: winding -1
    top = np.argmax(v[f].mean(1)[:, 1])
    assert _rig.inside(v, np.delete(f, top, 0), pts).tolist() == [True, False]  # a hole on top


def test_snap_moves_an_edge_joint_into_a_thin_limb():
    v, f = _box((0.04, 0.6, 0.04))  # an arm 4 cm thick
    p = np.array([0.03, 0.1, 0.0])  # 1 cm off its side
    q = _rig.snap_inside(v, f, p, reach=0.05)
    assert _rig.inside(v, f, q[None])[0] and np.linalg.norm(q - p) < 0.05


def test_skeleton_from_markers_on_a_block_figure():
    parts = [  # torso, head, arms out, legs: a crude A-pose figure 1.8 m tall, facing +Z
        _box((0.4, 0.6, 0.25), (0, 1.2, 0)),
        _box((0.22, 0.25, 0.22), (0, 1.62, 0)),
        _box((0.6, 0.1, 0.1), (0.5, 1.4, 0)),
        _box((0.6, 0.1, 0.1), (-0.5, 1.4, 0)),
        _box((0.14, 0.9, 0.14), (0.1, 0.45, 0)),
        _box((0.14, 0.9, 0.14), (-0.1, 0.45, 0)),
    ]
    v = np.concatenate([p[0] for p in parts])
    f = np.concatenate([p[1] + sum(len(q[0]) for q in parts[:i]) for i, p in enumerate(parts)])
    m = {"Chin": [0, 1.52, 0], "Groin": [0, 0.88, 0]}
    for s, x in (("Left", 1), ("Right", -1)):
        m |= {
            f"{s}Shoulder": [x * 0.22, 1.4, 0],
            f"{s}Elbow": [x * 0.5, 1.4, 0],
            f"{s}Wrist": [x * 0.75, 1.4, 0],
        }
        m |= {f"{s}Knee": [x * 0.1, 0.48, 0], f"{s}Ankle": [x * 0.1, 0.08, 0]}
    j = _rig.skeleton(m, v, f)
    assert list(j) == [n for n, _ in _rig.template()]
    pos = np.array(list(j.values()))
    assert np.isfinite(pos).all()
    assert (
        j["Hips"][1] < j["Spine"][1] < j["Spine1"][1] < j["Spine2"][1] < j["Neck"][1] < j["Head"][1]
    )
    assert j["LeftHand"][0] > 0 > j["RightHand"][0]  # the character's left is +X
    assert _rig.inside(
        v, f, np.array([j[n] for n in ("Hips", "Spine1", "Neck", "LeftArm", "LeftUpLeg")])
    ).all()


def test_inside_counts_a_hollow_shells_interior():
    """A robot built from plates: the middle of a hollow box (two shells) is enclosed, a point beside it
    isn't."""
    outer, fo = _box((1.0, 1.0, 1.0))
    inner, fi = _box((0.9, 0.9, 0.9))
    v, f = np.r_[outer, inner], np.r_[fo, fi[:, ::-1] + len(outer)]  # inner shell faces in
    assert _rig.inside(v, f, np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.8]])).tolist() == [True, False]
    # the same cavity with both shells open at the top (a neck hole): still inside, still not beside it
    tops = [i for i in range(len(f)) if v[f[i]].mean(0)[1] > 0.4]
    assert _rig.inside(
        v, np.delete(f, tops, 0), np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.8]])
    ).tolist() == [True, False]


def test_rig_skin_picks_fewest_fails_then_warnings_then_stretch():
    """M20 engine choice (rig_skin.rank): a clean rig beats a stretched one whatever its engine."""
    from steps.rig_skin import rank

    base = {"unweighted_vertices": 0, "weight_sum_off": 0, "max_influences": 4, "lr_asymmetry": 0.1}
    clean = base | {"pose_volume_change_pct_max": 2.0, "pose_edge_stretch_max": 2.0}
    warned = base | {"pose_volume_change_pct_max": 8.0, "pose_edge_stretch_max": 2.5}
    failed = base | {"pose_volume_change_pct_max": 2.0, "pose_edge_stretch_max": 12.0}
    assert rank(clean) < rank(warned) < rank(failed)
    assert rank(clean | {"pose_edge_stretch_max": 1.5}) < rank(
        clean
    )  # same checks: less stretch wins


def test_sides_swapped_when_the_labels_contradict_the_facing():
    # a character facing -Z (facing -1) has its left at -X; this detection put "Left" at +X
    markers = {
        "LeftShoulder": np.array([0.2, 1.4, 0.0]),
        "RightShoulder": np.array([-0.2, 1.4, 0.0]),
        "Chin": np.zeros(3),
    }
    conf = {"LeftShoulder": 0.9, "RightShoulder": 0.8, "Chin": 0.7}
    hands = {"Left": np.zeros((21, 3)), "Right": np.ones((21, 3))}
    assert _rig.sides_agree(markers, 1) and not _rig.sides_agree(markers, -1)
    m, c, h = _rig.swap_sides(markers, conf, hands)
    assert (
        m["LeftShoulder"][0] == -0.2
        and m["RightShoulder"][0] == 0.2
        and m["Chin"] is markers["Chin"]
    )
    assert c["LeftShoulder"] == 0.8 and h["Left"][0, 0] == 1 and _rig.sides_agree(m, -1)


def test_feet_cue_reads_the_facing_from_the_toes():
    rng = np.random.default_rng(0)
    body = np.c_[
        rng.uniform(-0.1, 0.1, 400), rng.uniform(0.1, 1.0, 400), rng.uniform(-0.1, 0.1, 400)
    ]
    feet = np.c_[
        rng.uniform(-0.1, 0.1, 100), rng.uniform(0.0, 0.05, 100), rng.uniform(-0.05, 0.15, 100)
    ]
    v = np.r_[body, feet]  # toes reach to +Z
    assert _rig.feet_cue(v) > 0.01
    assert _rig.feet_cue(v * np.array([1, 1, -1])) < -0.01  # the same character facing -Z
    assert (
        abs(_rig.feet_cue(np.r_[body, feet * np.array([1, 1, 0])])) < 0.01
    )  # round shoes: no call


def test_palm_normals_follow_the_hand_shape_when_the_fingers_do_not():
    # a flat hand slab beyond the left wrist, horizontal (its thin axis is Y): the palm faces down
    rng = np.random.default_rng(1)
    j = {n: np.zeros(3) for n, _ in _rig.template()}
    j["Hips"], j["Head"] = np.array([0, 1.0, 0.0]), np.array([0, 1.7, 0.0])
    j["LeftForeArm"], j["LeftHand"] = np.array([0.3, 1.0, 0.0]), np.array([0.5, 1.0, 0.0])
    j["RightForeArm"], j["RightHand"] = np.array([-0.3, 1.0, 0.0]), np.array([-0.5, 1.0, 0.0])
    slab = lambda x0: np.c_[
        rng.uniform(x0, x0 + 0.18, 400),
        1.0 + rng.uniform(-0.01, 0.01, 400),
        rng.uniform(-0.05, 0.05, 400),
    ]
    v = np.r_[slab(0.5), slab(-0.68), rng.uniform(-0.2, 0.2, (300, 3)) + [0, 1.0, 0]]
    # fingers that say the palm faces UP on the left (the pose model's slip), and down on the right
    j["LeftHandMiddle1"], j["LeftHandThumb1"], j["LeftHandPinky1"] = (
        [0.6, 1.0, 0.0],
        [0.55, 1.0, 0.04],
        [0.55, 1.0, -0.04],
    )
    j["RightHandMiddle1"], j["RightHandThumb1"], j["RightHandPinky1"] = (
        [-0.6, 1.0, 0.0],
        [-0.55, 1.0, 0.04],
        [-0.55, 1.0, -0.04],
    )
    j = {k: np.asarray(p, float) for k, p in j.items()}
    out = _rig.palm_normals(j, v)
    assert (
        out["Left"][1] < -0.9 and out["Right"][1] < -0.9
    )  # both palms down, whatever the fingers said


def limb():
    """A limb: a 0.2 x 0.2 x 2 m box along Z, and three joints in a row inside it under a root on the floor
    beside it (a creature's)."""
    v, f = _box((0.2, 0.2, 2.0))
    joints = [
        {"name": "root", "parent": None, "position": [0.0, -0.5, 0.0]},
        {"name": "A", "parent": "root", "position": [0.0, 0.0, -0.6]},
        {"name": "B", "parent": "A", "position": [0.0, 0.0, 0.0]},
        {"name": "C", "parent": "B", "position": [0.0, 0.0, 0.6]},
    ]
    return v, f, joints


def test_fit_quality_scores_how_centred_the_joints_sit():
    """In the middle of the limb: 1 and a score of 100; a joint near its skin: low and listed; one outside:
    0; a bone across the air: its share outside; the root on the floor is left out, the end joint (C) only
    checked for being inside."""
    v, f, joints = limb()
    q = _rig.fit_quality(v, f, joints)
    assert q["score"] == 100 and q["joints"] == 3 and q["inside"] == 3 and not q["attention"]
    assert (
        "root" not in q["centred"] and "C" not in q["centred"] and not q["bones_outside"]
    )  # C: an end
    joints[2]["position"] = [0.04, 0.0, 0.0]  # 6 cm from the skin across x, in the middle across y
    q = _rig.fit_quality(v, f, joints)
    assert q["centred"]["B"] == 0.6 and ["B", "close to the skin"] not in q["attention"]
    joints[2]["position"] = [0.08, 0.0, 0.0]  # 2 cm: the worse direction counts
    assert ["B", "close to the skin"] in _rig.fit_quality(v, f, joints)["attention"]
    joints[3]["position"] = [
        0.0,
        0.5,
        0.6,
    ]  # an end joint (C) dragged out: it counts now, as does its bone
    q = _rig.fit_quality(v, f, joints)
    assert (
        q["centred"]["C"] == 0
        and q["score"] < 60
        and q["attention"][0] == ["C", "outside the model"]
    )
    joints[3]["position"] = [0.0, 0.0, 0.6]
    joints[2]["position"] = [0.5, 0.0, 0.0]  # out to the side: its two bones cross the air
    q = _rig.fit_quality(v, f, joints)
    assert q["centred"]["B"] == 0 and q["attention"][0] == ["B", "outside the model"]
    assert (
        q["bones_outside"]["A > B"] > 0.5
        and ["C", "the bone from B leaves the body"] in q["attention"]
    )
    assert q["score"] < 50  # A in the middle, B outside, and its bones cross the air
