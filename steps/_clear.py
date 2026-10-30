"""Arms kept out of the body after the retarget (M67, after StableGen's arm clearance; GPL there: the method only).

A clip from the pack, Kimodo or a video was made on a slimmer body: on a wide or stylised character the forearms
and hands swing through the hips and the belly. Per clip and frame, how far each arm must open sideways (the upper
arm turned about the body's forward axis at the shoulder) for its forearm and hand to clear the torso; that eased
over time (held a little before and after, so the arm opens before it reaches a belt pouch, never less than a frame
needs, round the loop for a looping clip) and baked into the upper arm's turn. numpy and scipy's rotations, from the
GLB itself: the skinned mesh's dominant weights, the clip's channels (`_anim`), our joints (`_connect.rig`, `.pose`).

The torso: per spine bone (`BODY`) an elliptic cylinder in its frame, fitted to the 5–95 % range of the vertices it
carries most, along the body's up, side and forward axes (a torso is wider than deep; a belly sits in front, so the
centre is fitted too; the cylinders' heights stretched to meet, no gap between two). The arm: points along the
forearm (elbow to wrist) and the hand (wrist to knuckles), each with its limb's radius (the 88th, for the hand the
75th, percentile distance of its vertices from the bone) and a margin of 1 % of the height. What already overlaps at rest is allowed as deep as it is there (StableGen keeps a reference pose's
intersections the same way). ponytail: no arm against the other arm, no legs, sideways only (a hand in front of
the belly opens sideways too); add when clips show it.
"""

import itertools
import math

import numpy as np
from scipy.spatial.transform import Rotation

from steps import _anim, _connect, _glb
from steps._common import StepError

BODY = ("Hips", "Spine", "Spine1", "Spine2")
MAX_DEG = 40  # the most an arm is opened; a clip that needs more is reported (`uncleared`)
# the easing: a frame's need held this long either side, then a Gaussian
HOLD_S, SIGMA_S = 0.17, 0.1
SLOP = 1e-3  # inside means deeper than at rest by this much (normalised): float32 keys at the rest pose are not


def _pct(x, q):
    return float(np.percentile(x, q))


def _verts(doc, blob, w) -> dict[int, np.ndarray]:
    """The skinned vertices at rest (world), by the joint node that carries each most."""
    out = {}
    for node in doc["nodes"]:
        if "mesh" not in node or "skin" not in node:
            continue
        skin = doc["skins"][node["skin"]]
        joints = skin["joints"]
        ibm = (
            _glb.accessor(doc, blob, skin["inverseBindMatrices"])
            .reshape(-1, 4, 4)
            .transpose(0, 2, 1)
        )
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            att = prim["attributes"]
            if "JOINTS_0" not in att:
                continue
            v = _glb.accessor(doc, blob, att["POSITION"]).astype(float)
            slot = _glb.accessor(doc, blob, att["JOINTS_0"])[
                np.arange(len(v)), np.argmax(_glb.accessor(doc, blob, att["WEIGHTS_0"]), axis=1)
            ]
            for k in np.unique(slot):
                m = (
                    w[joints[k]] @ ibm[k]
                )  # glTF: a skinned vertex is joint world · inverse bind · position
                at = v[slot == k] @ m[:3, :3].T + m[:3, 3]
                out[joints[k]] = np.r_[out.get(joints[k], np.zeros((0, 3))), at]
    return out


def _radius(pts, a, b, q, lo, hi) -> float:
    """The q-th percentile distance of pts from the segment a–b, clamped to [lo, hi]."""
    if len(pts) < 8:
        return (lo + hi) / 2
    d = b - a
    t = np.clip((pts - a) @ d / max(d @ d, 1e-12), 0, 1)
    return float(np.clip(_pct(np.linalg.norm(pts - (a + t[:, None] * d), axis=1), q), lo, hi))


