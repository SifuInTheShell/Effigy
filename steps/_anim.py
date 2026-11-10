"""Animation edit maths (IMPLEMENTATION.md §3.2 `anim_edit`, M37): numpy only, no Blender.

Three parts: read a GLB's animations as dense per-bone samples (`read_glb`, `sample_clip`), `evaluate`
a correction layer on them (the same eight steps as ui/src/anim/clip.ts; one fixture,
tests/fixtures/anim_edit/, both pass) and bake the result back into the GLB (`write_glb`).

A clip ("base") is {"fps", "frames": F, "bones": [{"name", "parent": name|None, "rotation"?: (F,4) x,y,z,w,
"translation"?: (F,3), "offset"?: (3,)}]}; `translation` is present for the bones whose channel is animated
(more than two keys), the constant ones are written back as they were; `offset` (M38) is the constant
translation of a bone without a moving one (skeleton space for the foot locks), carried unchanged.
"""

import copy
import itertools
import json
import math
import struct
from pathlib import Path

import numpy as np

from steps._common import StepError

ROT, POS = "rotation", "translation"


# --- quaternions and easing ------------------------------------------------------------------------------


def _s(t):  # smoothstep
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _unit(q):
    return q / np.linalg.norm(q, axis=-1, keepdims=True)


def _lerp(a, b, t):
    return a + (b - a) * np.asarray(t, float)[..., None]


def slerp(a, b, t):
    """Shortest-path slerp, normalised; nlerp when the angle is tiny (dot > 0.9995)."""
    a, b = np.broadcast_arrays(np.asarray(a, float), np.asarray(b, float))
    t = np.asarray(t, float)[..., None]
    d = (a * b).sum(-1, keepdims=True)
    b = np.where(d < 0, -b, b)
    d = np.abs(d)
    th = np.arccos(np.clip(d, 0, 1))
    near = d > 0.9995
    sn = np.where(near, 1.0, np.sin(th))
    wa = np.where(near, 1 - t, np.sin((1 - t) * th) / sn)
    wb = np.where(near, t, np.sin(t * th) / sn)
    return _unit(wa * a + wb * b)


def qmul(a, b):
    ax, ay, az, aw = np.moveaxis(np.asarray(a, float), -1, 0)
    bx, by, bz, bw = np.moveaxis(np.asarray(b, float), -1, 0)
    return np.stack(
        [
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz,
        ],
        -1,
    )


def _consistent(q):
    """Flip quaternions so neighbours are on the same side (q and -q are one rotation)."""
    r = np.where((q[1:] * q[:-1]).sum(-1) < 0, -1.0, 1.0)
    return q * np.concatenate([[1.0], np.cumprod(r)])[:, None]


def _gauss(x, sigma):
    r = math.ceil(3 * sigma)
    k = np.arange(-r, r + 1)
    w = np.exp(-(k**2) / (2 * sigma**2))
    idx = np.clip(np.arange(len(x))[:, None] + k, 0, len(x) - 1)  # edges held
    return (x[idx] * (w / w.sum())[None, :, None]).sum(1)


def _qrot(q, v):
    u = np.asarray(q[:3], float)
    t = 2 * np.cross(u, v)
    return v + q[3] * t + np.cross(u, t)


def _qinv(q):
    return np.asarray(q, float) * [-1, -1, -1, 1]


def _turn(axis, ang):
    return np.append(axis * math.sin(ang / 2), math.cos(ang / 2))


def _axis(*cands):  # the first candidate that is not (nearly) zero, normalised; None: all are
    for c in cands:
        if (n := np.linalg.norm(c)) >= 1e-8:
            return c / n
    return None


_EX, _EY, _ID = np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 0, 1.0])


def _path(par, n):
    """The bones from the root down to n."""
    out = []
    while n is not None:
        out.append(n)
        n = par.get(n)
    return out[::-1]


