"""M65 connect clips: our pose read from a GLB, carried onto Kimodo's SOMA skeleton by the inverse of the
retarget (a pose the retarget's maths makes from a SOMA pose comes back as that pose), the seam layout and the
landing of the second clip, Kimodo's output cut back to the seams, and the motion step's connect params."""

import ast
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from steps import _anim, _connect, _rig, motion
from steps._common import StepError

ROOT = Path(__file__).resolve().parents[1]
# Kimodo's somaskel30 (kimodo/skeleton/definitions.py at the pinned commit): the joints the model is conditioned on
SOMA30 = [
    ("Hips", None),
    ("Spine1", "Hips"),
    ("Spine2", "Spine1"),
    ("Chest", "Spine2"),
    ("Neck1", "Chest"),
    ("Neck2", "Neck1"),
    ("Head", "Neck2"),
    ("Jaw", "Head"),
    ("LeftEye", "Head"),
    ("RightEye", "Head"),
]
for _s in ("Left", "Right"):
    SOMA30 += [
        (f"{_s}Shoulder", "Chest"),
        (f"{_s}Arm", f"{_s}Shoulder"),
        (f"{_s}ForeArm", f"{_s}Arm"),
        (f"{_s}Hand", f"{_s}ForeArm"),
        (f"{_s}HandThumbEnd", f"{_s}Hand"),
        (f"{_s}HandMiddleEnd", f"{_s}Hand"),
    ]
for _s in ("Left", "Right"):
    SOMA30 += [
        (f"{_s}Leg", "Hips"),
        (f"{_s}Shin", f"{_s}Leg"),
        (f"{_s}Foot", f"{_s}Shin"),
        (f"{_s}ToeBase", f"{_s}Foot"),
    ]
NAMES30 = [n for n, _ in SOMA30]
PARENTS30 = [p for _, p in SOMA30]


def our_rest() -> dict:
    """A person on our 52 joints in an A-pose, facing +Z, the left side at +X (metres)."""
    p = {
        "Hips": (0, 1.0, 0),
        "Spine": (0, 1.1, 0),
        "Spine1": (0, 1.25, 0),
        "Spine2": (0, 1.4, 0),
        "Neck": (0, 1.55, 0),
        "Head": (0, 1.65, 0.02),
    }
    for sx, s in ((1, "Left"), (-1, "Right")):
        d = np.array([sx * math.cos(math.radians(45)), -math.sin(math.radians(45)), 0])
        arm = np.array([sx * 0.18, 1.5, 0])
        hand = arm + 0.53 * d
        p |= {
            f"{s}Shoulder": (sx * 0.05, 1.5, 0),
            f"{s}Arm": arm,
            f"{s}ForeArm": arm + 0.28 * d,
            f"{s}Hand": hand,
        }
        for f, z, k in (("Thumb", 0.04, 0.03), ("Index", 0.03, 0.09), ("Middle", 0, 0.095)) + (
            ("Ring", -0.02, 0.09),
            ("Pinky", -0.04, 0.08),
        ):
            for i in (1, 2, 3):
                p[f"{s}Hand{f}{i}"] = hand + (k + 0.03 * (i - 1)) * d + [0, 0, z]
        p |= {
            f"{s}UpLeg": (sx * 0.1, 0.95, 0),
            f"{s}Leg": (sx * 0.1, 0.5, 0),
            f"{s}Foot": (sx * 0.1, 0.08, 0),
            f"{s}ToeBase": (sx * 0.1, 0.02, 0.12),
        }
    return {n: np.asarray(v, float) for n, v in p.items()}