def body(doc, blob, r: dict) -> dict | None:
    """The torso's cylinders and each arm's sample points (in its bones' rest frames) and radii; None without a
    skinned mesh on our spine."""
    name_of = {i: n for n, i in r["node"].items()}
    by = {
        name_of[i]: p
        for i, p in _verts(doc, blob, _connect._world(doc, {})).items()
        if i in name_of
    }
    pos, rot = r["pos"], r["rot"]
    up = _connect._unit(pos["Neck"] - pos["Hips"])
    side = pos["LeftArm"] - pos["RightArm"]
    side = _connect._unit(side - (side @ up) * up)
    fwd = np.cross(side, up)
    h = float(np.ptp(np.concatenate([*by.values(), np.array(list(pos.values()))]) @ up))
    cyl = []
    for b in BODY:
        if len(p := by.get(b, ())) < 8:
            continue
        d = p - pos[b]
        y, x, z = d @ up, d @ side, d @ fwd
        cyl.append(
            {
                "bone": b,
                "axes": rot[b].T
                @ np.array([up, side, fwd]).T,  # columns: up, side, forward in its frame
                "y": (_pct(y, 5), _pct(y, 95)),
                "c": tuple((_pct(t, 5) + _pct(t, 95)) / 2 for t in (x, z)),
                "a": tuple(
                    float(np.clip((_pct(t, 95) - _pct(t, 5)) / 2, 0.035 * h, 0.2 * h))
                    for t in (x, z)
                ),
            }
        )
    if not cyl:
        return None
    # each cylinder's top stretched up to the next one's bottom: their 5–95 % ranges leave gaps on a real mesh
    stack = sorted(cyl, key=lambda c: pos[c["bone"]] @ up + c["y"][0])
    for c, nxt in itertools.pairwise(stack):
        bottom = pos[nxt["bone"]] @ up + nxt["y"][0] - pos[c["bone"]] @ up
        c["y"] = (c["y"][0], max(c["y"][1], bottom))
    # which way is out: the turn about the forward axis that takes a hanging left arm to the left
    out = float(np.sign(np.cross(fwd, -up) @ side))
    arms = {}
    for s, sense in (("Left", out), ("Right", -out)):
        e, wr, k = pos[f"{s}ForeArm"], pos[f"{s}Hand"], pos[f"{s}HandMiddle1"]
        rf = _radius(by.get(f"{s}ForeArm", ()), e, wr, 88, 0.01 * h, 0.075 * h)
        rh = _radius(by.get(f"{s}Hand", ()), wr, k, 75, 0.01 * h, 0.035 * h)
        # six points on the forearm (elbow to wrist), three on the hand (to the knuckles), each in its bone's frame
        fore = [
            (f"{s}ForeArm", rot[f"{s}ForeArm"].T @ (t * (wr - e)), rf) for t in np.linspace(0, 1, 6)
        ]
        hand = [(f"{s}Hand", rot[f"{s}Hand"].T @ (t * (k - wr)), rh) for t in (1 / 3, 2 / 3, 1)]
        arms[s] = {"points": fore + hand, "sense": sense}
    # the axis to open about each frame: the hips' forward, in their frame
    return {"cyl": cyl, "arms": arms, "margin": 0.01 * h, "fwd": rot["Hips"].T @ fwd}


def _depth(B, P, W, Q, rad):
    """For points Q [F, ..., K, 3] with radii rad [K]: each cylinder's normalised radial distance (< 1 inside),
    inf above or below it; [F, ..., K, C]."""
    out = []
    for c in B["cyl"]:
        lead = (len(Q),) + (1,) * (Q.ndim - 3)
        ax = np.einsum("fij,jk->fik", W[c["bone"]], c["axes"]).reshape(
            lead + (3, 3)
        )  # world up, side, fwd
        d = Q - P[c["bone"]].reshape(lead + (1, 3))
        y, x, z = (np.einsum("...ki,...i->...k", d, ax[..., :, j]) for j in range(3))
        n = np.hypot((x - c["c"][0]) / (c["a"][0] + rad), (z - c["c"][1]) / (c["a"][1] + rad))
        out.append(np.where((y >= c["y"][0]) & (y <= c["y"][1]), n, np.inf))
    return np.stack(out, -1)


def _turns(axis, deg):
    """[F, T, 3, 3] turns about unit axes [F, 3] by deg [F, T]."""
    deg = np.asarray(deg, float)
    rv = axis[:, None, :] * np.radians(deg)[..., None]
    return Rotation.from_rotvec(rv.reshape(-1, 3)).as_matrix().reshape(deg.shape + (3, 3))


def _ease(need, fps, loop):
    """StableGen's easing of the per-frame need: held over ±HOLD_S, blurred by SIGMA_S, never below the need; a
    looping clip wraps (its last frame is its first)."""
    F = len(need)
    if F < 3 or not need.any():
        return need
    n = F - 1 if loop else F
    base, hold = need[:n], max(1, round(HOLD_S * fps))
    idx = np.arange(n)[:, None] + np.arange(-hold, hold + 1)
    held = base[idx % n if loop else np.clip(idx, 0, n - 1)].max(1)
    # a loop blurred as three copies end to end, the middle one kept
    out = _anim._gauss(np.tile(held, 3 if loop else 1)[:, None], max(SIGMA_S * fps, 0.5))[:, 0]
    out = np.maximum(out[n : 2 * n] if loop else out, base)
    return np.r_[out, out[:1]] if loop else out


