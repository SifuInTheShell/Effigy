"""Skeleton fitting for rig_fit (IMPLEMENTATION.md M19): the 52-joint Mixamo template, keypoints → joints.

Library frame: glTF, Y up, metres, the character faces +Z, so its left is +X. numpy only.

Markers (what the user drags, as in Mixamo's auto-rigger) are 13 points; every joint is derived from
them, so moving a marker moves its chain. Fingers come from DWPose's hand keypoints when they're
confident, else from proportions along the forearm.
ponytail: proportions are fixed fractions (an average adult); MHR/SOMA-X priors when a character needs them.
"""

import re
from itertools import pairwise

import numpy as np

SIDES = ("Left", "Right")
FINGERS = ("Thumb", "Index", "Middle", "Ring", "Pinky")


def template() -> list[tuple[str, str | None]]:
    """(name, parent) for the 52 joints: 22 body + 30 fingers, Mixamo names without the `mixamorig:` prefix."""
    j = [
        ("Hips", None),
        ("Spine", "Hips"),
        ("Spine1", "Spine"),
        ("Spine2", "Spine1"),
        ("Neck", "Spine2"),
    ]
    j.append(("Head", "Neck"))
    for s in SIDES:
        j += [(f"{s}Shoulder", "Spine2"), (f"{s}Arm", f"{s}Shoulder"), (f"{s}ForeArm", f"{s}Arm")]
        j.append((f"{s}Hand", f"{s}ForeArm"))
        for f in FINGERS:
            j += [
                (f"{s}Hand{f}1", f"{s}Hand"),
                (f"{s}Hand{f}2", f"{s}Hand{f}1"),
                (f"{s}Hand{f}3", f"{s}Hand{f}2"),
            ]
    for s in SIDES:
        j += [
            (f"{s}UpLeg", "Hips"),
            (f"{s}Leg", f"{s}UpLeg"),
            (f"{s}Foot", f"{s}Leg"),
            (f"{s}ToeBase", f"{s}Foot"),
        ]
    return j


MARKERS = ["Chin", "Groin"] + [
    f"{s}{p}" for s in SIDES for p in ("Shoulder", "Elbow", "Wrist", "Knee", "Ankle")
]
# COCO-WholeBody indices (person's left/right): body, feet, hands (21 each: wrist, then 4 per finger)
BODY = {
    "Nose": 0,
    "LeftShoulder": 5,
    "RightShoulder": 6,
    "LeftElbow": 7,
    "RightElbow": 8,
    "LeftWrist": 9,
}
BODY |= {"RightWrist": 10, "LeftHip": 11, "RightHip": 12, "LeftKnee": 13, "RightKnee": 14}
BODY |= {"LeftAnkle": 15, "RightAnkle": 16}
HAND0 = {"Left": 91, "Right": 112}
# where a bone ends when its joint has no single child: the same table as blender_ops/skin_proxy's,
# which Blender can't import from here (tests/test_rig.py holds them equal)
TAIL = {
    "Hips": "Spine",
    "Spine2": "Neck",
    "LeftHand": "LeftHandMiddle1",
    "RightHand": "RightHandMiddle1",
}


def nearest_bone(p, joints: list[dict]) -> str:
    """The body bone (no finger) whose segment, joint → its child, passes nearest to p: a rigid part's
    default bone (M32: a helmet → Head, a shield on the forearm → LeftForeArm, a held sword → the hand).
    A leaf (the head, a toe) continues its parent bone by that bone's length. joints: skeleton.json's."""
    pos = {j["name"]: np.asarray(j["position"], float) for j in joints}
    kids: dict = {}
    for j in joints:
        kids.setdefault(j["parent"], []).append(j["name"])
    best, name = np.inf, None
    for j in joints:
        n = j["name"]
        if any(f in n for f in FINGERS):
            continue
        a = pos[n]
        to = TAIL.get(n) or (kids[n][0] if len(kids.get(n, [])) == 1 else None)
        b = pos[to] if to else 2 * a - pos[j["parent"]]
        ab = b - a
        t = np.clip((p - a) @ ab / (ab @ ab or 1.0), 0, 1)
        if (d := np.linalg.norm(p - (a + t * ab))) < best:
            best, name = d, n
    return name


# --- rays parallel to an axis, against a triangle mesh ---------------------------------------------