def soma_rest(yaw_deg=5.0, scale=1.1) -> dict:
    """A SOMA-like rest in the standard T-pose: our proportions times `scale`, the arms out level, turned by
    `yaw_deg` (the retarget's turn T has something to do), the hips 1.1 m up."""
    ours = our_rest()
    p = {
        "Hips": ours["Hips"],
        "Spine1": ours["Spine"],
        "Spine2": ours["Spine1"],
        "Chest": ours["Spine2"],
        "Neck1": ours["Neck"],
        "Neck2": (ours["Neck"] + ours["Head"]) / 2,
        "Head": ours["Head"],
        "Jaw": ours["Head"] + [0, -0.05, 0.08],
        "LeftEye": ours["Head"] + [0.03, 0.03, 0.08],
        "RightEye": ours["Head"] + [-0.03, 0.03, 0.08],
    }
    for sx, s in ((1, "Left"), (-1, "Right")):
        arm = np.array([sx * 0.18, 1.5, 0])
        hand = arm + [sx * 0.53, 0, 0]
        p |= {
            f"{s}Shoulder": np.array([sx * 0.05, 1.5, 0]),
            f"{s}Arm": arm,
            f"{s}ForeArm": arm + [sx * 0.28, 0, 0],
            f"{s}Hand": hand,
            f"{s}HandThumb1": hand + [sx * 0.03, -0.01, 0.04],
            f"{s}HandMiddle2": hand + [sx * 0.095, 0, 0],
            f"{s}HandPinky2": hand + [sx * 0.08, 0, -0.04],
            f"{s}HandThumbEnd": hand + [sx * 0.06, -0.01, 0.07],
            f"{s}HandMiddleEnd": hand + [sx * 0.19, 0, 0],
            f"{s}Leg": ours[f"{s}UpLeg"],
            f"{s}Shin": ours[f"{s}Leg"],
            f"{s}Foot": ours[f"{s}Foot"],
            f"{s}ToeBase": ours[f"{s}ToeBase"],
        }
    R = _connect.yrot(math.radians(yaw_deg))
    return {n: R @ (np.asarray(v, float) * scale) for n, v in p.items()}


ARM = Rotation.from_euler(
    "x", -90, degrees=True
).as_matrix()  # an armature node turned, as exporters do


def glb(clips: dict, prefix="", yaw=0.0) -> tuple[dict, bytes]:
    """Our rig as a GLB document: an Armature node (turned by ARM) over the 52 joints at rest (identity local
    turns), and `clips`: name → [(joint, path, times, values in world terms: hips position or joint turn)]."""
    rest = our_rest()
    order = [n for n, _ in _rig.template()]
    parent = dict(_rig.template())
    # `yaw` turns the whole rig about +Y (an imported rig whose rest does not face +Z)
    top = _connect.yrot(math.radians(yaw)) @ ARM
    nodes = [{"name": "Armature", "rotation": Rotation.from_matrix(top).as_quat().tolist()}]
    index = {n: k + 1 for k, n in enumerate(order)}
    for n in order:
        base = rest[parent[n]] if parent[n] else np.zeros(3)
        nodes.append({"name": prefix + n, "translation": (ARM.T @ (rest[n] - base)).tolist()})
    nodes[0]["children"] = [index["Hips"]]
    for n in order:
        kids = [index[c] for c in order if parent[c] == n]
        if kids:
            nodes[index[n]]["children"] = kids
    blob, views, accessors, anims = bytearray(), [], [], []

    def add(arr, typ):
        arr = np.asarray(arr, "<f4")
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": arr.nbytes})
        blob.extend(arr.tobytes())
        accessors.append(
            {"bufferView": len(views) - 1, "componentType": 5126, "count": len(arr), "type": typ}
        )
        return len(accessors) - 1

    for name, chans in clips.items():
        samplers, channels = [], []
        for joint, path, t, v in chans:
            if path == "translation":  # the hips' world position → its local translation under ARM
                v = [ARM.T @ np.asarray(x, float) for x in v]
            else:  # a world turn of the joint (its parents still at rest) → its local turn
                v = [Rotation.from_matrix(ARM.T @ np.asarray(m) @ ARM).as_quat() for m in v]
            samplers.append(
                {
                    "input": add(np.asarray(t)[:, None], "SCALAR"),
                    "output": add(v, "VEC3" if path == "translation" else "VEC4"),
                }
            )
            channels.append(
                {"sampler": len(samplers) - 1, "target": {"node": index[joint], "path": path}}
            )
        anims.append({"name": name, "samplers": samplers, "channels": channels})
    doc = {
        "asset": {"version": "2.0"},
        "scenes": [{"nodes": [0]}],
        "nodes": nodes,
        "skins": [{"joints": [index[n] for n in order]}],
        "animations": anims,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    return doc, bytes(blob)


def test_the_tables_are_the_retargets():
    """steps/ may not import blender_ops (the GPL boundary): the name map and the retarget's MAIN and KEEP are
    copied, and must stay equal."""
    spec = importlib.util.spec_from_file_location("_maps", ROOT / "blender_ops" / "_maps.py")
    maps = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(maps)
    assert _connect.SOMA == maps.SOMA
    tree = ast.parse((ROOT / "blender_ops" / "retarget.py").read_text(encoding="utf-8"))
    found = {
        t.id: ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name) and t.id in ("MAIN", "KEEP")
    }
    assert found == {"MAIN": _connect.MAIN, "KEEP": _connect.KEEP}
    assert set(_connect.RIDES) | {_connect.SOMA[b] for b in _connect.SOMA} >= set(NAMES30)