def _fk(path, rot, pos, off, f):
    """World turns and positions along a root-first path at frame f (skeleton space, M38)."""
    W, p, Ws, ps = _ID, np.zeros(3), [], []
    for n in path:
        t = pos[n][f] if n in pos else off.get(n, np.zeros(3))
        p = p + _qrot(W, t)
        W = qmul(W, rot[n][f]) if n in rot else W
        Ws.append(W)
        ps.append(p)
    return Ws, ps


def _between(u, v):
    """The shortest turn taking unit u to unit v (opposite: a half turn about u x X, else u x Y)."""
    d = u @ v
    if 1 + d < 1e-8:
        return _turn(_axis(np.cross(u, _EX), np.cross(u, _EY)), math.pi)
    return _unit(np.append(np.cross(u, v), 1 + d))


def _two_bone(pw, WU, WL, WB, a, b, c, P):
    """U, L and B's new local turns putting B at P, B keeping its world turn (§3.2 3b); None: nothing to solve."""
    l1, l2 = np.linalg.norm(b - a), np.linalg.norm(c - b)
    if l1 * l2 < 1e-12 or np.linalg.norm(P - a) < 1e-12:
        return None  # a zero-length bone or a target on the hip: nothing to solve
    d = min(max(np.linalg.norm(P - a), abs(l1 - l2) + 1e-6), l1 + l2 - 1e-6)
    u, v = a - b, c - b
    k = _axis(np.cross(u, v), np.cross(u, _EX), np.cross(u, _EY))
    if k is None:
        return None  # the upper bone (nearly) zero long: no knee to turn
    th = math.acos(np.clip(u @ v / (l1 * l2), -1, 1))
    th2 = math.acos(np.clip((l1 * l1 + l2 * l2 - d * d) / (2 * l1 * l2), -1, 1))
    K = _turn(k, th2 - th)
    H = _between(_unit(_qrot(K, v) + b - a), _unit(P - a))
    nU, nL = qmul(H, WU), qmul(H, qmul(K, WL))
    return [_unit(qmul(_qinv(pw), nU)), _unit(qmul(_qinv(nU), nL)), _unit(qmul(_qinv(nL), WB))]