def hits(v: np.ndarray, f: np.ndarray, p: np.ndarray, axis: int = 2) -> np.ndarray:
    """Sorted coordinates along `axis` where the line through p (parallel to that axis) meets the mesh."""
    a, b = [i for i in range(3) if i != axis]
    t = v[f]  # [F, 3, 3]
    lo, hi = t[:, :, [a, b]].min(1), t[:, :, [a, b]].max(1)
    q = np.array([p[a], p[b]])
    t = t[((lo <= q) & (q <= hi)).all(1)]
    if not len(t):
        return np.empty(0)
    p0, p1, p2 = (t[:, k][:, [a, b]] for k in range(3))
    d = (p1[:, 1] - p2[:, 1]) * (p0[:, 0] - p2[:, 0]) + (p2[:, 0] - p1[:, 0]) * (
        p0[:, 1] - p2[:, 1]
    )
    ok = np.abs(d) > 1e-18
    p0, p1, p2, t, d = p0[ok], p1[ok], p2[ok], t[ok], d[ok]
    w0 = ((p1[:, 1] - p2[:, 1]) * (q[0] - p2[:, 0]) + (p2[:, 0] - p1[:, 0]) * (q[1] - p2[:, 1])) / d
    w1 = ((p2[:, 1] - p0[:, 1]) * (q[0] - p2[:, 0]) + (p0[:, 0] - p2[:, 0]) * (q[1] - p2[:, 1])) / d
    w2 = 1 - w0 - w1
    inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
    z = np.sort((w0 * t[:, 0, axis] + w1 * t[:, 1, axis] + w2 * t[:, 2, axis])[inside])
    return (
        z[np.r_[True, np.diff(z) > 1e-6]] if len(z) else z
    )  # a ray through a shared edge hits twice


def spans(v, f, p: np.ndarray, axis: int = 2, from_top: bool = False) -> list[list[float]]:
    """The solid [lo, hi] spans along the line through p parallel to `axis`: pairs of hits (paired from
    the high end with `from_top`: an open mesh's odd hit count drops the far one), two of them joined
    where `inside` holds in the gap between (the cavity of a hollow shell: a generated limb can be two
    1 mm walls round a hollow, and the middle of the first pair put its elbows, knees and hips in the
    front wall, which fit_quality then read as centred)."""
    z = hits(v, f, p, axis)
    z = z[len(z) % 2 :] if from_top else z
    pairs = [[z[i], z[i + 1]] for i in range(0, len(z) - 1, 2)]
    if len(pairs) < 2:
        return pairs
    gaps = np.repeat(np.asarray(p, float)[None], len(pairs) - 1, 0)
    gaps[:, axis] = [(a[1] + b[0]) / 2 for a, b in pairwise(pairs)]
    out = pairs[:1]
    # ponytail: an air gap with surfaces round it on 2 axes (a hand held in front of the belly) joins
    # too; rig_fit reads an A-pose, hands out to the sides. A gap-width or wall-thickness test if it bites.
    for nxt, joined in zip(pairs[1:], inside(v, f, gaps)):
        if joined:
            out[-1] = [out[-1][0], nxt[1]]
        else:
            out.append(nxt)
    return out


def mid_depth(v, f, x: float, y: float, near: float | None = None, facing: int = 1) -> float | None:
    """The middle of the first solid span a ray from the viewer crosses at (x, y) (facing +1: the
    character faces +Z and the viewer is in front), or of the span nearest `near`; None on a miss."""
    p = np.array([x, y, 0.0])
    s = spans(v, f, p, from_top=facing > 0)[::-facing]  # the viewer's side first
    if not s:
        z = hits(v, f, p)
        return float(z[0]) if len(z) else None
    mids = [(a + b) / 2 for a, b in s]
    return float(mids[0] if near is None else min(mids, key=lambda m: abs(m - near)))