def test_rig_and_pose_read_the_node_tree_with_its_armature_and_a_prefix():
    turn = Rotation.from_euler("z", 90, degrees=True).as_matrix()  # the left arm raised to the side
    hips = [our_rest()["Hips"], our_rest()["Hips"] + [0, 0, 1.0]]
    doc, blob = glb(
        {
            "Wave": [
                ("LeftArm", "rotation", [0, 1], [np.eye(3), turn]),
                ("Hips", "translation", [0, 1], hips),
            ]
        },
        prefix="mixamorig:",
    )
    r = _connect.rig(doc)
    rest = our_rest()
    assert np.allclose(r["pos"]["LeftHand"], rest["LeftHand"]) and np.allclose(
        r["rot"]["Head"], ARM
    )
    assert r["parent"]["Hips"] is None and r["parent"]["LeftHand"] == "LeftForeArm"
    assert _connect.clip_seconds(doc, blob, "Wave") == 1.0
    P, W = _connect.pose(doc, blob, r, "Wave", [0.0, 0.5, 1.0])
    assert np.allclose(P["Hips"][1], rest["Hips"] + [0, 0, 0.5])  # halfway
    arm = rest["LeftForeArm"] - rest["LeftArm"]
    assert np.allclose(P["LeftForeArm"][2] - P["LeftArm"][2], turn @ arm, atol=1e-5)
    assert np.allclose(W["LeftArm"][2], turn @ ARM, atol=1e-5)
    with pytest.raises(StepError) as e:
        _connect.rig({"nodes": [{"name": "Hips"}], "skins": [{"joints": [0]}]})
    assert e.value.code == "bad_params" and "52-joint" in str(e.value)


def test_the_inverse_retarget_gives_back_the_soma_pose_the_retargets_maths_made_ours_from():
    """A random SOMA pose (the joints our skeleton lacks riding on their bones, as the inverse assumes), put on
    our rig by the retarget's own formula (our world turn = T·G·Tᵀ · align · our rest turn, the hips by travel ×
    scale), comes back as that pose: every joint's position and turn."""
    doc, _ = glb({})
    r = _connect.rig(doc)
    rest = soma_rest()
    al, T, scale = _connect.aligns(r, {}, rest)
    assert not np.allclose(T, np.eye(3)) and not math.isclose(scale, 1.0)
    G = {n: Rotation.random(random_state=k).as_matrix() for k, n in enumerate(NAMES30)}
    for s, b in _connect.RIDES.items():
        G[s] = G[_connect.SOMA[b]]
    pos = {"Hips": rest["Hips"] + [0.3, -0.2, 0.5]}
    for n, p in SOMA30[1:]:
        pos[n] = pos[p] + G[p] @ (rest[n] - rest[p])
    W = {
        b: (T @ G[s] @ T.T @ al.get(b, np.eye(3)) @ r["rot"][b])[None]
        for b, s in _connect.SOMA.items()
        if s in G
    }
    P = {"Hips": (r["pos"]["Hips"] + scale * T @ (pos["Hips"] - rest["Hips"]))[None]}
    got, turns = _connect.soma_pose(P, W, r, al, T, NAMES30, PARENTS30, rest, scale)
    for k, n in enumerate(NAMES30):
        assert np.allclose(got[0, k], pos[n], atol=1e-9), n
        assert np.allclose(turns[0, k], G[n], atol=1e-9), n


