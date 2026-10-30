"""Connect clips (IMPLEMENTATION.md M65, transition only): Kimodo makes the motion out of one clip's last
pose, into another clip's first, or between the two. numpy only: steps/motion.py turns `plan`'s output into
Kimodo's own constraints (the full body and the hands' and feet's turns on the seam frames, as Kimodo joins
its own prompts), generates, and `finish` cuts the pinned frames back to the seams.

Our side: the version's rigged_anim.glb, its node tree posed by a clip's channels at the seam frames
(`pose`), placed so the seam faces +Z with the hips over the origin (`place`, Kimodo's heading rule on the hip
line; B at its landing). SOMA's side: Kimodo's rest (the standard T-pose its BVH is written in, the hips 1 m
up as that BVH puts them). Between them, the inverse of blender_ops/retarget (which brings every Kimodo clip
onto our rigs), so that the retarget of the transition lands back on the pose it was made from: per bone our
world turn away from our rest with the retarget's rest alignment taken back out (frames from joint positions:
a bone's direction and the palm or the body's forward; the torso and the clavicles unaligned), SOMA's joints
following from its rest offsets, the hips by our travel over the height ratio.
"""

import json
import math
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from steps import _anim
from steps._common import StepError

FPS = 30  # Kimodo's rate
SEAM = 5  # frames pinned at each end: Kimodo's own num_transition_frames between two prompts
MAX_SECONDS = 8.0  # the transition; with the pinned frames it stays inside Kimodo's 10 s

# our joint → SOMA's (the same table as blender_ops/_maps.SOMA, which steps/ may not import: GPL boundary;
# tests/test_connect.py holds the two equal)
SOMA = {"Hips": "Hips", "Spine": "Spine1", "Spine1": "Spine2", "Spine2": "Chest", "Neck": "Neck1"}
SOMA["Head"] = "Head"
for _s in ("Left", "Right"):
    SOMA |= {
        f"{_s}{n}": f"{_s}{n}" for n in ("Shoulder", "Arm", "ForeArm", "Hand", "Foot", "ToeBase")
    }
    SOMA |= {f"{_s}UpLeg": f"{_s}Leg", f"{_s}Leg": f"{_s}Shin"}
    SOMA |= {f"{_s}HandThumb{k}": f"{_s}HandThumb{k}" for k in (1, 2, 3)}
    for _f in ("Index", "Middle", "Ring", "Pinky"):
        SOMA |= {f"{_s}Hand{_f}{k}": f"{_s}Hand{_f}{k + 1}" for k in (1, 2, 3)}
# the retarget's bone directions and its unaligned bones (blender_ops/retarget.py MAIN, KEEP; the test reads them)
MAIN = {"Hips": "Spine", "Spine2": "Neck", "Neck": "Head"}
MAIN |= {"LeftHand": "LeftHandMiddle1", "RightHand": "RightHandMiddle1"}
KEEP = {"Hips", "Spine", "Spine1", "Spine2", "Neck", "Head", "LeftShoulder", "RightShoulder"}
# the joints a connected clip needs: the body, and the hand joints the palm and the hand's direction come from
NEEDED = ["Hips", "Spine", "Spine1", "Spine2", "Neck", "Head"] + [
    f"{s}{n}"
    for s in ("Left", "Right")
    for n in ("Shoulder", "Arm", "ForeArm", "Hand", "HandMiddle1", "HandThumb1", "HandPinky1")
    + ("UpLeg", "Leg", "Foot", "ToeBase")
]
# a SOMA joint our skeleton lacks takes the turn of the bone it rides on: the second neck joint the first's,
# the jaw and the eyes the head's, the hand's two tips the hand's
RIDES = {"Neck2": "Neck", "Jaw": "Head", "LeftEye": "Head", "RightEye": "Head"}
RIDES |= {f"{s}Hand{t}End": f"{s}Hand" for s in ("Left", "Right") for t in ("Thumb", "Middle")}


# --- small maths -------------------------------------------------------------------------------------------


def _unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def yrot(a: float) -> np.ndarray:
    """A turn by `a` radians about +Y (positive: +Z towards +X, a person facing +Z turning to their left)."""
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1.0, 0], [-s, 0, c]])


