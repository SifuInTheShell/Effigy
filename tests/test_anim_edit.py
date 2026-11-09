"""M37 anim_edit: the evaluation (steps/_anim.py), the GLB bake and the step, on a small synthetic rig."""

import json
import os
import struct
from pathlib import Path

import numpy as np
import pytest

from steps import _anim, anim_edit
from steps._common import StepError

FIXTURE = Path(__file__).parent / "fixtures" / "anim_edit" / "case.json"
FPS = 24


OFFSET = {
    "Spine": [0, 0.4, 0],
    "Arm": [0.3, 0, 0],
}  # the constant translations of the bones that don't move


def _rig(F, seed):
    """Dense rotations (random walk, every other sign flipped) and translations for 4 bones."""
    r = np.random.default_rng(seed)
    bones = [("Root", None), ("Hips", "Root"), ("Spine", "Hips"), ("Arm", "Spine")]
    out = []
    for i, (n, p) in enumerate(bones):
        b = {"name": n, "parent": p}
        if n != "Root":
            ang = np.cumsum(r.normal(0, 0.05, (F, 3)), 0)
            q = np.concatenate(
                [np.sin(ang / 2), np.cos(np.linalg.norm(ang, axis=1) / 2)[:, None]], 1
            )
            q /= np.linalg.norm(q, axis=1, keepdims=True)
            b["rotation"] = q
        if n in ("Root", "Hips"):
            b["translation"] = np.cumsum(r.normal(0, 0.02, (F, 3)), 0) + [0.5 * i, 1.0, 0.2]
        elif n in OFFSET:
            b["offset"] = np.array(OFFSET[n])
        out.append(b)
    return {"fps": FPS, "frames": F, "bones": out}


def _build_glb(path, clips, spine_moves=False):
    """GLB with a node per bone (Root > Hips > Spine > Arm), one animation per clip: LINEAR rotation and
    translation keys on every frame, Root's rotation and everything's scale 2-key STEP, like anim's output;
    `spine_moves`: the Spine's translation two LINEAR keys that move (a root travelling from A to B)."""
    acc, bvs, blob = [], [], bytearray()

    def add(arr, typ, minmax=False):
        a = np.ascontiguousarray(arr, "<f4")
        bvs.append({"buffer": 0, "byteOffset": len(blob), "byteLength": a.nbytes})
        blob.extend(a.tobytes())
        d = {"bufferView": len(bvs) - 1, "componentType": 5126, "count": len(a), "type": typ}
        if minmax:
            d["min"], d["max"] = [float(a.min())], [float(a.max())]
        acc.append(d)
        return len(acc) - 1

    names = ["Root", "Hips", "Spine", "Arm"]
    nodes = [{"name": n, "children": [i + 1] if i < 3 else []} for i, n in enumerate(names)]
    nodes[0]["translation"] = [0, 0, 0]
    anims = []
    for cname, F, seed in clips:
        base = _rig(F, seed)
        t_dense = add(np.arange(F) / FPS, "SCALAR", True)
        t_step = add([0, (F - 1) / FPS], "SCALAR", True)
        samplers, channels = [], []

        def chan(node, path, s, channels=channels, samplers=samplers):
            channels.append({"sampler": len(samplers), "target": {"node": node, "path": path}})
            samplers.append(s)

        for i, b in enumerate(base["bones"]):
            if "rotation" in b:
                chan(
                    i,
                    "rotation",
                    {
                        "input": t_dense,
                        "output": add(b["rotation"], "VEC4"),
                        "interpolation": "LINEAR",
                    },
                )
            else:
                chan(
                    i,
                    "rotation",
                    {
                        "input": t_step,
                        "output": add([[0, 0, 0, 1]] * 2, "VEC4"),
                        "interpolation": "STEP",
                    },
                )
            if "translation" in b:
                chan(
                    i,
                    "translation",
                    {
                        "input": t_dense,
                        "output": add(b["translation"], "VEC3"),
                        "interpolation": "LINEAR",
                    },
                )
            else:
                two = (
                    [[0, 0.1, 0], [0, 0.3, 0]]
                    if spine_moves and b["name"] == "Spine"
                    else [[0, 0.1, 0]] * 2
                )
                chan(
                    i,
                    "translation",
                    {
                        "input": t_step,
                        "output": add(two, "VEC3"),
                        "interpolation": "LINEAR"
                        if spine_moves and b["name"] == "Spine"
                        else "STEP",
                    },
                )
            chan(
                i,
                "scale",
                {"input": t_step, "output": add([[1, 1, 1]] * 2, "VEC3"), "interpolation": "STEP"},
            )
        anims.append({"name": cname, "samplers": samplers, "channels": channels})
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": bvs,
        "accessors": acc,
        "nodes": nodes,
        "scenes": [{"nodes": [0]}],
        "animations": anims,
    }
    j = json.dumps(doc).encode()
    j += b" " * (-len(j) % 4)
    blob.extend(b"\0" * (-len(blob) % 4))
    total = 12 + 8 + len(j) + 8 + len(blob)
    Path(path).write_bytes(
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<II", len(j), 0x4E4F534A)
        + j
        + struct.pack("<II", len(blob), 0x004E4942)
        + bytes(blob)
    )


def _bone(res, name):
    return next(b for b in res["bones"] if b["name"] == name)


def _same(a, b, tol=1e-6):
    assert a["frames"] == b["frames"]
    for x, y in zip(a["bones"], b["bones"], strict=True):
        assert x["name"] == y["name"]
        for k in ("rotation", "translation", "offset"):
            assert (k in x) == (k in y)
            if k in x:
                np.testing.assert_allclose(x[k], y[k], atol=tol)


Q90 = [0.0, np.sin(np.pi / 4), 0.0, np.cos(np.pi / 4)]  # 90 degrees about Y


@pytest.fixture
def base():
    return _rig(30, 1)


def test_identity_and_unit_quaternions(base):
    _same(_anim.evaluate(base, {}), base, 1e-12)
    res = _anim.evaluate(base, {"smooth": {"Spine": 2}, "speed": 0.5})
    for b in res["bones"]:
        if "rotation" in b:
            np.testing.assert_allclose(np.linalg.norm(b["rotation"], axis=1), 1, atol=1e-12)