def test_our_a_pose_lands_on_soma_with_our_arm_directions_and_somas_own_torso():
    doc, _ = glb({})
    r = _connect.rig(doc)
    rest = soma_rest()
    al, T, scale = _connect.aligns(r, {}, rest)
    P = {"Hips": r["pos"]["Hips"][None]}
    W = {n: m[None] for n, m in r["rot"].items()}
    got, _ = _connect.soma_pose(P, W, r, al, T, NAMES30, PARENTS30, rest, scale)
    at = {n: got[0, k] for k, n in enumerate(NAMES30)}
    ours = our_rest()
    unit = lambda v: v / np.linalg.norm(v)
    for s in (
        "Left",
        "Right",
    ):  # the limbs: aligned, so they point the way ours do (in Kimodo's frame: Tᵀ)
        assert np.allclose(
            unit(at[f"{s}ForeArm"] - at[f"{s}Arm"]),
            T.T @ unit(ours[f"{s}ForeArm"] - ours[f"{s}Arm"]),
        )
    # the torso keeps SOMA's own posture (the retarget's KEEP), and the hips stay at SOMA's rest
    assert np.allclose(unit(at["Chest"] - at["Spine2"]), unit(rest["Chest"] - rest["Spine2"]))
    assert np.allclose(at["Hips"], rest["Hips"])


def test_place_faces_the_seam_forward_over_the_origin_and_landing_reads_the_speeds():
    P = {"Hips": np.array([[2.0, 1.0, 3.0]]), "LeftUpLeg": np.array([[2.0, 1.0, 2.9]])}
    P["RightUpLeg"] = np.array([[2.0, 1.0, 3.1]])  # the right hip at +Z: facing +X
    assert math.isclose(_connect.heading(P, 0), math.pi / 2)
    Q, _ = _connect.place(P, {"Hips": np.eye(3)[None]}, 0)
    assert np.allclose(Q["Hips"][0], [0, 1.0, 0]) and math.isclose(
        _connect.heading(Q, 0), 0, abs_tol=1e-12
    )
    Q, _ = _connect.place(P, {"Hips": np.eye(3)[None]}, 0, turn=math.pi / 2, at=(0, 0, 2))
    assert math.isclose(_connect.heading(Q, 0), math.pi / 2) and np.allclose(
        Q["Hips"][0], [0, 1.0, 2]
    )
    assert np.allclose(_connect.landing([0, 0, 1.4], [0, 0, 1.4], 1.0, None), [0, 0, 1.4])
    assert np.allclose(
        _connect.landing([0, 0, 0], [0, 0, 1.4], 2.0, None), [0, 0, 1.4]
    )  # from a stand
    assert np.allclose(_connect.landing([0, 0, 1.4], [0, 0, 1.4], 1.0, 0.3), [0, 0, 0.3])


def test_plan_pins_the_seams_from_a_standing_clip_into_a_walk(tmp_path):
    rest = our_rest()
    walk = [rest["Hips"] + [0, 0, 1.4 * t] for t in (0, 1)]
    doc, blob = glb(
        {
            "Idle": [("Hips", "translation", [0, 2], [rest["Hips"]] * 2)],
            "Walk": [("Hips", "translation", [0, 1], walk)],
        }
    )
    (tmp_path / "rigged_anim.glb").write_bytes(_anim.pack(doc, blob))
    sr = soma_rest()
    req = {"from": "Idle", "to": "Walk", "seconds": 1.0, "travel": None, "turn": 0.0}
    pl = _connect.plan(tmp_path, req, sr, NAMES30, PARENTS30)
    N = 31 + 2 * (_connect.SEAM - 1)
    assert pl["frames"] == N == _connect.frames_for(1.0, True, True)
    assert (
        pl["index"] == [*range(5), *range(N - 5, N)]
        and pl["keep"] == [4, N - 4]
        and pl["seams"] == [4, N - 5]
    )
    assert pl["pos"].shape == (10, 30, 3) and pl["rot"].shape == (10, 30, 3, 3)
    T, scale = _connect.aligns(_connect.rig(doc), {}, sr)[1:]
    hips = pl["pos"][:, 0] - sr["Hips"]  # SOMA's hips travel, in Kimodo's frame
    assert np.allclose(hips[:5], 0, atol=1e-9)  # Idle ends standing over the origin
    # Walk starts where a stand-to-walk gets in 1 s: half its 1.4 m/s, straight on (+Z), over the height ratio
    assert np.allclose(T @ hips[5], [0, 0, 0.7 / scale], atol=1e-6)
    assert np.allclose(T @ (hips[9] - hips[5]), [0, 0, 1.4 * 4 / 30 / scale], atol=1e-6)
    only = _connect.plan(tmp_path, {**req, "to": None}, sr, NAMES30, PARENTS30)
    assert (
        only["index"] == list(range(5))
        and only["keep"] == [4, only["frames"]]
        and only["seams"] == [4]
    )
    with pytest.raises(StepError) as e:
        _connect.plan(tmp_path, {**req, "to": "Run"}, sr, NAMES30, PARENTS30)
    assert e.value.code == "bad_params" and "Run" in str(e.value)