def _lock(rot, pos, off, par, lk, F):
    """One foot lock (§3.2 step 3b): two-bone IK of U > L > B pinning B to its world position at the span's middle.
    M69 `reach` (a share of the leg's length): where the pinned foot is further from the hip than that, the top
    translated bone above it (the hips) moves toward it, the least that brings it back, eased (`_hold_blur` over the
    blend), before the solve."""
    chain = _path(par, lk["bone"])  # root .. U, L, B
    T = _fk(chain, rot, pos, off, (lk["from"] + lk["to"]) // 2)[1][-1]
    span = range(max(0, lk["from"] - lk["blend"]), min(F - 1, lk["to"] + lk["blend"]) + 1)

    def weight(f):
        if f < lk["from"]:
            return float(_s(1 - (lk["from"] - f) / lk["blend"]))
        return 1.0 if f <= lk["to"] else float(_s(1 - (f - lk["to"]) / lk["blend"]))

    hips = next((n for n in chain if n in pos), None)
    if lk.get("reach") and hips is not None:
        need, toward = np.zeros(F), np.zeros(3)
        for f in span:
            ps = _fk(chain, rot, pos, off, f)[1]
            a, b, c = ps[-3:]
            P = c + (T - c) * weight(f)
            excess = np.linalg.norm(P - a) - lk["reach"] * (
                np.linalg.norm(b - a) + np.linalg.norm(c - b)
            )
            if excess > 0:
                need[f] = excess
                toward += (P - a) / np.linalg.norm(P - a) * excess
        if need.any():
            toward /= np.linalg.norm(toward)
            eased = _hold_blur(need, max(lk["blend"], 1))
            up = _path(par, hips)[:-1]
            for f in np.nonzero(eased > 1e-12)[0]:
                Wp = _fk(up, rot, pos, off, f)[0][-1] if up else _ID
                pos[hips][f] = pos[hips][f] + _qrot(_qinv(Wp), toward * eased[f])
    for f in span:
        w = weight(f)
        Ws, ps = _fk(chain, rot, pos, off, f)
        c = ps[-1]
        pw = Ws[-4] if len(Ws) > 3 else _ID
        r = _two_bone(pw, *Ws[-3:], *ps[-3:], c + (T - c) * w)
        if r is not None:
            for n, q in zip(chain[-3:], r, strict=True):
                rot[n][f] = q


def _chain(pw, W, p, P, swivel):
    """M63: the chain's new local turns putting its last bone at P (ui/src/anim/clip.ts ikChain): first a turn about its
    root-tip line by `swivel` degrees, then the two-bone solve for two bones, FABRIK (16 rounds) for more; None: nothing
    to solve. W, p: world turns and positions, the top first; the last bone keeps its world turn."""
    n = len(W) - 1
    W, p = list(W), list(p)
    ax = p[n] - p[0]
    if swivel != 0 and np.linalg.norm(ax) > 1e-12:
        R = _turn(_unit(ax), math.radians(swivel))
        for i in range(n):
            W[i] = _unit(qmul(R, W[i]))
            if i > 0:
                p[i] = p[0] + _qrot(R, p[i] - p[0])
    if n == 2:
        return _two_bone(pw, *W, *p, P)
    L = [np.linalg.norm(p[i + 1] - p[i]) for i in range(n)]
    if min(L) < 1e-12 or np.linalg.norm(P - p[0]) < 1e-12:
        return None
    q = [x.copy() for x in p]
    for _ in range(16):
        q[n] = np.asarray(P, float).copy()
        for i in range(n - 1, -1, -1):
            q[i] = q[i + 1] + _unit(q[i] - q[i + 1]) * L[i]
        q[0] = p[0].copy()
        for i in range(1, n + 1):
            q[i] = q[i - 1] + _unit(q[i] - q[i - 1]) * L[i - 1]
    out, up, cur = [], pw, W[0]
    for i in range(n):
        t = _qrot(_qinv(W[i]), p[i + 1] - p[i])  # the next joint in bone i's own frame
        Wn = _unit(qmul(_between(_unit(_qrot(cur, t)), _unit(q[i + 1] - q[i])), cur))
        out.append(_unit(qmul(_qinv(up), Wn)))
        cur, up = qmul(Wn, qmul(_qinv(W[i]), W[i + 1])), Wn
    out.append(_unit(qmul(_qinv(up), W[n])))
    return out


def _ik_at(keys, x):
    """An IK control's (p, swivel, w) at base frame x (§3.2 step 3c); None: no IK there."""
    ks = sorted(keys, key=lambda k: k["f"])

    def fade(k, d):
        w = (
            k["w"]
            if d == 0
            else k["w"] * float(_s(1 - d / k["falloff"]))
            if d < k["falloff"]
            else 0.0
        )
        return (np.asarray(k["p"], float), k["swivel"], w) if w > 0 else None

    if x <= ks[0]["f"]:
        return fade(ks[0], ks[0]["f"] - x)
    if x >= ks[-1]["f"]:
        return fade(ks[-1], x - ks[-1]["f"])
    a, b = next((a, b) for a, b in itertools.pairwise(ks) if a["f"] <= x <= b["f"])
    t = float(_s((x - a["f"]) / (b["f"] - a["f"])))
    w = a["w"] + (b["w"] - a["w"]) * t
    if w <= 0:
        return None
    p = np.asarray(a["p"], float) + (np.asarray(b["p"], float) - a["p"]) * t
    return p, a["swivel"] + (b["swivel"] - a["swivel"]) * t, w


def _ik(rot, pos, off, par, ctl, F):
    """One IK control (§3.2 step 3c): its bone reaches the keyed target, the `chain` bones above it turning."""
    path, n = _path(par, ctl["bone"]), ctl["chain"]
    space = _path(par, ctl["space"]) if ctl.get("space") else None
    for f in range(F):
        if (at := _ik_at(ctl["keys"], f)) is None:
            continue
        p, swivel, w = at
        Ws, ps = _fk(path, rot, pos, off, f)
        if space:
            Wsp, psp = _fk(space, rot, pos, off, f)
            p = psp[-1] + _qrot(Wsp[-1], p)
        pw = Ws[-n - 2] if len(Ws) > n + 1 else _ID
        r = _chain(pw, Ws[-n - 1 :], ps[-n - 1 :], ps[-1] + (p - ps[-1]) * w, swivel * w)
        if r is not None:
            for name, q in zip(path[-n - 1 :], r, strict=True):
                rot[name][f] = q


# --- M69: Butterworth, mirror, reach ----------------------------------------------------------------------


def _butter(x, sigma):
    """A zero-phase second-order Butterworth low-pass along frames (forward, then backward), its half-power point where
    a Gaussian of `sigma` frames has it (0.1325 / sigma cycles a frame; the two passes put the cut at 0.1652 / sigma, at
    most 0.45), so one slider means the same in both; the ends padded by odd reflection (at most 30 frames), the filter
    started at rest on the first sample. Flatter below the cut than the Gaussian: the jitter goes, a quick gesture keeps
    more of its size."""
    x = np.asarray(x, float)
    N = len(x)
    if N < 3:
        return x.copy()
    K = math.tan(math.pi * min(0.1652 / sigma, 0.45))
    n = 1 / (1 + math.sqrt(2) * K + K * K)
    b0, a1, a2 = K * K * n, 2 * (K * K - 1) * n, (1 - math.sqrt(2) * K + K * K) * n
    P = min(N - 1, 30)
    pad = np.concatenate([2 * x[0] - x[P:0:-1], x, 2 * x[-1] - x[np.arange(N - 2, N - P - 2, -1)]])

    def run(s):
        y = np.empty_like(s)
        x1 = x2 = y1 = y2 = s[0]
        for i in range(len(s)):
            y[i] = b0 * s[i] + 2 * b0 * x1 + b0 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, s[i], y1, y[i]
        return y

    return run(run(pad)[::-1])[::-1][P : P + N]


def _partner(n, names):
    """The bone on the other side ("Left" and "Right" swapped), or n itself (a middle bone, or no partner)."""
    for a, b in (("Left", "Right"), ("Right", "Left")):
        if a in n:
            return m if (m := n.replace(a, b)) in names else n
    return n


def _reflect(q, n):
    """A turn seen in a mirror whose plane has the unit normal n: its axis mirrored, its angle reversed."""
    v = q[..., :3]
    return np.concatenate([2 * (v @ n)[..., None] * n - v, q[..., 3:]], -1)


def _mirror(names, par, rot, pos, off, rest):
    """M69 step 1b, in place: the clip as its mirror image. Each bone's world motion from its rest (skeleton space,
    as the locks' FK sees it: a bone without a rotation channel turns nothing) is reflected across the body's middle
    plane (through the middle of its left/right pairs at rest, the normal from right to left) and put on its
    partner's rest; the root's translation is reflected across the plane. Nothing happens without a left/right pair
    or a rest for every bone."""
    if any(n not in rest for n in names):
        return
    order, seen = [], set()

    def visit(n):
        if n not in seen:
            seen.add(n)
            if par.get(n) in names:
                visit(par[n])
            order.append(n)

    for n in names:
        visit(n)
    Wr, pr = {}, {}
    for n in order:
        up = par.get(n)
        Wp, pp = (Wr[up], pr[up]) if up in Wr else (_ID, np.zeros(3))
        Wr[n] = qmul(Wp, np.asarray(rest[n]["q"], float)) if n in rot else Wp
        pr[n] = pp + _qrot(Wp, np.asarray(rest[n]["t"], float))
    pairs = [(n, m) for n in names if (m := _partner(n, names)) != n and "Left" in n]
    if not pairs:
        return
    side = sum(pr[a] - pr[b] for a, b in pairs)
    if np.linalg.norm(side) < 1e-9:
        return
    nrm = side / np.linalg.norm(side)
    mid = sum(pr[a] + pr[b] for a, b in pairs) / (2 * len(pairs))
    F = len(next(iter({**rot, **pos}.values())))
    W = {}
    for n in order:
        up = par.get(n)
        Wp = W[up] if up in W else np.tile(_ID, (F, 1))
        W[n] = _unit(qmul(Wp, rot[n])) if n in rot else Wp
    # each bone's new world turn: its partner's motion from rest, mirrored, on its own rest
    want = {
        m: qmul(_reflect(qmul(W[n], _qinv(Wr[n])), nrm), Wr[m])
        for n in names
        if (m := _partner(n, names))
    }
    Wn = {}
    for n in order:
        up = par.get(n)
        Wp = Wn[up] if up in Wn else np.tile(_ID, (F, 1))
        if n in rot:
            rot[n] = _unit(qmul(_qinv(Wp), want[n]))
            Wn[n] = want[n]
        else:
            Wn[n] = Wp
    old = {n: v.copy() for n, v in pos.items()}
    for n in pos:
        m = _partner(n, names)
        p = old[m] if m in old else old[n]
        if par.get(n) not in names:  # a root: its place reflected across the middle plane
            p = p - 2 * ((p - mid) @ nrm)[:, None] * nrm
        pos[n] = p


def _hold_blur(need, k):
    """A per-frame need held over ±k frames, blurred (Gaussian, sigma k / 2), never below the need: a correction that
    starts before it is needed and ends after (M67's easing, in frames)."""
    F = len(need)
    held = np.array([need[max(0, f - k) : f + k + 1].max() for f in range(F)])
    return np.maximum(_gauss(held[:, None], max(k / 2, 0.5))[:, 0], need)


# --- evaluate ---------------------------------------------------------------------------------------------


def _curve(x, ks, ident, mix):
    """The layer's value at base frame x from keys [(f, value, falloff)] sorted by f (§3.2 step 3)."""
    f0, v0, fo0 = ks[0]
    if x <= f0:
        d = f0 - x
        if d <= 0:
            return v0
        return mix(ident, v0, _s(1 - d / fo0)) if d < fo0 else ident
    f1, v1, fo1 = ks[-1]
    if x >= f1:
        d = x - f1
        if d <= 0:
            return v1
        return mix(ident, v1, _s(1 - d / fo1)) if d < fo1 else ident
    for (fa, va, _), (fb, vb, _) in itertools.pairwise(ks):
        if fa <= x < fb:
            return mix(va, vb, _s((x - fa) / (fb - fa)))


def _apply_keys(arr, keys, ident, mix):
    ks = sorted(
        (
            (float(k["f"]), np.asarray(k.get("q", k.get("v")), float), float(k.get("falloff", 0)))
            for k in keys
        ),
        key=lambda k: k[0],
    )
    return np.stack([_curve(float(x), ks, ident, mix) for x in range(len(arr))])


def evaluate(base: dict, layer: dict) -> dict:
    """`base` clip + correction `layer` -> the edited clip (same shape, "frames" = new length).
    Order: 1 dense base (given) · 1b mirror · 2 smooth · 3 keys, offsets, 3b feet, 3c ik · 4 trim · 5 in_place · 6 ground ·
    7 loop · 8 speed."""
    fps = base["fps"]
    names = [b["name"] for b in base["bones"]]
    par = {b["name"]: b.get("parent") for b in base["bones"]}
    rot = {b["name"]: np.array(b[ROT], float) for b in base["bones"] if ROT in b}
    pos = {b["name"]: np.array(b[POS], float) for b in base["bones"] if POS in b}
    off = {b["name"]: np.array(b["offset"], float) for b in base["bones"] if "offset" in b}

    def top(n):  # no translated ancestor
        p = par.get(n)
        while p is not None:
            if p in pos:
                return False
            p = par.get(p)
        return True

    tops = [n for n in pos if top(n)]
    if layer.get("mirror"):  # 1b (M69)
        rest = {b["name"]: b["rest"] for b in base["bones"] if "rest" in b}
        _mirror(names, par, rot, pos, off, rest)
    blur = _butter if layer.get("filter") == "butterworth" else _gauss  # M69
    for n, sigma in (layer.get("smooth") or {}).items():  # 2
        if sigma > 0:
            if n in rot:
                rot[n] = _unit(blur(_consistent(rot[n]), sigma))
            if n in pos:
                pos[n] = blur(pos[n], sigma)
    for n, ks in (layer.get("keys") or {}).items():  # 3
        if ks:
            rot[n] = _unit(qmul(rot[n], _apply_keys(rot[n], ks, np.array([0, 0, 0, 1.0]), slerp)))
    for n, ks in (layer.get("offsets") or {}).items():
        if ks:
            pos[n] = pos[n] + _apply_keys(pos[n], ks, np.zeros(3), _lerp)
    for lk in layer.get("feet") or []:  # 3b
        _lock(rot, pos, off, par, lk, base["frames"])
    for ctl in layer.get("ik") or []:  # 3c
        _ik(rot, pos, off, par, ctl, base["frames"])
    if layer.get("trim"):  # 4
        a, b = layer["trim"]
        rot, pos = ({n: v[a : b + 1] for n, v in d.items()} for d in (rot, pos))
    G = len(next(iter({**rot, **pos}.values())))
    if layer.get("in_place") and G > 1:  # 5
        for n in tops:
            pos[n][:, [0, 2]] -= (
                np.arange(G)[:, None] / (G - 1) * (pos[n][-1, [0, 2]] - pos[n][0, [0, 2]])
            )
    for n in tops:  # 6
        pos[n][:, 1] += layer.get("ground", 0)
    n_loop = min(int(layer.get("loop", 0)), G - 1)  # 7
    if n_loop > 0:
        w = _s((np.arange(G - n_loop, G) - (G - n_loop) + 1) / n_loop)
        for n, q in rot.items():
            q[G - n_loop :] = slerp(q[G - n_loop :], q[0], w)
        for n, p in pos.items():
            cols = [0, 1, 2] if n not in tops or layer.get("in_place") else [1]
            p[G - n_loop :, cols] = _lerp(p[G - n_loop :], p[0], w)[:, cols]
    speed = layer.get("speed", 1)  # 8
    if speed != 1:
        Gn = math.floor((G - 1) / speed + 0.5) + 1
        x = np.minimum(np.arange(Gn) * speed, G - 1)
        i = np.minimum(np.floor(x).astype(int), G - 1)
        j, t = np.minimum(i + 1, G - 1), x - i
        rot = {n: slerp(q[i], q[j], t) for n, q in rot.items()}
        pos = {n: _lerp(p[i], p[j], t) for n, p in pos.items()}
        G = Gn
    bones = []
    for n in names:
        b = {"name": n, "parent": par[n]}
        if n in rot:
            b[ROT] = rot[n]
        if n in pos:
            b[POS] = pos[n]
        if n in off:
            b["offset"] = off[n]
        bones.append(b)
    return {"fps": fps, "frames": G, "bones": bones}


# --- GLB read ---------------------------------------------------------------------------------------------

_NCOMP = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}