def test_key_falloff_and_two_keys(base):
    res = _anim.evaluate(base, {"keys": {"Spine": [{"f": 10, "q": Q90, "falloff": 4}]}})
    got, was = _bone(res, "Spine")["rotation"], _bone(base, "Spine")["rotation"]
    np.testing.assert_allclose(got[10], _anim.qmul(was[10], Q90), atol=1e-9)
    np.testing.assert_allclose(got[5], was[5], atol=1e-12)  # beyond the falloff
    np.testing.assert_allclose(got[16], was[16], atol=1e-12)
    # half way down the falloff: smoothstep(0.5) = 0.5 of the key, a 45 degree turn about Y
    half = _anim.qmul(was[8], _anim.slerp([0, 0, 0, 1.0], Q90, 0.5))
    np.testing.assert_allclose(got[8], half, atol=1e-9)
    res = _anim.evaluate(
        base,
        {
            "keys": {
                "Spine": [
                    {"f": 4, "q": [0, 0, 0, 1], "falloff": 0},
                    {"f": 12, "q": Q90, "falloff": 0},
                ]
            }
        },
    )
    got = _bone(res, "Spine")["rotation"]
    mid = _anim.slerp([0, 0, 0, 1.0], Q90, 0.5)  # f = 8: smoothstep(0.5)
    np.testing.assert_allclose(got[8], _anim.qmul(was[8], mid), atol=1e-9)
    np.testing.assert_allclose(got[2], was[2], atol=1e-12)  # falloff 0: only the key's own frame
    np.testing.assert_allclose(got[20], was[20], atol=1e-12)


def test_offsets_trim_speed_loop(base):
    pos = _bone(base, "Hips")["translation"]
    res = _anim.evaluate(base, {"offsets": {"Hips": [{"f": 3, "v": [0, 0.5, 0], "falloff": 2}]}})
    p = _bone(res, "Hips")["translation"]
    np.testing.assert_allclose(p[3], pos[3] + [0, 0.5, 0], atol=1e-12)
    np.testing.assert_allclose(p[0], pos[0], atol=1e-12)
    np.testing.assert_allclose(p[2], pos[2] + [0, 0.25, 0], atol=1e-12)
    res = _anim.evaluate(base, {"trim": [5, 14]})
    assert res["frames"] == 10
    np.testing.assert_allclose(_bone(res, "Arm")["rotation"], _bone(base, "Arm")["rotation"][5:15])
    res = _anim.evaluate(base, {"speed": 2})
    assert res["frames"] == 16  # floor(14.5 + .5) + 1
    q = _bone(base, "Arm")["rotation"]
    np.testing.assert_allclose(_bone(res, "Arm")["rotation"][3], q[6], atol=1e-12)
    res = _anim.evaluate(base, {"speed": 0.5})
    assert res["frames"] == 59
    np.testing.assert_allclose(_bone(res, "Arm")["rotation"][2], q[1], atol=1e-12)  # sample g*speed
    res = _anim.evaluate(base, {"loop": 6})
    for b in res["bones"]:
        if "rotation" in b:
            np.testing.assert_allclose(b["rotation"][-1], b["rotation"][0], atol=1e-9)
            np.testing.assert_allclose(b["rotation"][0], _bone(base, b["name"])["rotation"][0])
    # not in place: the root's X/Z stay (only Y closes), in place: X/Z closed too
    end = _bone(res, "Root")["translation"]
    assert abs(end[-1, 1] - end[0, 1]) < 1e-9 and abs(end[-1, 0] - end[0, 0]) > 1e-3


def test_in_place_and_ground(base):
    res = _anim.evaluate(base, {"in_place": True, "ground": 0.25})
    for n in ("Root",):  # the top-most translated bone only
        p = _bone(res, n)["translation"]
        np.testing.assert_allclose(p[-1, [0, 2]], p[0, [0, 2]], atol=1e-9)
        np.testing.assert_allclose(p[:, 1], _bone(base, n)["translation"][:, 1] + 0.25, atol=1e-12)
    np.testing.assert_allclose(
        _bone(res, "Hips")["translation"], _bone(base, "Hips")["translation"], atol=1e-12
    )


def test_smooth_reduces_jitter(base):
    r = np.random.default_rng(5)
    arm = _bone(base, "Arm")
    sign = np.where(np.arange(30) % 7 == 3, -1.0, 1.0)[:, None]  # q and -q: one rotation
    arm["rotation"] = _anim._unit(arm["rotation"] + r.normal(0, 0.03, (30, 4))) * sign
    res = _anim.evaluate(base, {"smooth": {"Arm": 2.0}})
    q = _bone(res, "Arm")["rotation"]
    q = _anim._consistent(q)
    jerk = lambda a: np.abs(np.diff(_anim._consistent(a), 2, axis=0)).mean()
    assert jerk(q) < 0.3 * jerk(arm["rotation"])
    assert np.abs(q).max() <= 1 + 1e-12 and abs(np.diff(q, axis=0)).max() < 0.2  # no sign pops


def test_fixture_parity():
    """The TypeScript evaluator reads the same file (ui/src/anim/evaluate.ts). EFFIGY_REGEN_FIXTURE=1 rewrites it."""
    if os.environ.get("EFFIGY_REGEN_FIXTURE"):
        _write_fixture()
    case = json.loads(FIXTURE.read_text(encoding="utf-8"))
    res = _anim.evaluate(case["base"], case["layer"])
    exp = case["expected"]
    assert res["frames"] == exp["frames"] and res["fps"] == exp["fps"]
    for g, e in zip(res["bones"], exp["bones"], strict=True):
        assert g["name"] == e["name"] and g["parent"] == e["parent"]
        for k in ("rotation", "translation", "offset"):
            assert (k in g) == (k in e)
            if k in g:
                np.testing.assert_allclose(g[k], e[k], atol=1e-9, rtol=0)


def _world(clip, f, names=("Hips", "Spine", "Arm")):
    """World positions and turns of bones by plain FK, independent of steps/_anim's solver."""
    by, W, p, out = {b["name"]: b for b in clip["bones"]}, np.array([0, 0, 0, 1.0]), np.zeros(3), {}
    for n in ("Root", *names):
        b = by[n]
        p = p + _anim._qrot(W, b["translation"][f] if "translation" in b else b["offset"])
        W = _anim.qmul(W, b["rotation"][f]) if "rotation" in b else W
        out[n] = (p, W)
    return out


LOCK = {"feet": [{"bone": "Arm", "from": 8, "to": 16, "blend": 0}]}