def test_a_rig_whose_rest_faces_another_way_still_pins_kimodos_heading_0(tmp_path):
    """The seams are placed facing the rig's own rest, which the retarget's turn T takes to SOMA's: the pinned
    heading in Kimodo's frame is the same whichever way the rig was built (a SOMA-like rest faces 5° here)."""
    sr = soma_rest()
    req = {"from": "Idle", "to": "Walk", "seconds": 1.0, "travel": 2.0, "turn": 0.0}
    li, ri = NAMES30.index("LeftLeg"), NAMES30.index("RightLeg")
    seen = []
    for yaw in (0.0, 90.0, 180.0):
        d = tmp_path / str(int(yaw))
        d.mkdir()
        _two_clips(d, yaw)
        pl = _connect.plan(d, req, sr, NAMES30, PARENTS30)
        hip = pl["pos"][4, ri] - pl["pos"][4, li]
        seen.append((math.degrees(math.atan2(hip[2], -hip[0])), pl["pos"][5, 0] - pl["pos"][4, 0]))
    for h, step in seen:
        assert math.isclose(h, 5.0, abs_tol=1e-6)  # SOMA's own rest heading, as for a rig facing +Z
        assert np.allclose(step, seen[0][1], atol=1e-9)  # and B lands the same 2 m straight on


def test_seam_check_measures_our_joints_at_the_clip_ends(tmp_path):
    rest = our_rest()
    up = Rotation.from_euler("z", 90, degrees=True).as_matrix()
    doc, blob = glb(
        {
            "Idle": [("Hips", "translation", [0, 1], [rest["Hips"]] * 2)],
            "Raise": [
                ("LeftArm", "rotation", [0, 1], [np.eye(3), up])
            ],  # starts at rest, ends with the arm up
        }
    )
    path = tmp_path / "rigged_anim.glb"
    path.write_bytes(_anim.pack(doc, blob))
    out = _connect.seam_check(path, "Raise", "Idle", "Idle")
    assert (
        out["from_cm"] == 0.0 and out["to_cm"] > 5
    )  # Raise's first frame is Idle's pose; its last is not
    assert _connect.seam_check(path, "Raise", None, "Idle") == {"to_cm": out["to_cm"]}


def test_finish_cuts_to_the_seams_starts_over_the_origin_and_measures_them():
    names77 = ["Hips", "LeftLeg", "RightLeg"]
    pl = {
        "names": ["Hips"],
        "index": [0, 1, 7, 8],
        "pos": np.zeros((4, 1, 3)) + [0, 1, 0],
        "keep": [1, 8],
        "seams": [1, 7],
    }
    F = 9
    posed = np.zeros((F, 3, 3))
    posed[:, 0] = [[0.5, 1.0, 0.1 * f] for f in range(F)]  # the hips walk along +Z from x = 0.5
    posed[:, 1] = posed[:, 0] + [
        0,
        0,
        -0.1,
    ]  # the right hip at +Z: facing +X, stepping to its right
    posed[:, 2] = posed[:, 0] + [0, 0, 0.1]
    rots = np.tile(np.eye(3), (F, 3, 1, 1))
    out, turns, err = _connect.finish(posed, rots, pl, names77)
    assert len(out) == 7 and err == [
        round(math.hypot(0.5, 0.1) * 100, 1),
        round(math.hypot(0.5, 0.7) * 100, 1),
    ]
    assert np.allclose(out[0, 0], [0, 1.0, 0])
    d = out[0, 2] - out[0, 1]
    assert math.isclose(math.atan2(d[2], -d[0]), 0, abs_tol=1e-12)  # facing +Z
    assert np.allclose(
        out[1, 0] - out[0, 0], [-0.1, 0, 0]
    )  # facing +Z now, still stepping to its right (-X)
    assert np.allclose(turns[0, 0], _connect.yrot(-math.pi / 2))