def read_glb(path) -> tuple[dict, bytes]:
    """(json document, BIN chunk padded to 4 bytes)."""
    data = Path(path).read_bytes()
    if data[:4] != b"glTF":
        raise StepError("failed", f"{path}: not a GLB")
    doc, blob, pos = None, b"", 12
    while pos < len(data):
        n, kind = struct.unpack_from("<II", data, pos)
        chunk = data[pos + 8 : pos + 8 + n]
        if kind == 0x4E4F534A:
            doc = json.loads(chunk)
        elif kind == 0x004E4942 and not blob:
            blob = chunk
        pos += 8 + n
    return doc, blob + b"\0" * (-len(blob) % 4)


def _acc(doc, blob, i) -> np.ndarray:
    a = doc["accessors"][i]
    if "sparse" in a or "bufferView" not in a or a["componentType"] != 5126:
        raise StepError(
            "failed", f"animation accessor {i}: only plain float accessors are supported"
        )
    bv = doc["bufferViews"][a["bufferView"]]
    if "EXT_meshopt_compression" in bv.get("extensions", {}):
        raise StepError("failed", "animation data in EXT_meshopt_compression is not supported")
    n, c = a["count"], _NCOMP[a["type"]]
    off, stride = bv.get("byteOffset", 0) + a.get("byteOffset", 0), bv.get("byteStride") or 4 * c
    if stride == 4 * c:
        return np.frombuffer(blob, "<f4", n * c, off).reshape(n, c).astype(float)
    raw = np.frombuffer(blob, np.uint8, (n - 1) * stride + 4 * c, off)
    return np.stack([raw[k * stride : k * stride + 4 * c].view("<f4") for k in range(n)]).astype(
        float
    )