def test_foot_lock_pins_the_bone_and_keeps_lengths_and_turn():
    base = _rig(30, 1)
    _bone(base, "Hips")["translation"][:] = [0, 1, 0]  # a hip that stays: every target is in reach
    res = _anim.evaluate(base, LOCK)
    T = _world(base, 12)["Arm"][0]
    for f in range(30):
        was, now = _world(base, f), _world(res, f)
        if 8 <= f <= 16:
            np.testing.assert_allclose(now["Arm"][0], T, atol=1e-6)
        else:
            np.testing.assert_allclose(
                now["Arm"][0], was["Arm"][0], atol=1e-12
            )  # outside: untouched
        for a, b in (("Hips", "Spine"), ("Spine", "Arm")):
            np.testing.assert_allclose(
                np.linalg.norm(now[b][0] - now[a][0]),
                np.linalg.norm(was[b][0] - was[a][0]),
                atol=1e-9,
            )
        d = abs(now["Arm"][1] @ was["Arm"][1])  # B keeps its world turn (q, -q: one rotation)
        np.testing.assert_allclose(d, 1, atol=1e-9)
        for n in ("Hips", "Spine", "Arm"):
            np.testing.assert_allclose(np.linalg.norm(_bone(res, n)["rotation"][f]), 1, atol=1e-12)


def test_foot_lock_blend_weights_the_target():
    base = _rig(30, 1)
    _bone(base, "Hips")["translation"][:] = [0, 1, 0]
    res = _anim.evaluate(base, {"feet": [{"bone": "Arm", "from": 8, "to": 16, "blend": 4}]})
    T = _world(base, 12)["Arm"][0]
    for f, w in ((4, 0.0), (6, 0.5), (7, 0.84375), (18, 0.5), (20, 0.0)):  # s(1 - distance/blend)
        c = _world(base, f)["Arm"][0]
        np.testing.assert_allclose(_world(res, f)["Arm"][0], c + (T - c) * w, atol=1e-6)


def test_foot_lock_clamps_and_survives_a_straight_leg():
    base = _rig(30, 1)
    hips = _bone(base, "Hips")["translation"]
    hips[:] = [0, 1, 0]
    hips[13:, 0] = 5  # the hip walks away: the target is out of reach
    res = _anim.evaluate(base, LOCK)
    w = _world(res, 14)
    assert np.isfinite(_bone(res, "Arm")["rotation"]).all()
    np.testing.assert_allclose(np.linalg.norm(w["Arm"][0] - w["Hips"][0]), 0.7, atol=1e-5)
    for n in ("Hips", "Spine", "Arm"):  # a straight, unrotated leg: the knee axis is degenerate
        _bone(base, n)["rotation"][:] = [0, 0, 0, 1]
    _bone(base, "Arm")["offset"] = np.array([0, 0.3, 0])
    hips[:] = [0, 1, 0]
    hips[:, 0] = np.linspace(0, 0.2, 30)
    res = _anim.evaluate(base, LOCK)
    for n in ("Hips", "Spine", "Arm"):
        q = _bone(res, n)["rotation"]
        assert np.isfinite(q).all()
        np.testing.assert_allclose(np.linalg.norm(q, axis=1), 1, atol=1e-12)
    _bone(base, "Spine")["offset"] = np.array(
        [0, 5e-9, 0]
    )  # a (nearly) zero-long upper bone: no knee, frames left
    _bone(base, "Arm")["offset"] = np.array([0, 0.5, 0])
    res = _anim.evaluate(base, LOCK)
    for n in ("Hips", "Spine", "Arm"):
        assert np.isfinite(_bone(res, n)["rotation"]).all()


def test_foot_validation():
    chain = {
        "fps": FPS,
        "frames": 10,
        "bones": [
            {"name": n, "parent": p, "rotation": np.tile([0, 0, 0, 1.0], (10, 1))}
            for n, p in (("A", None), ("B", "A"), ("C", "B"), ("D", "C"))
        ],
    }
    ok = {"bone": "D", "from": 2, "to": 5, "blend": 2}
    assert anim_edit.validate("c", {"feet": [ok]}, chain)["feet"] == [ok]
    assert anim_edit.validate("c", {"feet": [{**ok, "from": 2.0}]}, chain)["feet"][0]["from"] == 2
    for bad in (
        {**ok, "bone": "Nope"},
        {**ok, "bone": "B"},  # no grandparent
        {**ok, "from": 6},
        {**ok, "to": 10},
        {**ok, "blend": -1},
        {**ok, "blend": 11},
        {**ok, "from": 2.5},
        {**ok, "blend": True},
        {**ok, "extra": 1},
        {"bone": "D", "from": 2, "to": 5},
    ):
        with pytest.raises(StepError) as e:
            anim_edit.validate("c", {"feet": [bad]}, chain)
        assert e.value.code == "bad_params"
    with pytest.raises(StepError):
        anim_edit.validate("c", {"feet": [ok] * 65}, chain)
    del chain["bones"][1]["rotation"]  # a parent that doesn't rotate
    with pytest.raises(StepError):
        anim_edit.validate("c", {"feet": [ok]}, chain)


def _limb(F=20, seed=3):
    """Root (moving) > A > B > C > D, each turning a little (a random walk about a bend), 0.3 m apart: for the IK."""
    r = np.random.default_rng(seed)
    root = np.tile([0, 1.0, 0], (F, 1)) + np.linspace(0, 0.2, F)[:, None] * [1, 0, 0]
    bones = [{"name": "Root", "parent": None, "translation": root}]
    for n, p in zip("ABCD", ["Root", *"ABC"], strict=True):
        ang = np.cumsum(r.normal(0, 0.03, (F, 3)), 0) + [0, 0, 0.4]
        q = np.concatenate([np.sin(ang / 2), np.cos(np.linalg.norm(ang, axis=1) / 2)[:, None]], 1)
        bones.append(
            {"name": n, "parent": p, "rotation": _anim._unit(q), "offset": np.array([0.3, 0, 0])}
        )
    return {"fps": FPS, "frames": F, "bones": bones}


def _at(clip, f, name):
    """A bone's world position and turn by plain FK."""
    by, path = {b["name"]: b for b in clip["bones"]}, []
    while name:
        path.insert(0, name)
        name = by[name]["parent"]
    W, p = np.array([0, 0, 0, 1.0]), np.zeros(3)
    for n in path:
        b = by[n]
        p = p + _anim._qrot(W, b["translation"][f] if "translation" in b else b["offset"])
        W = _anim.qmul(W, b["rotation"][f]) if "rotation" in b else W
    return p, W