# --- the motion step --------------------------------------------------------------------------------------


def _two_clips(folder: Path, yaw=0.0) -> None:
    rest = our_rest()
    hips = [rest["Hips"] + [0, 0, 1.4 * t] for t in (0, 1)]
    doc, blob = glb(
        {
            "Idle": [("Hips", "translation", [0, 2], [rest["Hips"]] * 2)],
            "Walk": [("Hips", "translation", [0, 1], hips)],
        },
        yaw=yaw,
    )
    (folder / "rigged_anim.glb").write_bytes(_anim.pack(doc, blob))


def test_connect_params_and_names(tmp_path):
    assert motion.connect_params({"prompt": "A person waves."}, None) is None
    _two_clips(tmp_path)
    c = motion.connect_params(
        {"from": "Idle", "to": "Walk", "turn": -90, "travel": 1.5}, str(tmp_path)
    )
    assert c == {"from": "Idle", "to": "Walk", "seconds": 1.5, "travel": 1.5, "turn": -90.0}
    assert motion.connect_name(c) == "Idle to Walk"
    assert (
        motion.connect_name({**c, "to": None}) == "After Idle"
        and motion.connect_name({**c, "from": None}) == "Into Walk"
    )
    for bad in ({"seconds": 9}, {"seconds": 0.2}, {"turn": 200}, {"travel": 50}, {"seconds": "x"}):
        with pytest.raises(StepError) as e:
            motion.connect_params({"from": "Idle", **bad}, str(tmp_path))
        assert e.value.code == "bad_params", bad
    with pytest.raises(StepError) as e:
        motion.connect_params({"from": "Idle"}, str(tmp_path / "nothing"))
    assert e.value.code == "missing_input"
    with pytest.raises(StepError) as e:  # a clip the version lacks fails before the model loads
        motion.connect_params({"from": "Idle", "to": "Run"}, str(tmp_path))
    assert e.value.code == "bad_params" and "Run" in str(e.value)


def test_the_motion_step_connects_with_a_default_prompt_and_names_the_transition(
    tmp_path, monkeypatch
):
    src = tmp_path / "040_anim"
    src.mkdir()
    _two_clips(src)
    out = tmp_path / "050_motion"
    out.mkdir()
    seen = {}

    def fake_generate(prompt, seconds, seed, steps, out, params=None, connect=None):
        seen.update(prompt=prompt, seconds=seconds, connect=connect)
        (out / "motion.bvh").write_text("HIERARCHY\n")
        return out / "motion.bvh", {"seam_cm": [1.2, 2.5], "height_scale": 1.02}

    def fake_blender(op, inputs, outdir, params, timeout=None):
        seen["name"] = params["name"]
        Path(outdir).mkdir(parents=True, exist_ok=True)
        (Path(outdir) / "clip.glb").write_bytes(b"glb")
        return {"bones": 78, "frames": 46, "fps": 30, "seconds": 1.5}

    monkeypatch.setattr(motion, "generate", fake_generate)
    monkeypatch.setattr(motion, "run_blender", fake_blender)
    res = motion.run(
        "generate", str(src), str(out), {"from": "Idle", "to": "Walk", "for": "030_rig_skin"}
    )
    assert (
        seen["prompt"].startswith("A person moves")
        and seen["seconds"] == 1.5
        and seen["name"] == "Idle to Walk"
    )
    assert seen["connect"] == (
        src,
        {"from": "Idle", "to": "Walk", "seconds": 1.5, "travel": None, "turn": 0.0},
    )
    m = res["metrics"]
    assert (m["name"], m["from"], m["to"], m["seam_cm"], m["height_scale"]) == (
        "Idle to Walk",
        "Idle",
        "Walk",
        [1.2, 2.5],
        1.02,
    )
    assert json.loads((out / "prompt.json").read_text())["name"] == "Idle to Walk"