def _parents(doc) -> dict[int, int]:
    return {c: i for i, n in enumerate(doc["nodes"]) for c in n.get("children", [])}


def _channels(doc, blob, anim):
    """Yield (channel, node name, path, interpolation, times, values) of an animation."""
    for ch in anim["channels"]:
        s = anim["samplers"][ch["sampler"]]
        interp = s.get("interpolation", "LINEAR")
        if interp == "CUBICSPLINE":
            raise StepError("failed", f"animation {anim.get('name')}: CUBICSPLINE is not supported")
        yield (
            ch,
            doc["nodes"][ch["target"]["node"]].get("name", f"node_{ch['target']['node']}"),
            ch["target"]["path"],
            interp,
            _acc(doc, blob, s["input"])[:, 0],
            _acc(doc, blob, s["output"]),
        )


def _sample(times, vals, interp, u, is_rot):
    if interp == "STEP" or len(times) == 1:
        return vals[np.clip(np.searchsorted(times, u + 1e-6, "right") - 1, 0, len(times) - 1)]
    i = np.clip(np.searchsorted(times, u, "right") - 1, 0, len(times) - 2)
    w = np.clip((u - times[i]) / (times[i + 1] - times[i]), 0, 1)
    w = np.where(w < 1e-4, 0.0, np.where(w > 1 - 1e-4, 1.0, w))  # a sample on a key is the key
    return slerp(vals[i], vals[i + 1], w) if is_rot else _lerp(vals[i], vals[i + 1], w)