def clear(doc, blob, fps: float, skip=()) -> tuple[bytes | None, dict]:
    """Every clip's arms (but the clips in `skip`) opened where they would enter the torso. Returns the new GLB
    (None when nothing moved) and per changed clip the most each arm opened ({"Left": deg, "Right": deg}) and
    "uncleared", the frames MAX_DEG did not clear for one arm or both. StepError when there is nothing to check
    against: a rig not on our 52 joints, no skinned torso."""
    try:
        r = _connect.rig(doc)
    except StepError as e:
        raise StepError(
            "bad_params", "not a person on the 52-joint skeleton: " + str(e).split("; ")[-1]
        ) from e
    B = body(doc, blob, r)
    if B is None:
        raise StepError("missing_input", "no skinned torso: the spine bones carry no vertices")
    rest_P = {n: r["pos"][n][None] for n in r["node"]}
    rest_W = {n: r["rot"][n][None] for n in r["node"]}
    degs = np.arange(MAX_DEG + 1, dtype=float)
    edits, report = {}, {}
    for anim in doc.get("animations", []):
        if (name := anim.get("name")) in skip:
            continue
        base = _anim.sample_clip(doc, blob, name, fps)
        F = base["frames"]
        P, W = _connect.pose(doc, blob, r, name, np.arange(F) / fps)
        bones = {b["name"].split(":")[-1]: b for b in base["bones"]}
        axis = np.einsum("fij,j->fi", W["Hips"], B["fwd"])
        # a looping clip ends on its first pose (every bone within 2°)
        loop = all(
            abs(float(b[_anim.ROT][0] @ b[_anim.ROT][-1])) > math.cos(math.radians(1))
            for b in base["bones"]
            if _anim.ROT in b
        )
        got, stuck = {}, np.zeros(F, bool)
        for s, arm in B["arms"].items():
            if _anim.ROT not in bones.get(f"{s}Arm", {}):
                continue
            rad = np.array([p[2] for p in arm["points"]]) + B["margin"]

            def at(P, W, pts=arm["points"]):  # the sample points in the world [F, K, 3]
                return np.stack([P[b] + np.einsum("fij,j->fi", W[b], v) for b, v, _ in pts], 1)

            # [K, C]: how deep each point may be in each cylinder (at most as deep as at rest)
            allowed = np.minimum(_depth(B, rest_P, rest_W, at(rest_P, rest_W), rad)[0], 1.0)
            S, Q = P[f"{s}Arm"], at(P, W)  # the shoulder [F, 3], the points [F, K, 3]
            T = _turns(axis, np.broadcast_to(arm["sense"] * degs, (F, len(degs))))  # [F, D, 3, 3]
            Qd = S[:, None, None] + np.einsum("fdij,fkj->fdki", T, Q - S[:, None])  # [F, D, K, 3]
            free = ~(_depth(B, P, W, Qd, rad) < allowed - SLOP).any((-1, -2))  # [F, D]
            need = np.where(free.any(1), degs[np.argmax(free, 1)], MAX_DEG)
            ease = _ease(need, fps, loop)
            if ease.max() < 0.05:
                continue
            Tf = _turns(axis, arm["sense"] * ease[:, None])[:, 0]  # [F, 3, 3] in the world
            par = W[r["parent"][f"{s}Arm"]]
            # the world turn in the parent's frame: parent⁻¹ · turn · parent
            fix = Rotation.from_matrix(np.einsum("fji,fjk,fkl->fil", par, Tf, par))
            q0 = bones[f"{s}Arm"][_anim.ROT]
            q = (fix * Rotation.from_quat(q0)).as_quat()
            bones[f"{s}Arm"][_anim.ROT] = q * np.sign(np.sum(q * q0, 1, keepdims=True) + 1e-12)
            got[s] = round(float(ease.max()), 1)
            stuck |= ~free.any(1)
        if stuck.any():
            got["uncleared"] = int(stuck.sum())
        if got:
            edits[name], report[name] = base, got
    if not edits:
        return None, {}
    return _anim.write_glb(doc, blob, edits, fps), report