def _ctl(chain, keys, **kw):  # evaluate takes a cleaned layer: the keys' defaults filled in
    keys = [{"swivel": 0, "w": 1, "falloff": 0, **k} for k in keys]
    return {"ik": [{"bone": "D", "chain": chain, "keys": keys, **kw}]}


def test_ik_two_and_three_bones_reach_the_target_and_keep_the_hand():
    base = _limb()
    for chain, tol in ((2, 1e-6), (3, 1e-4)):
        T = _at(base, 5, "B")[0] + [0.2, 0.25, 0.1]  # within reach of both chains
        res = _anim.evaluate(base, _ctl(chain, [{"f": 5, "p": T.tolist(), "w": 1, "falloff": 0}]))
        np.testing.assert_allclose(_at(res, 5, "D")[0], T, atol=tol)
        assert abs(_at(res, 5, "D")[1] @ _at(base, 5, "D")[1]) == pytest.approx(1, abs=1e-9)
        for f in (4, 6):  # falloff 0: its own frame only
            np.testing.assert_allclose(_at(res, f, "D")[0], _at(base, f, "D")[0], atol=1e-12)
        same_a = np.allclose(_bone(res, "A")["rotation"][5], _bone(base, "A")["rotation"][5])
        assert same_a == (chain == 2)  # two bones: C and B turn; three: A too


def test_ik_weight_falloff_and_swivel():
    base = _limb()
    b, c = _at(base, 12, "B")[0], _at(base, 12, "D")[0]
    T = b + 0.85 * (c - b) + [0, 0.05, 0]  # in reach on every frame it weighs on
    res = _anim.evaluate(base, _ctl(2, [{"f": 10, "p": T.tolist(), "w": 1, "falloff": 4}]))
    for f, w in ((10, 1.0), (12, 0.5), (14, 0.0)):  # held, its weight s(1 - d/falloff)
        c = _at(base, f, "D")[0]
        np.testing.assert_allclose(_at(res, f, "D")[0], c + (T - c) * w, atol=1e-6)
    b, c = _at(base, 8, "B")[0], _at(base, 8, "D")[0]
    T = b + 0.7 * (c - b)
    res = _anim.evaluate(base, _ctl(2, [{"f": 8, "p": T.tolist(), "w": 0.5, "falloff": 0}]))
    np.testing.assert_allclose(_at(res, 8, "D")[0], c + (T - c) * 0.5, atol=1e-6)
    # swivel 90 on the bone's own spot: the hand stays, the elbow swings a quarter turn about shoulder-hand
    c = _at(base, 8, "D")[0]
    res = _anim.evaluate(base, _ctl(2, [{"f": 8, "p": c.tolist(), "swivel": 90, "w": 1}]))
    np.testing.assert_allclose(_at(res, 8, "D")[0], c, atol=1e-6)
    a, ax = _at(base, 8, "B")[0], _anim._unit(c - _at(base, 8, "B")[0])
    elbow = [_at(x, 8, "C")[0] - a for x in (base, res)]
    side = [v - (v @ ax) * ax for v in elbow]
    cos = side[0] @ side[1] / np.linalg.norm(side[0]) / np.linalg.norm(side[1])
    assert cos == pytest.approx(0, abs=1e-6) and np.cross(side[0], side[1]) @ ax > 0


def test_ik_in_a_bone_space_follows_that_bone():
    base = _limb()
    p = [0.3, 0.35, 0.1]  # in Root's frame; Root walks 20 cm along X and does not turn
    res = _anim.evaluate(
        base, _ctl(2, [{"f": 0, "p": p, "w": 1}, {"f": 19, "p": p, "w": 1}], space="Root")
    )
    for f in (0, 10, 19):
        np.testing.assert_allclose(_at(res, f, "D")[0], _at(base, f, "Root")[0] + p, atol=1e-6)


def test_ik_validation():
    base = _limb(10)
    ok = {"bone": "D", "chain": 2, "keys": [{"f": 2, "p": [0, 1, 0]}]}
    assert anim_edit.validate("c", {"ik": [ok]}, base)["ik"] == [
        {
            "bone": "D",
            "chain": 2,
            "keys": [{"f": 2, "p": [0, 1, 0], "swivel": 0, "w": 1, "falloff": 0}],
        }
    ]
    assert (
        anim_edit.validate("c", {"ik": [{**ok, "space": "Root", "chain": 3}]}, base)["ik"][0][
            "space"
        ]
        == "Root"
    )
    key = ok["keys"][0]
    for bad in (
        {**ok, "bone": "Nope"},
        {**ok, "chain": 4},  # Root does not turn
        {**ok, "chain": 1},
        {**ok, "chain": 2.0},
        {**ok, "space": "Nope"},
        {**ok, "space": []},
        {**ok, "keys": []},
        {**ok, "keys": [key, key]},
        {**ok, "keys": [{**key, "f": 10}]},
        {**ok, "keys": [{**key, "f": 2.5}]},
        {**ok, "keys": [{**key, "w": 1.5}]},
        {**ok, "keys": [{**key, "swivel": 181}]},
        {**ok, "keys": [{**key, "p": [0, 1]}]},
        {**ok, "keys": [{**key, "extra": 1}]},
        {**ok, "extra": 1},
        {"bone": "D", "keys": ok["keys"]},
    ):
        with pytest.raises(StepError) as e:
            anim_edit.validate("c", {"ik": [bad]}, base)
        assert e.value.code == "bad_params"
    for bad in ([ok] * 65, [ok, ok]):  # at most 64; one per bone
        with pytest.raises(StepError):
            anim_edit.validate("c", {"ik": bad}, base)


def test_sample_clip_gives_the_still_bones_an_offset(tmp_path):
    _, v = _library(tmp_path)
    clip = _read_clip(v / "rigged_anim.glb", "A")
    np.testing.assert_allclose(_bone(clip, "Spine")["offset"], [0, 0.1, 0])
    assert "offset" not in _bone(clip, "Hips")
    doc, blob = _anim.read_glb(v / "rigged_anim.glb")
    doc["animations"][0]["channels"] = [
        c
        for c in doc["animations"][0]["channels"]
        if c["target"] != {"node": 2, "path": "translation"}
    ]  # Spine's constant translation channel gone: its node's translation is the offset
    doc["nodes"][2]["translation"] = [1, 2, 3]
    spine = _bone(_anim.sample_clip(doc, blob, "A", FPS), "Spine")
    np.testing.assert_allclose(spine["offset"], [1, 2, 3])