def _rot(m3: np.ndarray) -> np.ndarray:
    """The rotation of a 3×3 that may carry a scale (the nearest one)."""
    return Rotation.from_matrix(m3).as_matrix()


def _frame(primary, secondary) -> np.ndarray:
    """Columns: the primary direction, the secondary made perpendicular to it, their cross (retarget's `frame`)."""
    x = _unit(primary)
    y = secondary - x * float(secondary @ x)
    if np.linalg.norm(y) <= 1e-6:
        y = np.cross(x, np.eye(3)[int(np.argmin(np.abs(x)))])
    y = _unit(y)
    return np.stack([x, y, np.cross(x, y)], axis=1)


# --- our rig and its clips in the GLB ------------------------------------------------------------------------


def _local(node, over=None) -> np.ndarray:
    """A node's local 4×4: its rest TRS (or matrix) with a clip's sampled channels (`over`) on top."""
    if "matrix" in node and not over:
        return np.array(node["matrix"], float).reshape(4, 4).T
    over = over or {}
    m = np.eye(4)
    s = np.asarray(over.get("scale", node.get("scale", [1, 1, 1])), float)
    m[:3, :3] = (
        Rotation.from_quat(over.get("rotation", node.get("rotation", [0, 0, 0, 1]))).as_matrix() * s
    )
    m[:3, 3] = over.get("translation", node.get("translation", [0, 0, 0]))
    return m


def _world(doc, over: dict) -> dict[int, np.ndarray]:
    """Every node's world 4×4 (`over`: node → path → value at this instant)."""
    par, out = _anim._parents(doc), {}

    def get(i):
        if i not in out:
            m = _local(doc["nodes"][i], over.get(i))
            out[i] = get(par[i]) @ m if i in par else m
        return out[i]

    for i in range(len(doc["nodes"])):
        get(i)
    return out


def rig(doc) -> dict:
    """Our skeleton: joint name (a "mixamorig:" prefix dropped) → node, parent and children names, and the rest
    world positions and turns. A person on the 52-joint template, or bad_params."""
    nodes = doc["nodes"]
    joints = doc["skins"][0]["joints"] if doc.get("skins") else range(len(nodes))
    node = {nodes[i].get("name", f"node_{i}").split(":")[-1]: i for i in joints}
    if missing := [n for n in NEEDED if n not in node]:
        raise StepError(
            "bad_params",
            f"connecting clips needs a person on the 52-joint skeleton; this one lacks {', '.join(missing[:4])}",
        )
    name = {i: n for n, i in node.items()}
    par = _anim._parents(doc)
    parent = {n: name.get(par.get(i)) for n, i in node.items()}
    children = {n: [c for c, p in parent.items() if p == n] for n in node}
    w = _world(doc, {})
    pos = {n: w[i][:3, 3].copy() for n, i in node.items()}
    return {
        "node": node,
        "parent": parent,
        "children": children,
        "pos": pos,
        "rot": {n: _rot(w[i][:3, :3]) for n, i in node.items()},
        # the way the rest faces: a seam placed facing it is Kimodo's heading 0 (its first_heading_angle) after the
        # retarget's turn T; Effigy's own rigs face +Z (0), an imported one may not
        "heading": heading({n: pos[n][None] for n in ("LeftUpLeg", "RightUpLeg")}, 0),
    }


def clip_seconds(doc, blob, clip: str) -> float:
    anim = doc["animations"][_anim.clip_index(doc, clip)]
    return max(float(t.max()) for *_, t, _v in _anim._channels(doc, blob, anim))


def pose(doc, blob, r: dict, clip: str, times) -> tuple[dict, dict]:
    """Our joints' world positions (T, 3) and turns (T, 3, 3) at `times` (seconds) of a clip."""
    anim = doc["animations"][_anim.clip_index(doc, clip)]
    u = np.asarray(times, float)
    samples = {}
    for ch, _, path, interp, t, v in _anim._channels(doc, blob, anim):
        if path in ("translation", "rotation", "scale"):
            samples.setdefault(ch["target"]["node"], {})[path] = _anim._sample(
                t, v, interp, u, path == "rotation"
            )
    P = {n: np.zeros((len(u), 3)) for n in r["node"]}
    W = {n: np.zeros((len(u), 3, 3)) for n in r["node"]}
    for f in range(len(u)):
        w = _world(doc, {i: {k: a[f] for k, a in s.items()} for i, s in samples.items()})
        for n, i in r["node"].items():
            P[n][f], W[n][f] = w[i][:3, 3], _rot(w[i][:3, :3])
    return P, W