def clip_index(doc, name):
    ix = [i for i, a in enumerate(doc.get("animations", [])) if a.get("name") == name]
    if len(ix) != 1:
        raise StepError(
            "bad_params", f"clip {name!r}: {'not found' if not ix else 'name is not unique'}"
        )
    return ix[0]


def sample_clip(doc, blob, name, fps) -> dict:
    """A clip's rotations and animated translations sampled at f/fps for f in 0..F-1 (§3.2 step 1)."""
    anim = doc["animations"][clip_index(doc, name)]
    chans = list(_channels(doc, blob, anim))
    dur = max(float(c[4].max()) for c in chans)
    F = math.floor(dur * fps + 0.5) + 1
    u = np.arange(F) / fps
    par, nodes = _parents(doc), {}
    for ch, node, path, interp, t, v in chans:
        # a translation that moves (by its values: two keys can move a root from A to B) is sampled
        if path == "rotation" or (path == "translation" and np.ptp(v, axis=0).max() > 1e-7):
            nodes.setdefault(ch["target"]["node"], {"name": node})[
                ROT if path == "rotation" else POS
            ] = _sample(t, v, interp, u, path == "rotation")
        elif path == "translation":
            nodes.setdefault(ch["target"]["node"], {"name": node})
    for k, b in nodes.items():  # M38: the constant translation of a bone without a moving one
        if POS not in b:
            const = next(
                (c[5][0] for c in chans if c[0]["target"] == {"node": k, "path": "translation"}),
                doc["nodes"][k].get("translation", [0, 0, 0]),
            )
            b["offset"] = np.asarray(const, float)
    for k, b in nodes.items():  # M69: its node's rest (the mirror's reference)
        node = doc["nodes"][k]
        b["rest"] = {
            "q": node.get("rotation", [0, 0, 0, 1]),
            "t": node.get("translation", [0, 0, 0]),
        }
    if len({b["name"] for b in nodes.values()}) != len(nodes):
        raise StepError("failed", f"clip {name!r}: bone names are not unique")
    for k, b in nodes.items():
        p = par.get(k)
        while p is not None and p not in nodes:
            p = par.get(p)
        b["parent"] = nodes[p]["name"] if p is not None else None
    return {"fps": fps, "frames": F, "bones": list(nodes.values())}