def _write_fixture():
    base = _rig(30, 11)
    r = np.random.default_rng(12)
    for b in base["bones"]:
        if "rotation" in b:  # jitter, and a sign flip every 6th frame (q and -q)
            q = _anim._unit(b["rotation"] + r.normal(0, 0.02, (30, 4)))
            b["rotation"] = q * np.where(np.arange(30) % 6 == 5, -1.0, 1.0)[:, None]
    layer = {
        "smooth": {"Spine": 1.5, "Hips": 1.0},
        "keys": {
            "Spine": [
                {"f": 6, "q": [0.0, 0.2588190451, 0.0, 0.9659258263], "falloff": 3},
                {"f": 14, "q": [0.2588190451, 0.0, 0.0, 0.9659258263], "falloff": 5},
            ],
            "Arm": [{"f": 20, "q": [0.0, 0.0, 0.3826834324, 0.9238795325], "falloff": 0}],
        },
        "offsets": {
            "Hips": [
                {"f": 10, "v": [0.0, 0.1, -0.05], "falloff": 4},
                {"f": 18, "v": [0.02, 0.0, 0.0], "falloff": 2},
            ]
        },
        "trim": [2, 27],
        "in_place": True,
        "ground": 0.05,
        "loop": 5,
        "speed": 1.5,
        "feet": [{"bone": "Arm", "from": 8, "to": 16, "blend": 3}],
        "ik": [  # M63: a FABRIK chain of three, then a two-bone one whose target rides on the hips
            {
                "bone": "Tip",
                "chain": 3,
                "keys": [
                    {"f": 4, "p": [0.8, 2.5, 0.6], "swivel": 0, "w": 1, "falloff": 3},
                    {"f": 12, "p": [0.7, 2.7, 0.7], "swivel": 30, "w": 0.6, "falloff": 4},
                ],
            },
            {
                "bone": "Hand",
                "chain": 2,
                "space": "Hips",
                "keys": [{"f": 18, "p": [0.3, 0.6, 0.2], "swivel": -20, "w": 1, "falloff": 5}],
            },
        ],
    }
    r = np.random.default_rng(
        13
    )  # M63: a hand and a fingertip below the arm, for the chain of three
    for n, p, o in (("Hand", "Arm", [0.25, 0, 0]), ("Tip", "Hand", [0.15, 0, 0])):
        ang = np.cumsum(r.normal(0, 0.05, (30, 3)), 0)
        q = np.concatenate([np.sin(ang / 2), np.cos(np.linalg.norm(ang, axis=1) / 2)[:, None]], 1)
        base["bones"].append(
            {"name": n, "parent": p, "rotation": _anim._unit(q), "offset": np.array(o, float)}
        )
    case = {"base": base, "layer": layer, "expected": _anim.evaluate(base, layer)}
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(
        json.dumps(case, default=lambda a: a.tolist() if hasattr(a, "tolist") else a),
        encoding="utf-8",
    )


# --- the GLB bake and the step ----------------------------------------------------------------------------


def _library(tmp_path):
    asset = tmp_path / "asset"
    v = asset / "010_anim"
    v.mkdir(parents=True)
    _build_glb(v / "rigged_anim.glb", [("A", 25, 1), ("B", 13, 2)])
    (v / "clips.json").write_text(
        json.dumps(
            {
                "clips": [
                    {"name": "A", "frames": 24, "seconds": 1.0},
                    {"name": "B", "frames": 12, "seconds": 0.5},
                ],
                "fps": 24,
                "missing": [],
                "height_scale": 1,
            }
        )
    )
    (v / "skeleton.json").write_text("{}")
    (v / "meta.json").write_text(json.dumps({"step": "anim", "version": "010_anim"}))
    return asset, v


def _read_clip(glb, name):
    doc, blob = _anim.read_glb(glb)
    return _anim.sample_clip(doc, blob, name, FPS)


def _run(v, out, clips):
    out.mkdir()
    return anim_edit.run("run", str(v), str(out), {"clips": clips})


def test_identity_step_keeps_samples_and_other_clip(tmp_path):
    asset, v = _library(tmp_path)
    res = _run(v, asset / "020_anim_edit", {"A": {}})
    out = asset / "020_anim_edit"
    assert next(iter(res["outputs"])) == "rigged_anim" and res["metrics"] == {
        "clips": ["A"],
        "base": "010_anim",
    }
    for n in ("A", "B"):
        _same(_read_clip(out / "rigged_anim.glb", n), _read_clip(v / "rigged_anim.glb", n))
    d0, b0 = _anim.read_glb(v / "rigged_anim.glb")
    d1, b1 = _anim.read_glb(out / "rigged_anim.glb")
    assert (
        d1["animations"][1] == d0["animations"][1] and b1[: len(b0)] == b0
    )  # untouched clip, same accessors
    assert len(d1["accessors"]) > len(d0["accessors"])
    raw = (out / "rigged_anim.glb").read_bytes()
    assert struct.unpack_from("<I", raw, 8)[0] == len(raw) and len(raw) % 4 == 0
    assert json.loads((out / "edit.json").read_text())["base"] == "010_anim"
    clips = json.loads((out / "clips.json").read_text())
    assert clips["clips"][0]["frames"] == 24 and (out / "skeleton.json").is_file()


def test_edit_and_reedit_from_anim_edit(tmp_path):
    asset, v = _library(tmp_path)
    key = {"keys": {"Arm": [{"f": 8, "q": Q90, "falloff": 3}]}, "trim": [0, 11], "speed": 2}
    _run(v, asset / "020_anim_edit", {"A": key})
    e1 = asset / "020_anim_edit"
    (e1 / "meta.json").write_text(json.dumps({"step": "anim_edit"}))
    clip = _read_clip(e1 / "rigged_anim.glb", "A")
    assert clip["frames"] == 7  # 12 frames at speed 2: floor(5.5 + .5) + 1
    clips = json.loads((e1 / "clips.json").read_text())["clips"]
    assert clips[0]["frames"] == 6 and clips[0]["seconds"] == 0.25 and clips[1]["frames"] == 12
    # the scale channels are 2-key STEP over the new duration
    doc, _ = _anim.read_glb(e1 / "rigged_anim.glb")
    a = doc["animations"][0]
    sc = next(c for c in a["channels"] if c["target"]["path"] == "scale")
    s = a["samplers"][sc["sampler"]]
    assert s["interpolation"] == "STEP" and doc["accessors"][s["input"]]["max"] == [
        pytest.approx(0.25)
    ]
    # editing again starts from the base: A is edited anew, not stacked on the bake
    _run(e1, asset / "030_anim_edit", {"A": {"keys": {"Arm": [{"f": 8, "q": Q90, "falloff": 3}]}}})
    e2 = asset / "030_anim_edit"
    assert json.loads((e2 / "edit.json").read_text())["base"] == "010_anim"
    ref = _anim.evaluate(
        _read_clip(v / "rigged_anim.glb", "A"),
        {"keys": {"Arm": [{"f": 8, "q": Q90, "falloff": 3}]}},
    )
    _same(_read_clip(e2 / "rigged_anim.glb", "A"), ref)