def winding(v: np.ndarray, f: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Generalized winding number of each point (≈1 inside, ≈0 outside; open meshes and double shells
    still read > 0.5 inside: Jacobson et al. 2013, solid angles by Van Oosterom & Strackee)."""
    out = np.empty(len(pts))
    tri = v[f]
    for i, p in enumerate(pts):
        a, b, c = (tri[:, k] - p for k in range(3))
        la, lb, lc = (np.linalg.norm(x, axis=1) for x in (a, b, c))
        num = np.einsum("ij,ij->i", a, np.cross(b, c))
        den = la * lb * lc + np.einsum("ij,ij->i", a, b) * lc + np.einsum("ij,ij->i", b, c) * la
        den += np.einsum("ij,ij->i", c, a) * lb
        out[i] = np.arctan2(num, den).sum() / (2 * np.pi)
    return out


def inside(v, f, pts: np.ndarray) -> np.ndarray:
    """Per point: |winding| > 0.5 (a patch wound inward reads -1, not 0), or inside a solid span along
    at least 2 of the 3 axes (a raw TRELLIS mesh has holes that pull the winding number towards 0), or
    enclosed by the surface both ways along at least 2 of the 3 axes (the hollow chest of a robot built
    from plates, or the cavity of a double-shelled torso, where the spine belongs; along the third axis
    the ray may leave through the neck or an open bottom: M22 read the dwarf's and the mage's whole
    torso chain and the viking's spine and head as outside that way, all of them in place)."""
    w = np.abs(winding(v, f, pts)) > 0.5
    for i, p in enumerate(pts):
        if not w[i]:
            spans = enclosed = 0
            for axis in range(3):
                z = hits(v, f, p, axis)
                spans += any(z[k] <= p[axis] <= z[k + 1] for k in range(0, len(z) - 1, 2))
                enclosed += bool(len(z)) and z[0] < p[axis] < z[-1]
            w[i] = spans >= 2 or enclosed >= 2
    return w


def fit_quality(v, f, joints: list[dict]) -> dict:
    """How well a skeleton sits in its mesh (the editor's Check fit): per joint how centred it is across its
    limb (the lower of its two shortest axis-parallel wall-to-wall spans through it, the limb's cross-section:
    1 in the middle, 0 on the skin; 0 outside), per bone the share of it outside the body (a bone across the air between two
    legs), and `score`, the joints' mean centring in % times the bones' mean share inside. End joints (a toe
    tip, an ear tip: where the last bone stops, at the skin) count only when outside (0); a root on the floor (a creature's) and its
    bones are left out; `attention` lists what to look at, worst first."""
    v, f = np.asarray(v, float), np.asarray(f)
    at = {j["name"]: np.asarray(j["position"], float) for j in joints}
    parent = {j["name"]: j["parent"] for j in joints}
    names = list(at)
    t = np.linspace(0.1, 0.9, 9)[:, None]
    bones = [(parent[n], n) for n in names if parent[n]]
    pts = np.concatenate(
        [np.array([at[n] for n in names]), *(at[a] + t * (at[b] - at[a]) for a, b in bones)]
    )
    ok = inside(v, f, pts)
    held = dict(zip(names, ok[: len(names)]))
    root = {n for n in names if parent[n] is None and not held[n]}  # on the floor
    centred, ends = {}, set(names) - set(parent.values())
    for n in names:
        if n in root or (
            n in ends and held[n]
        ):  # an end joint only says where the last bone stops: a toe tip
            continue
        across = []
        for axis in range(3) if held[n] else ():
            c = at[n][axis]
            # the solid span round the joint (a hollow limb's whole width, not one wall); else the
            # surfaces either side (an open mesh's odd hit count leaves the joint between spans)
            s = next((s for s in spans(v, f, at[n], axis) if s[0] <= c <= s[1]), None)
            if s is None:
                z = hits(v, f, at[n], axis)
                lo, hi = z[z <= c], z[z >= c]
                s = [lo[-1], hi[0]] if len(lo) and len(hi) else None
            if s and s[1] > s[0]:
                across.append((s[1] - s[0], min(c - s[0], s[1] - c) / ((s[1] - s[0]) / 2)))
        centred[n] = round(float(min(b for _, b in sorted(across)[:2])), 2) if across else 0.0
    out = {
        f"{a} > {b}": round(float(1 - ok[len(names) + 9 * i : len(names) + 9 * (i + 1)].mean()), 2)
        for i, (a, b) in enumerate(bones)
        if a not in root
    }
    why = {}  # one line per joint, the worst first: outside, a bone into it through the air, near the skin
    for n in names:
        if n not in root and not held[n]:  # end joints too
            why.setdefault(n, "outside the model")
    for b, x in sorted(out.items(), key=lambda x: -x[1]):
        if x >= 0.3:
            why.setdefault(b.split(" > ")[1], f"the bone from {b.split(' > ')[0]} leaves the body")
    for n, c in sorted(centred.items(), key=lambda x: x[1]):
        if c < 0.25:
            why.setdefault(n, "close to the skin")
    return {
        "score": round(
            100
            * float(np.mean(list(centred.values())))
            * (1 - float(np.mean(list(out.values()) or [0])))
        ),
        "joints": len(names) - len(root),
        "inside": int(sum(held[n] for n in names if n not in root)),
        "centred": centred,
        "bones_outside": {b: x for b, x in out.items() if x > 0},
        "attention": [[n, w] for n, w in why.items()],
    }


def snap_inside(v, f, p, reach: float):
    """p moved to the nearest span middle along X, Y or Z within `reach`, from p or from the nearest
    vertex (a ray that missed the limb), that `inside` confirms (layered cuffs make some spans gaps
    between layers); else p. For a joint a keypoint put on a thin limb's edge."""
    q = v[np.argmin(((v - p) ** 2).sum(1))]
    cands = []
    for start in (p, q):
        for axis in range(3):
            for c in [(a + b) / 2 for a, b in spans(v, f, start, axis)]:
                x = start.copy()
                x[axis] = c
                if np.linalg.norm(x - p) < reach:
                    cands.append(x)
    cands.sort(key=lambda x: np.linalg.norm(x - p))
    ok = inside(v, f, np.array(cands)) if cands else []
    return next((c for c, good in zip(cands, ok) if good), p)


# --- markers → skeleton --------------------------------------------------------------------------


def _run(alpha: np.ndarray, p: np.ndarray, d: np.ndarray, reach: float) -> float:
    """Length of the solid (alpha > 0.5) run through pixel p along direction d, within ±reach."""
    h, w = alpha.shape
    n = 0
    for sign in (1, -1):
        for t in range(1, int(reach)):
            x, y = (p + sign * t * d).round().astype(int)
            if not (0 <= x < w and 0 <= y < h) or alpha[y, x] <= 0.5:
                break
            n += 1
    return float(n + 1)


def refine_shoulders(pts2d: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Shoulder keypoints moved out to the joint: the pose model marks where a wide torso ends (the
    robot's chest plate), not where the arm pivots under the shoulder pad. Along the shoulder's row the
    silhouette is followed outward to its edge; the joint sits half an upper arm's thickness inside it.
    Only ever outward, at most half the upper arm's length. Pixels of the front render."""
    pts = pts2d.copy()
    mid = (pts2d[BODY["LeftShoulder"]] + pts2d[BODY["RightShoulder"]]) / 2
    for s in SIDES:
        sh, el = pts2d[BODY[f"{s}Shoulder"]][:2], pts2d[BODY[f"{s}Elbow"]][:2]
        arm = el - sh
        length = float(np.linalg.norm(arm))
        if length < 4:
            continue
        u = arm / length
        thick = np.median(
            [
                _run(alpha, sh + u * length * t, np.array([-u[1], u[0]]), length)
                for t in (0.5, 0.6, 0.7)
            ]
        )
        out = np.array([np.sign(sh[0] - mid[0]) or 1.0, 0.0])
        edge = sh.copy()
        while np.linalg.norm(edge - sh) < length and _inside_px(alpha, edge + out):
            edge = edge + out
        joint = edge - out * thick / 2
        step = float((joint - sh) @ out)
        if step > 0:
            pts[BODY[f"{s}Shoulder"], :2] = sh + out * min(step, 0.5 * length)
    return pts


def _inside_px(alpha, p) -> bool:
    x, y = p.round().astype(int)
    return 0 <= y < alpha.shape[0] and 0 <= x < alpha.shape[1] and alpha[y, x] > 0.5


def auto_markers(pts2d, scores, to3d, ground: float, top: float) -> tuple[dict, dict]:
    """Markers from DWPose keypoints (pixels → library frame by `to3d(u, v, near)`); returns
    (markers, confidence). Chin: below the nose by a fifth of nose→shoulder-line; groin: between the hips,
    lowered by a quarter of hip→knee."""
    kp = {k: pts2d[i] for k, i in BODY.items()}
    sc = {k: float(scores[i]) for k, i in BODY.items()}
    m, conf = {}, {}
    for s in SIDES:
        for part in ("Shoulder", "Elbow", "Wrist", "Knee", "Ankle"):
            m[s + part], conf[s + part] = kp[s + part], sc[s + part]
    sh = (kp["LeftShoulder"] + kp["RightShoulder"]) / 2
    m["Chin"] = kp["Nose"] + (sh - kp["Nose"]) * 0.25
    conf["Chin"] = min(sc["Nose"], sc["LeftShoulder"], sc["RightShoulder"])
    hip = (kp["LeftHip"] + kp["RightHip"]) / 2
    knee = (kp["LeftKnee"] + kp["RightKnee"]) / 2
    m["Groin"] = hip + (knee - hip) * 0.25
    conf["Groin"] = min(sc["LeftHip"], sc["RightHip"])
    out = {k: to3d(*m[k]) for k in MARKERS}
    return out, conf


def sides_agree(markers: dict, facing: int) -> bool:
    """A character facing +Z has its left side at +X, one facing -Z at -X. A 2D pose model can label the
    sides the other way round on a frontal view (the helmeted dwarf, M22): then every joint called Left
    is on the right, and a retargeted clip plays mirrored front to back."""
    return (markers["LeftShoulder"][0] - markers["RightShoulder"][0]) * facing > 0


def swap_sides(markers: dict, conf: dict, hands: dict) -> tuple[dict, dict, dict]:
    """Left ↔ Right in the marker, confidence and hand-keypoint names."""

    def flip(k):
        return k.replace("Left", "\0").replace("Right", "Left").replace("\0", "Right")

    return tuple({flip(k): x for k, x in d.items()} for d in (markers, conf, hands))


def feet_cue(v) -> float:
    """How far the feet reach ahead of the shins along Z, as a fraction of the height: positive for a
    character facing +Z. Toes stick out forward on every shod or bare foot (0.012–0.051 on the M22
    bench), so the feet say which way the body faces when no face can be read (a helmet, a flat cartoon
    face: the pose model's face scores were a coin flip on the dwarf and the runner); round cartoon
    shoes give ~0 and decide nothing."""
    y = v[:, 1]
    ground, height = y.min(), y.max() - y.min()
    feet = v[y < ground + 0.06 * height]
    shins = v[(y > ground + 0.10 * height) & (y < ground + 0.25 * height)]
    if not len(feet) or not len(shins):
        return 0.0
    return float(feet[:, 2].mean() - shins[:, 2].mean()) / height


def palm_normals(j: dict, v: np.ndarray) -> dict:
    """Per side, the direction out of the palm (unit, library frame) or None. The finger keypoints'
    normal (middle finger × thumb-to-pinky) when it agrees with the hand's own shape, within 35° of the
    mesh's thinnest axis beyond the wrist (a hand is flat), and does not turn the palm up; else that mesh
    axis, signed down or toward the body, or by the fingers' sign for a horizontal palm. The fingers were
    swapped or scattered on one hand of 9 of the 16 M22 characters. Written into skeleton.json as the
    hands' `twist` (M22b), so the viewer shows what the retarget will do and the user can turn it."""
    up = j["Head"] - j["Hips"]
    up = up / (np.linalg.norm(up) or 1.0)
    span = [p @ up for p in j.values()]
    height = max(span) - min(span)
    out = {}
    for s in SIDES:
        f = np.cross(
            j[f"{s}HandMiddle1"] - j[f"{s}Hand"], j[f"{s}HandThumb1"] - j[f"{s}HandPinky1"]
        )
        phys = f / np.linalg.norm(f) if np.linalg.norm(f) > 1e-9 else None  # the way the palm faces
        if phys is not None and s == "Right":
            phys = -phys  # the formula mirrors on the right hand
        wrist, elbow = j[f"{s}Hand"], j[f"{s}ForeArm"]
        axis = wrist - elbow
        axis = axis / (np.linalg.norm(axis) or 1.0)
        rel = v - wrist
        along = rel @ axis
        radial = np.linalg.norm(rel - np.outer(along, axis), axis=1)
        pts = v[(along > 0) & (along < 0.14 * height) & (radial < 0.07 * height)]
        m = None
        if len(pts) >= 50:
            _, sv, vt = np.linalg.svd(pts - pts.mean(0), full_matrices=False)
            if sv[2] > 0 and sv[1] / sv[2] >= 1.4:  # a clearly flat hand
                m = vt[2]
        if phys is not None and (m is None or (abs(m @ phys) > 0.82 and phys @ up < 0.1)):
            out[s] = phys if (m is not None or phys @ up < 0.1) else -phys
            continue
        if m is None:
            out[s] = None
            continue
        inward = j["Hips"] - wrist
        inward = inward - up * (inward @ up)
        inward = inward / (np.linalg.norm(inward) or 1.0)
        score = m @ (-up + inward)
        if (
            abs(score) < 0.2 and phys is not None
        ):  # a horizontal palm: forward or back, the fingers say
            score = m @ phys
        out[s] = m if score > 0 else -m
    return out


def skeleton(m: dict, v, f, hands: dict | None = None, facing: int = 1) -> dict:
    """All 52 joints from the 13 markers (+ optional 3D hand keypoints per side, [21, 3]); facing -1:
    the character faces -Z (toes and thumbs point that way)."""
    m = {k: np.asarray(p, float) for k, p in m.items()}
    up = np.array([0.0, 1.0, 0.0])
    ground, height = float(v[:, 1].min()), float(np.ptp(v[:, 1]))

    def centred(p, near=None):  # same x, y; depth moved to the middle of the body there
        z = mid_depth(v, f, p[0], p[1], p[2] if near is None else near, facing)
        return np.array([p[0], p[1], p[2] if z is None else z])

    def core(
        p,
    ):  # torso joints: between the front-most and back-most surface, not the first layer's
        z = hits(v, f, np.array([p[0], p[1], 0.0]))  # middle (the robot's chest plate put its spine
        return (
            np.array([p[0], p[1], (z[0] + z[-1]) / 2]) if len(z) >= 2 else centred(p)
        )  # 19 cm forward)

    j = {}
    for s in SIDES:
        j[f"{s}Arm"], j[f"{s}ForeArm"], j[f"{s}Hand"] = (
            m[f"{s}Shoulder"],
            m[f"{s}Elbow"],
            m[f"{s}Wrist"],
        )
        j[f"{s}Leg"], j[f"{s}Foot"] = m[f"{s}Knee"], m[f"{s}Ankle"]
        knee = m[f"{s}Knee"]
        j[f"{s}UpLeg"] = centred(
            np.array(
                [
                    m["Groin"][0] + 0.8 * (knee[0] - m["Groin"][0]),
                    m["Groin"][1] + 0.05 * height,
                    m["Groin"][2],
                ]
            )
        )
    j["Hips"] = core(
        np.array([m["Groin"][0], (j["LeftUpLeg"][1] + j["RightUpLeg"][1]) / 2, m["Groin"][2]])
    )
    shoulders = (m["LeftShoulder"] + m["RightShoulder"]) / 2
    j["Head"] = core(m["Chin"] + up * 0.02 * height)
    j["Neck"] = core(shoulders + (j["Head"] - shoulders) * 0.3)
    for name, t in (("Spine", 0.25), ("Spine1", 0.5), ("Spine2", 0.75)):
        j[name] = core(j["Hips"] + (j["Neck"] - j["Hips"]) * t)
    for s in SIDES:
        arm = j[f"{s}Arm"]
        j[f"{s}Shoulder"] = centred(j["Neck"] + (arm - j["Neck"]) * 0.35 - up * 0.01 * height)
        foot = j[f"{s}Foot"]
        y = ground + 0.025 * height
        zs = hits(v, f, np.array([foot[0], y, 0.0]), axis=2)
        if len(zs) >= 2 and abs(zs[-1] - zs[0]) > 0.05 * height:  # the foot's heel-to-tip extent
            heel, tip = (zs[0], zs[-1]) if facing > 0 else (zs[-1], zs[0])
            toe_z = heel + 0.7 * (tip - heel)  # the ball of the foot
            # the ankle sits over the back third of the foot; the pose model marks it mid-foot on boots
            # (the kid: 61 % along), which stands the foot bone on end and turns the toes up in every clip
            foot = j[f"{s}Foot"] = np.array([foot[0], foot[1], heel + 0.3 * (tip - heel)])
        else:
            toe_z = foot[2] + facing * 0.08 * height
        j[f"{s}ToeBase"] = np.array([foot[0], y, toe_z])
        j |= _fingers(s, j[f"{s}ForeArm"], j[f"{s}Hand"], (hands or {}).get(s), facing)
    names = [n for n, _ in template()]
    assert set(j) == set(names), set(names) ^ set(j)
    return {n: j[n].round(5).tolist() for n in names}


def _fingers(side: str, elbow, wrist, kps, facing: int = 1) -> dict:
    """Finger joints: hand keypoints 1-3 (thumb), 5-7, 9-11, 13-15, 17-19 when given, else proportions:
    fingers along elbow→wrist, spread across it in the frontal plane, the thumb forward (+Z)."""
    out = {}
    if kps is not None:
        for i, f in enumerate(FINGERS):
            for k in range(3):
                out[f"{side}Hand{f}{k + 1}"] = np.asarray(kps[1 + 4 * i + k], float)
        return out
    d = wrist - elbow
    hand = 0.74 * np.linalg.norm(d)  # hand length ≈ 0.74 forearm (anthropometric averages)
    d = d / (np.linalg.norm(d) or 1)
    fwd = np.array([0.0, 0.0, float(facing)])
    across = np.cross(d, fwd)
    across /= np.linalg.norm(across) or 1
    for i, f in enumerate(FINGERS[1:]):
        base = wrist + d * 0.45 * hand + across * (0.09 - 0.06 * i) * hand
        for k, t in enumerate((0.0, 0.22, 0.36)):
            out[f"{side}Hand{f}{k + 1}"] = base + d * t * hand
    thumb = wrist + d * 0.12 * hand + fwd * 0.12 * hand
    for k, t in enumerate((0.0, 0.18, 0.32)):
        out[f"{side}HandThumb{k + 1}"] = thumb + (d * 0.7 + fwd * 0.3) * t * hand
    return out


# the joints that bend like an elbow or a knee: the human skeleton's ForeArm and Leg (not UpLeg), the animals'
# `Lower` limb bones (fox, horse, kaiju, bird); a jaw's mouth_lower is not one
BENDS = re.compile(r"^(?!.*mouth).*(forearm|lower|(?<!up)leg$)", re.IGNORECASE)


def joint_loops(v, joints: list[dict], min_bands: int = 3) -> dict:
    """Edge loops at the joints (after img2threejs's joint_loops gate, Apache-2.0): the distinct vertex
    bands along the parent bone's axis within ±0.35 of its length of the joint, a band being a tenth of
    the bone wide (a tilted ring stays one band, an irregular dense mesh counts as many). Under
    `min_bands` the elbow or knee creases instead of bending, whatever the weights say. Only vertices
    within the window's radius of the axis count (not the other leg); bones shorter than 5 % of the figure
    (fingers, ears) are left out, and so is every joint but the elbows and knees (`BENDS`): on the 16 bench rigs
    the window only caught a slice of the wide parts (neck, head, shoulders, hips) and flagged 10 of them there,
    never at an elbow or a knee. {"bands": {joint: n}, "few": [the joints under min_bands]}."""
    v = np.asarray(v, float)
    at = {j["name"]: np.asarray(j["position"], float) for j in joints}
    height = float(np.ptp(v[:, 1])) or 1.0
    bands, few = {}, []
    for j in joints:
        p = j.get("parent")
        if not p or p not in at or not BENDS.search(j["name"]):
            continue
        axis = at[j["name"]] - at[p]
        length = float(np.linalg.norm(axis))
        if length < 0.05 * height:
            continue
        axis /= length
        r = 0.35 * length
        off = v - at[j["name"]]
        along = off @ axis
        radial = np.sqrt(np.maximum((off * off).sum(1) - along**2, 0))
        s = np.sort(along[(np.abs(along) <= r) & (radial <= r)])
        n, start = (1, s[0]) if len(s) else (0, 0.0)
        for x in s:  # a band is one tenth of the bone wide, from its first vertex
            if x - start > 0.1 * length:
                n, start = n + 1, x
        if (
            not n
        ):  # no surface within the window: the body there is wider than it (the spine in the torso)
            continue  # nothing to judge, not a joint without loops (every bench character's three spine joints)
        bands[j["name"]] = n
        if n < min_bands:
            few.append(j["name"])
    return {"bands": bands, "few": few}