# --- GLB write --------------------------------------------------------------------------------------------


def write_glb(doc, blob, edits: dict, fps: float) -> bytes:
    """The GLB with each edited clip's channels re-pointed to new dense accessors in an appended BIN tail.
    edits: {clip name: evaluate() result}. Everything else keeps its bytes.
    ponytail: old animation accessors stay in the file; compact when size matters."""
    doc, out = copy.deepcopy(doc), bytearray(blob)

    def add(arr, typ, minmax=False):
        a = np.ascontiguousarray(arr, "<f4")
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(out), "byteLength": a.nbytes})
        out.extend(a.tobytes())
        acc = {
            "bufferView": len(doc["bufferViews"]) - 1,
            "componentType": 5126,
            "count": len(a),
            "type": typ,
        }
        if minmax:
            acc["min"], acc["max"] = [float(a.min())], [float(a.max())]
        doc["accessors"].append(acc)
        return len(doc["accessors"]) - 1

    for name, res in edits.items():
        anim = doc["animations"][clip_index(doc, name)]
        bones, G = {b["name"]: b for b in res["bones"]}, res["frames"]
        dense_t = add(np.arange(G) / fps, "SCALAR", True)
        step_t = add([0, (G - 1) / fps], "SCALAR", True)
        samplers = []
        for ch, node, path, _, t, v in _channels(doc, blob, anim):
            b, typ = bones.get(node, {}), "VEC4" if path == "rotation" else "VEC3"
            new = b.get(path)
            if path == "scale" and len(t) > 2:
                raise StepError("failed", f"clip {name!r}: animated scale is not supported")
            if path not in ("rotation", "translation", "scale"):
                raise StepError("failed", f"clip {name!r}: {path} channels are not supported")
            if new is None or np.ptp(new, axis=0).max() < 1e-7:  # constant: its (new) value held
                s = {
                    "input": step_t,
                    "output": add(np.tile(v[0] if new is None else new[0], (2, 1)), typ),
                    "interpolation": "STEP",
                }
            else:
                s = {"input": dense_t, "output": add(new, typ), "interpolation": "LINEAR"}
            ch["sampler"] = len(samplers)
            samplers.append(s)
        anim["samplers"] = samplers
    doc["buffers"][0]["byteLength"] = len(out)
    return pack(doc, bytes(out))