@pytest.mark.parametrize(
    "clips",
    [
        {"Nope": {}},
        {"A": {"keys": {"Nobody": [{"f": 1, "q": [0, 0, 0, 1]}]}}},
        {"A": {"keys": {"Arm": [{"f": 1, "q": [0, 0, 0, float("nan")]}]}}},
        {"A": {"keys": {"Arm": [{"f": 99, "q": [0, 0, 0, 1]}]}}},
        {"A": {"keys": {"Arm": [{"f": 1, "q": [0, 0, 0, 1], "falloff": -1}]}}},
        {"A": {"offsets": {"Arm": [{"f": 1, "v": [0, 0, 0]}]}}},  # no animated translation
        {"A": {"trim": [10, 10]}},
        {"A": {"trim": [0, 99]}},
        {"A": {"speed": 20}},
        {"A": {"loop": 25}},
        {"A": {"smooth": {"Arm": 11}}},
        {"A": {"bogus": 1}},
        {},
        {"A": {"ground": float("inf")}},
        {"A": {"keys": {"Arm": [{"f": 3, "q": [float("nan"), 0, 0, 1]}]}}},
        {"A": {"offsets": {"Hips": [{"f": 3, "v": [0, float("inf"), 0]}]}}},
        {"A": {"keys": {"Arm": ["x"]}}},
        {"A": {"keys": ["Arm"]}},
        {"A": {"keys": {"Arm": [{"f": 2.5, "q": [0, 0, 0, 1]}]}}},
        {"A": {"trim": [0, 1], "speed": 10}},
        {"A": {"mirror": "yes"}},  # M69
        {"A": {"filter": "median"}},
        {"A": {"feet": [{"bone": "Arm", "from": 1, "to": 3, "blend": 1, "reach": 0.2}]}},
    ],
)
def test_bad_params(tmp_path, clips):
    _, v = _library(tmp_path)
    with pytest.raises(StepError) as e:
        _run(v, tmp_path / "o", clips)
    assert e.value.code == "bad_params"


def test_the_m69_fields_are_saved(tmp_path):
    _, v = _library(tmp_path)
    layer = {"filter": "butterworth", "smooth": {"Arm": 2}, "mirror": True}
    layer["feet"] = [{"bone": "Arm", "from": 1, "to": 3, "blend": 1, "reach": 0.97}]
    _run(v, tmp_path / "o", {"A": layer})
    saved = json.loads((tmp_path / "o" / "edit.json").read_text())["clips"]["A"]
    assert (
        saved["filter"] == "butterworth"
        and saved["mirror"] is True
        and saved["feet"][0]["reach"] == 0.97
    )


def test_quaternion_is_normalised_and_w_positive(tmp_path):
    _, v = _library(tmp_path)
    r = _run(v, tmp_path / "o", {"A": {"keys": {"Arm": [{"f": 3, "q": [0, -2, 0, -2]}]}}})
    k = json.loads((tmp_path / "o" / "edit.json").read_text())["clips"]["A"]["keys"]["Arm"][0]["q"]
    np.testing.assert_allclose(k, [0, np.sqrt(0.5), 0, np.sqrt(0.5)], atol=1e-12)
    assert r["outputs"]


def test_refuses_cubicspline_and_non_animation_version(tmp_path):
    _, v = _library(tmp_path)
    doc, blob = _anim.read_glb(v / "rigged_anim.glb")
    doc["animations"][0]["samplers"][0]["interpolation"] = "CUBICSPLINE"
    with pytest.raises(StepError, match="CUBICSPLINE"):
        _anim.sample_clip(doc, blob, "A", FPS)
    (v / "meta.json").write_text(json.dumps({"step": "rig_skin"}))
    with pytest.raises(StepError) as e:
        _run(v, tmp_path / "o", {"A": {}})
    assert e.value.code == "missing_input"


def test_a_two_key_move_and_a_held_turn_survive_the_bake(tmp_path):
    """A translation that moves in two keys is sampled and baked moving, not frozen; a whole-clip key on a bone
    that never turns (2-key STEP; two equal keys at the ends hold a turn) is written with its new value."""
    asset, v = _library(tmp_path)
    _build_glb(v / "rigged_anim.glb", [("A", 25, 1), ("B", 13, 2)], spine_moves=True)
    spine = _bone(_read_clip(v / "rigged_anim.glb", "A"), "Spine")
    np.testing.assert_allclose(spine["translation"][[0, -1], 1], [0.1, 0.3], atol=1e-6)
    _run(
        v,
        asset / "020_anim_edit",
        {"A": {"keys": {"Root": [{"f": 0, "q": Q90}, {"f": 24, "q": Q90}]}}},
    )
    out = _read_clip(asset / "020_anim_edit" / "rigged_anim.glb", "A")
    np.testing.assert_allclose(
        _bone(out, "Spine")["translation"][[0, 12, -1], 1], [0.1, 0.2, 0.3], atol=1e-6
    )
    np.testing.assert_allclose(
        np.abs(_bone(out, "Root")["rotation"][[0, -1]]), np.abs([Q90, Q90]), atol=1e-6
    )