def heading(P: dict, f: int) -> float:
    """Kimodo's heading of a pose (radians, 0 facing +Z): from the hip line, right hip minus left."""
    d = P["RightUpLeg"][f] - P["LeftUpLeg"][f]
    return math.atan2(d[2], -d[0])


def place(P: dict, W: dict, f: int, turn: float = 0.0, at=(0.0, 0.0, 0.0)) -> tuple[dict, dict]:
    """The pose moved so that at frame f its hips stand over `at` (on the floor plane) facing `turn` radians from +Z."""
    R = yrot(turn - heading(P, f))
    c = P["Hips"][f] * [1, 0, 1]
    at = np.asarray(at, float)
    return {n: (p - c) @ R.T + at for n, p in P.items()}, {n: R @ w for n, w in W.items()}


def speed(P: dict) -> np.ndarray:
    """The hips' velocity on the floor over the samples (m/s, x and z; y zero)."""
    v = (P["Hips"][-1] - P["Hips"][0]) / ((len(P["Hips"]) - 1) / FPS)
    return v * [1, 0, 1]


def landing(va, vb, seconds: float, travel: float | None, facing: float = 0.0) -> np.ndarray:
    """Where B's first frame stands, from where A ended (the origin, facing `facing`): `travel` metres straight on,
    or, by default, the mean of A's end velocity and B's start velocity over the transition."""
    if travel is not None:
        return yrot(facing) @ [0.0, 0.0, float(travel)]
    return seconds * (np.asarray(va, float) + np.asarray(vb, float)) / 2


def twists(path: Path) -> dict:
    """skeleton.json's per-joint `twist` vectors (the library's frame, glTF Y up: what we work in)."""
    if not path.is_file():
        return {}
    joints = json.loads(path.read_text(encoding="utf-8")).get("joints", [])
    return {x["name"]: _unit(np.asarray(x["twist"], float)) for x in joints if x.get("twist")}


# --- the retarget's alignment, and its inverse ---------------------------------------------------------------


def _refs(pos: dict):
    """The body's forward and up, and the palm normal per side, from joint positions (retarget's `refs`)."""
    up = _unit(pos["Head"] - pos["Hips"])
    toes = sum(pos[f"{s}ToeBase"] - pos[f"{s}Foot"] for s in ("Left", "Right"))
    forward = toes - up * float(toes @ up)
    if np.linalg.norm(forward) < 1e-6:
        forward = np.cross(up, _unit(pos["RightUpLeg"] - pos["LeftUpLeg"]))
    palm = {
        s: _unit(
            np.cross(
                pos[f"{s}HandMiddle1"] - pos[f"{s}Hand"],
                pos[f"{s}HandThumb1"] - pos[f"{s}HandPinky1"],
            )
        )
        for s in ("Left", "Right")
    }
    return _unit(forward), up, palm


def _along(r: dict, n: str) -> tuple[str, str]:
    """The two joints whose difference is the bone's direction: head to its main child; a leaf continues its parent."""
    if n in MAIN:
        return n, MAIN[n]
    return (n, r["children"][n][0]) if r["children"][n] else (r["parent"][n], n)


def _frames(r: dict, pos: dict, twist: dict, tw: dict | None = None) -> dict:
    """Our bone → its rest frame in `pos` (a rig's joint positions by our names), the twist reference chosen per
    bone as the retarget does (skeleton.json's twists override the palm and their own bones)."""
    forward, up, palm = _refs(pos)
    tw = tw or {}
    for s in ("Left", "Right"):
        if f"{s}Hand" in tw:
            palm[s] = tw[f"{s}Hand"] if s == "Left" else -tw[f"{s}Hand"]
    out = {}
    for n, choice in twist.items():
        a, b = _along(r, n)
        if a not in pos or b not in pos:
            continue
        side = "Left" if n.startswith("Left") else "Right"
        if n in tw and not n.endswith("Hand"):
            sec = tw[n]
        elif "Hand" in n or "ForeArm" in n:
            sec = palm[side]
        else:
            sec = up if choice == "up" else forward
        out[n] = _frame(pos[b] - pos[a], sec)
    return out