def turns(path) -> dict[str, float]:
    """Per clip, the largest turn (degrees) any bone makes against its own first key. A retarget whose bone map missed
    writes a clip that plays and never moves (the Mr. Mak review's silent-clip gate); a real clip turns tens of degrees."""
    doc, blob = read_glb(path)
    out = {}
    for a in doc.get("animations", []):
        turn = 0.0
        for _, _, kind, _, _, vals in _channels(doc, blob, a):
            if kind == "rotation" and len(vals) > 1:
                q = vals / np.linalg.norm(vals, axis=1, keepdims=True)
                turn = max(
                    turn, float(np.degrees(2 * np.arccos(np.abs(q @ q[0]).clip(0, 1).min())))
                )
        out[a.get("name", "")] = round(turn, 2)
    return out


def pack(doc: dict, blob: bytes) -> bytes:
    """A GLB from its JSON document and its BIN chunk (padded to 4 bytes)."""
    j = json.dumps(doc, separators=(",", ":")).encode()
    j += b" " * (-len(j) % 4)
    blob += b"\0" * (-len(blob) % 4)
    return (
        struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(j) + 8 + len(blob))
        + struct.pack("<II", len(j), 0x4E4F534A)
        + j
        + struct.pack("<II", len(blob), 0x004E4942)
        + blob
    )