def test_a_copy_is_a_new_animation_and_the_source_stays(tmp_path):
    asset, v = _library(tmp_path)
    key = {"keys": {"Arm": [{"f": 8, "q": Q90, "falloff": 3}]}, "trim": [0, 11], "speed": 2}
    _run(v, asset / "020_anim_edit", {"A": {}, "A copy": {**key, "from": "A"}})
    e1 = asset / "020_anim_edit"
    (e1 / "meta.json").write_text(json.dumps({"step": "anim_edit"}))
    glb, ref = e1 / "rigged_anim.glb", v / "rigged_anim.glb"
    doc, _ = _anim.read_glb(glb)
    assert [a["name"] for a in doc["animations"]] == ["A", "B", "A copy"]
    _same(_read_clip(glb, "A"), _read_clip(ref, "A"))
    _same(_read_clip(glb, "A copy"), _anim.evaluate(_read_clip(ref, "A"), key))
    clips = {c["name"]: c for c in json.loads((e1 / "clips.json").read_text())["clips"]}
    assert clips["A copy"]["frames"] == 6 and clips["A copy"]["seconds"] == 0.25
    assert clips["A"]["frames"] == 24
    assert json.loads((e1 / "edit.json").read_text())["clips"]["A copy"]["from"] == "A"
    # a re-edit that does not mention the copy drops it
    _run(e1, asset / "030_anim_edit", {"B": {}})
    doc, _ = _anim.read_glb(asset / "030_anim_edit" / "rigged_anim.glb")
    assert [a["name"] for a in doc["animations"]] == ["A", "B"]


@pytest.mark.parametrize(
    "clips",
    [
        {"C": {}},  # not a base clip, no from
        {"C": {"from": "Nope"}},
        {"C": {"from": ["A"]}},
        {"A": {"from": "B"}},  # a base clip is not a copy
        {"": {"from": "A"}},
        {" C": {"from": "A"}},
        {"x" * 65: {"from": "A"}},
        {"😀" * 65: {"from": "A"}},  # length in code points
        {"a/b": {"from": "A"}},
        {"a:b": {"from": "A"}},
        {"a\tb": {"from": "A"}},
        {"a[1]": {"from": "A"}},
        {"a": {"from": "A"}},  # a base clip's name ignoring case
        {"X": {"from": "A"}, "x": {"from": "B"}},  # two copies ignoring case
        {"C": {"from": "A"}, "D": {"from": "C"}},  # copy of a copy
        {f"C{i}": {"from": "A"} for i in range(65)},
    ],
)
def test_bad_copies(tmp_path, clips):
    _, v = _library(tmp_path)
    with pytest.raises(StepError) as e:
        _run(v, tmp_path / "o", clips)
    assert e.value.code == "bad_params"


def test_a_clip_that_never_moves_is_found(tmp_path):
    """The silent-clip gate: the fixture's clips turn their bones tens of degrees; the same Walk with every rotation
    held at its first key turns none (what a missed bone map writes)."""
    import numpy as np

    from steps import _anim

    src = Path(__file__).parent / "fixtures" / "anim.glb"
    turned = _anim.turns(src)
    assert min(turned.values()) > 5, turned
    doc, blob = _anim.read_glb(src)
    blob = bytearray(blob)
    walk = next(a for a in doc["animations"] if a["name"] == "Walk")
    for ch in walk["channels"]:
        if ch["target"]["path"] != "rotation":
            continue
        acc = doc["accessors"][walk["samplers"][ch["sampler"]]["output"]]
        bv = doc["bufferViews"][acc["bufferView"]]
        off = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
        q = np.frombuffer(bytes(blob[off : off + acc["count"] * 16]), "<f4").reshape(-1, 4)
        blob[off : off + acc["count"] * 16] = (
            np.tile(q[0], (acc["count"], 1)).astype("<f4").tobytes()
        )
    frozen = tmp_path / "frozen.glb"
    frozen.write_bytes(_anim.pack(doc, bytes(blob)))
    assert _anim.turns(frozen)["Walk"] == 0 and _anim.turns(frozen)["Idle"] > 5


# --- M69: mirror, Butterworth, reach ----------------------------------------------------------------------

FIXTURE2 = FIXTURE.with_name("case2.json")
X = np.array([1.0, 0, 0])


def _turned(axis, deg):
    a = np.radians(deg) / 2
    return np.r_[np.sin(a) * np.asarray(axis, float) / np.linalg.norm(axis), np.cos(a)]


def _sym(F=30, seed=4, still_right=False):
    """A symmetric rig: Hips (moving) > Spine > LeftArm > LeftForeArm, and > LeftUpLeg > LeftLeg > LeftFoot, the
    right side the mirror image (its rest turns the left's reflected across x = 0, so their rolls differ); random
    motion on every turning bone (the right arm still with `still_right`)."""
    r = np.random.default_rng(seed)
    spec = [("Hips", None, [0, 1.0, 0]), ("Spine", "Hips", [0, 0.3, 0])]
    for s, x in (("Left", 1), ("Right", -1)):
        spec += [
            (f"{s}Arm", "Spine", [x * 0.2, 0.2, 0]),
            (f"{s}ForeArm", f"{s}Arm", [x * 0.28, 0, 0]),
            (f"{s}UpLeg", "Hips", [x * 0.1, -0.05, 0]),
            (f"{s}Leg", f"{s}UpLeg", [0, -0.45, 0]),
            (f"{s}Foot", f"{s}Leg", [0, -0.45, 0]),
        ]
    rest_l = {n: _anim._unit(r.normal(0, 0.3, 4) + [0, 0, 0, 1]) for n, *_ in spec if "Left" in n}
    bones = []
    for n, p, t in spec:
        q = rest_l.get(n)
        if q is None and "Right" in n:
            q = _anim._reflect(rest_l[n.replace("Right", "Left")], X)
        q = np.array([0, 0, 0, 1.0]) if q is None else q
        ang = np.cumsum(r.normal(0, 0.04, (F, 3)), 0)
        delta = np.concatenate(
            [np.sin(ang / 2), np.cos(np.linalg.norm(ang, axis=1) / 2)[:, None]], 1
        )
        if still_right and "Right" in n:
            delta = np.tile([0, 0, 0, 1.0], (F, 1))
        b = {
            "name": n,
            "parent": p,
            "rotation": _anim._unit(_anim.qmul(q, _anim._unit(delta))),
            "rest": {"q": q, "t": t},
        }
        if n == "Hips":
            b["translation"] = np.cumsum(r.normal(0, 0.01, (F, 3)), 0) + t
        else:
            b["offset"] = np.array(t, float)
        bones.append(b)
    return {"fps": FPS, "frames": F, "bones": bones}


def _by(clip):
    return {b["name"]: b for b in clip["bones"]}