def aligns(r: dict, tw: dict, rest: dict) -> tuple[dict, np.ndarray, float]:
    """What the retarget does to a Kimodo clip before posing our bones: (our bone → its alignment, the turn T that
    makes SOMA face our way, our height over SOMA's). `rest`: SOMA joint → rest position (Kimodo's frame).
    ponytail: the height ratio from joints (the head joint over the lowest foot or toe joint, on both rigs); the
    retarget measures Blender's bones, tails included, so a big-headed character's hips travel comes back a few
    per cent off at the seams (the exact ratio would take a Blender run on both rigs)."""
    ours = {n: r["pos"][n] for n in r["node"] if n in SOMA}
    fwd_o = _refs(ours)[0]
    twist = {}
    for n in ours:
        a, b = _along(r, n)
        if a in ours and b in ours:
            twist[n] = "up" if abs(float(_unit(ours[b] - ours[a]) @ fwd_o)) > 0.7 else "forward"
    src = {n: rest[SOMA[n]] for n in ours if SOMA[n] in rest}
    fwd_s = _refs(src)[0]
    T = yrot(math.atan2(fwd_o[0], fwd_o[2]) - math.atan2(fwd_s[0], fwd_s[2]))
    turned = {n: T @ p for n, p in src.items()}
    f_o, f_s = _frames(r, ours, twist, tw), _frames(r, turned, twist)
    al = {n: np.eye(3) if n in KEEP else f_s[n] @ f_o[n].T for n in f_o if n in f_s}

    def tall(p, names):
        return p["Head"][1] - min(p[f"{s}{x}"][1] for s in ("Left", "Right") for x in names)

    scale = tall(ours, ("Foot", "ToeBase")) / tall(rest, ("Foot", "ToeBase"))
    return al, T, float(scale)


def soma_pose(P, W, r, al, T, names, parents, rest, scale) -> tuple[np.ndarray, np.ndarray]:
    """Our pose (placed) as Kimodo's skeleton holds it: positions (F, J, 3) and world turns (F, J, 3, 3) of the
    SOMA joints `names` (parents first, `parents` by name), in Kimodo's frame and its rest's convention (the
    identity at the standard T-pose). Inverse of the retarget: our bone's world turn = Δ·align·rest, so the SOMA
    bone's Δ = (our world turn · our rest⁻¹) · align⁻¹, in Kimodo's frame Tᵀ·Δ·T; the hips travel over `scale`."""
    F = len(P["Hips"])
    delta = {}
    for s in names:
        b = RIDES.get(s) or next((o for o, x in SOMA.items() if x == s), None)
        if b is None:
            raise StepError("failed", f"SOMA joint {s} has no counterpart on our skeleton")
        d = W[b] @ r["rot"][b].T @ al.get(b, np.eye(3)).T  # (F, 3, 3), our world
        delta[s] = T.T @ d @ T
    pos = np.zeros((F, len(names), 3))
    ix = {s: k for k, s in enumerate(names)}
    for k, s in enumerate(names):
        p = parents[k]
        if p is None:  # the hips: their travel from our rest, over the height ratio
            pos[:, k] = rest[s] + ((P["Hips"] - r["pos"]["Hips"]) / scale) @ T
        else:
            pos[:, k] = pos[:, ix[p]] + delta[p] @ (rest[s] - rest[p])
    return pos, np.stack([delta[s] for s in names], axis=1)


# --- the plan and the cut ---------------------------------------------------------------------------------


def frames_for(seconds: float, a: bool, b: bool) -> int:
    """The frames to generate: the transition's own (seconds at 30 fps, both ends included), plus SEAM - 1
    pinned before A's last frame and after B's first."""
    return round(seconds * FPS) + 1 + (SEAM - 1) * (int(a) + int(b))