def test_a_mirror_twice_is_the_clip():
    base = _sym()
    once = _anim.evaluate(base, {"mirror": True})
    for b in once["bones"]:
        b["rest"] = _by(base)[b["name"]]["rest"]
    twice = _by(_anim.evaluate(once, {"mirror": True}))
    for b in base["bones"]:
        for k in ("rotation", "translation"):
            if k in b:
                got, want = twice[b["name"]][k], b[k]
                if k == "rotation":  # q and -q are one turn
                    got = got * np.sign(np.sum(got * want, 1, keepdims=True))
                np.testing.assert_allclose(got, want, atol=1e-9)


def test_a_left_arm_wave_becomes_a_right_arm_wave():
    base = _sym(still_right=True)
    out = _anim.evaluate(base, {"mirror": True})
    rest = {n: b["rest"]["q"] for n, b in _by(base).items()}
    moving = lambda clip, n: float(np.ptp(_by(clip)[n]["rotation"], axis=0).max())
    assert moving(base, "RightArm") == 0 and moving(base, "LeftArm") > 0.05
    assert moving(out, "RightArm") > 0.05 and moving(out, "LeftArm") < 1e-9
    np.testing.assert_allclose(  # the still arm now holds its own rest turn
        np.abs(_by(out)["LeftArm"]["rotation"] @ rest["LeftArm"]), 1, atol=1e-9
    )
    # the hips' sideways travel reversed, its height kept
    hb, ho = _by(base)["Hips"]["translation"], _by(out)["Hips"]["translation"]
    np.testing.assert_allclose(ho[:, 0], -hb[:, 0], atol=1e-9)
    np.testing.assert_allclose(ho[:, 1:], hb[:, 1:], atol=1e-9)


def test_no_rest_no_mirror():
    base = _sym()
    for b in base["bones"]:
        b.pop("rest")
    out = _anim.evaluate(base, {"mirror": True})
    np.testing.assert_allclose(_by(out)["LeftArm"]["rotation"], _by(base)["LeftArm"]["rotation"])


def test_butterworth_keeps_a_quick_gesture_and_takes_the_jitter():
    F, sigma = 240, 3
    t = np.arange(F)
    half = 0.1325 / sigma  # the Gaussian's half-power frequency, cycles a frame
    gesture = np.sin(2 * np.pi * 0.5 * half * t)[:, None]  # a movement at half that
    noise = np.sin(2 * np.pi * 0.45 * t)[:, None]  # jitter near the frame rate's limit
    inner = slice(40, 200)
    size = lambda y: float(np.ptp(y[inner]))
    bw, ga = _anim._butter(gesture, sigma), _anim._gauss(gesture, sigma)
    assert (
        size(bw) > size(ga) + 0.05 and size(bw) > 1.9
    )  # the gesture kept: 0.97 of it against 0.92
    assert size(_anim._butter(noise, sigma)) < 0.01  # the jitter gone
    # the same half-power point: a movement there kept by both alike
    at = np.sin(2 * np.pi * half * t)[:, None]
    assert abs(size(_anim._butter(at, sigma)) - size(_anim._gauss(at, sigma))) < 0.1
    assert (
        np.abs(_anim._butter(np.ones((F, 2)) * 3, sigma) - 3).max() < 1e-9
    )  # a constant untouched
    # a steady movement (a straight line) stays straight to its ends (odd reflection, from 24 frames up), where the
    # Gaussian, holding its edges, bends both ends by most of a frame's travel
    line = np.arange(24.0)[:, None]
    assert np.abs(_anim._butter(line, 2.0) - line).max() < 1e-3
    assert np.abs(_anim._gauss(line, 2.0) - line)[[0, -1]].min() > 0.5


def test_reach_moves_the_hips_toward_a_foot_left_behind():
    base = _sym(seed=7)
    hips = _by(base)["Hips"]
    hips["translation"] = hips["translation"] + np.linspace(0, 0.9, base["frames"])[:, None] * [
        0,
        0,
        1.0,
    ]
    lock = {"bone": "LeftFoot", "from": 2, "to": 27, "blend": 2}
    plain = _anim.evaluate(base, {"feet": [lock]})
    held = _anim.evaluate(base, {"feet": [lock | {"reach": 0.97}]})
    moved = np.linalg.norm(
        _by(held)["Hips"]["translation"] - _by(plain)["Hips"]["translation"], axis=1
    )
    assert moved.max() > 0.01 and moved[0] < moved.max()  # eased, not a jump at the start
    for f in range(4, 26):  # inside the lock the pinned foot is within the leg's reach
        names = ("Hips", "LeftUpLeg", "LeftLeg", "LeftFoot")
        path = list(names)
        clip = _by(held)
        rot = {n: clip[n]["rotation"] for n in path}
        pos = {"Hips": clip["Hips"]["translation"]}
        off = {n: clip[n]["offset"] for n in path[1:]}
        ps = _anim._fk(path, rot, pos, off, f)[1]
        leg = np.linalg.norm(ps[2] - ps[1]) + np.linalg.norm(ps[3] - ps[2])
        assert np.linalg.norm(ps[3] - ps[1]) <= 0.97 * leg + 1e-6


def _write_fixture2():
    base = _sym(F=24, seed=11)
    layer = {
        "mirror": True,
        "filter": "butterworth",
        "smooth": {"Spine": 2.0, "Hips": 1.5},
        "feet": [{"bone": "LeftFoot", "from": 6, "to": 14, "blend": 3, "reach": 0.9}],
    }
    case = {"base": base, "layer": layer, "expected": _anim.evaluate(base, layer)}
    FIXTURE2.write_text(
        json.dumps(case, default=lambda a: a.tolist() if hasattr(a, "tolist") else a),
        encoding="utf-8",
    )


def test_fixture2_parity():
    """M69's case for the TypeScript evaluator too (ui/tests/anim.spec.ts): mirror, Butterworth, reach."""
    if os.environ.get("EFFIGY_REGEN_FIXTURE") or not FIXTURE2.is_file():
        _write_fixture2()
    case = json.loads(FIXTURE2.read_text(encoding="utf-8"))
    res = _anim.evaluate(case["base"], case["layer"])
    for g, e in zip(res["bones"], case["expected"]["bones"], strict=True):
        for k in ("rotation", "translation"):
            assert (k in g) == (k in e)
            if k in g:
                np.testing.assert_allclose(g[k], e[k], atol=1e-9, rtol=0)