def plan(src: Path, p: dict, rest: dict, names: list, parents: list) -> dict:
    """Kimodo's seam constraints for a connect request `p` (from, to, seconds, travel, turn): the frames to
    generate, the pinned frame indices, the SOMA joints' positions and turns on them, the cut, the ratio."""
    doc, blob = _anim.read_glb(src / "rigged_anim.glb")
    r = rig(doc)
    al, T, scale = aligns(r, twists(src / "skeleton.json"), rest)
    N = frames_for(p["seconds"], bool(p["from"]), bool(p["to"]))
    idx, Ps, Ws, va = [], [], [], np.zeros(3)
    if p["from"]:
        end = clip_seconds(doc, blob, p["from"])
        P, W = pose(
            doc, blob, r, p["from"], [max(end - (SEAM - 1 - i) / FPS, 0) for i in range(SEAM)]
        )
        P, W = place(P, W, SEAM - 1, r["heading"])
        va = speed(P)
        idx += list(range(SEAM))
        Ps.append(P)
        Ws.append(W)
    if p["to"]:
        end = clip_seconds(doc, blob, p["to"])
        P, W = pose(doc, blob, r, p["to"], [min(i / FPS, end) for i in range(SEAM)])
        P, W = place(P, W, 0, r["heading"] + math.radians(p["turn"]))
        at = landing(va, speed(P), p["seconds"], p["travel"], r["heading"])
        P = {n: x + at for n, x in P.items()}
        idx += list(range(N - SEAM, N))
        Ps.append(P)
        Ws.append(W)
    both = [soma_pose(P, W, r, al, T, names, parents, rest, scale) for P, W in zip(Ps, Ws)]
    return {
        "frames": N,
        "index": idx,
        "pos": np.concatenate([b[0] for b in both]),
        "rot": np.concatenate([b[1] for b in both]),
        "keep": [SEAM - 1 if p["from"] else 0, N - SEAM + 1 if p["to"] else N],
        "seams": ([SEAM - 1] if p["from"] else []) + ([N - SEAM] if p["to"] else []),
        "names": list(names),
        "scale": scale,
    }


def finish(posed: np.ndarray, rots: np.ndarray, pl: dict, names77: list) -> tuple:
    """Kimodo's output (F, 77, 3) and its world turns (F, 77, 3, 3) cut to the transition, then placed so that it
    starts over the origin facing +Z (Effigy's clips start so); and the seam error, the SOMA joints' mean distance
    from what was pinned on the seam frames (cm, Kimodo's size)."""
    ix = [names77.index(n) for n in pl["names"]]
    pinned = dict(zip(pl["index"], pl["pos"]))
    err = [
        round(float(np.linalg.norm(posed[f][ix] - pinned[f], axis=-1).mean() * 100), 1)
        for f in pl["seams"]
    ]
    a, b = pl["keep"]
    posed, rots = posed[a:b], rots[a:b]
    d = posed[0, names77.index("RightLeg")] - posed[0, names77.index("LeftLeg")]
    R = yrot(-math.atan2(d[2], -d[0]))
    c = posed[0, names77.index("Hips")] * [1, 0, 1]
    return (posed - c) @ R.T, R @ rots, err


def seam_check(glb: Path, transition: str, a: str | None, b: str | None) -> dict:
    """M65 acceptance 1, on the laptop: in an animated version holding the clips and the transition, how far our
    joints are (cm, their mean) between the transition's first frame and A's last, and its last and B's first, each
    pose placed over the origin facing +Z: the retarget's round trip measured on our rig, not on Kimodo's."""
    doc, blob = _anim.read_glb(glb)
    r = rig(doc)

    def at(clip, first):
        P, W = pose(doc, blob, r, clip, [0.0 if first else clip_seconds(doc, blob, clip)])
        return place(P, W, 0)[0]

    def cm(P, Q):
        return round(float(np.mean([np.linalg.norm(P[n][0] - Q[n][0]) for n in P])) * 100, 1)

    out = {}
    if a:
        out["from_cm"] = cm(at(transition, True), at(a, False))
    if b:
        out["to_cm"] = cm(at(transition, False), at(b, True))
    return out


if (
    __name__ == "__main__"
):  # python -m steps._connect <version>/rigged_anim.glb "<transition>" <from|-> <to|->
    import sys

    g, t, a, b = sys.argv[1:5]
    print(json.dumps(seam_check(Path(g), t, None if a == "-" else a, None if b == "-" else b)))
