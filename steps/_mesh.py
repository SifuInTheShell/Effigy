"""Mesh facts and GLB IO shared by clean/remesh/uv/validate (IMPLEMENTATION.md §3.9). numpy only."""

import itertools

import numpy as np


def components(n_vertices: int, faces: np.ndarray) -> np.ndarray:
    """Connected-component label per vertex (union-find by min-label hooking + pointer jumping)."""
    parent = np.arange(n_vertices)
    a = np.concatenate([faces[:, 0], faces[:, 1], faces[:, 2]])
    b = np.concatenate([faces[:, 1], faces[:, 2], faces[:, 0]])
    while True:
        pa, pb = parent[a], parent[b]
        low = np.minimum(pa, pb)
        before = parent.copy()
        np.minimum.at(parent, pa, low)
        np.minimum.at(parent, pb, low)
        while not np.array_equal(parent, parent[parent]):
            parent = parent[parent]
        if np.array_equal(parent, before):
            return parent


def mesh_report(vertices, faces) -> dict:
    v, f = np.asarray(vertices, np.float64), np.asarray(faces, np.int64)
    edges = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
    _, uses = np.unique(edges, axis=0, return_counts=True)
    area = 0.5 * np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1)
    used = np.unique(f)
    labels = components(len(v), f)[used]
    lo, hi = v[used].min(0), v[used].max(0)
    rep = {
        "vertices": len(used),
        "faces": len(f),
        "components": len(np.unique(labels)),
        "boundary_edges": int((uses == 1).sum()),
        "non_manifold_edges": int((uses > 2).sum()),
        "degenerate_faces": int((area < 1e-12).sum()),
        "bbox": [lo.round(5).tolist(), hi.round(5).tolist()],
        "height_m": round(float(hi[1] - lo[1]), 5),  # library frame: Y up, metres
    }
    rep["watertight"] = rep["boundary_edges"] == 0 and rep["non_manifold_edges"] == 0
    # holes through a closed surface (a donut has one): Euler V - E + F = 2 per piece, less 2 per tunnel.
    # A generated cat's black back came out a shell with tunnels that no open edge showed.
    euler = len(used) - len(np.unique(edges, axis=0)) + len(f)
    rep["tunnels"] = int((2 * rep["components"] - euler) // 2) if rep["watertight"] else None
    return rep


def uv_overlap(uv, faces, size: int = 512) -> float:
    """The share of the texels the faces cover that two or more faces claim: stacked or mirrored islands paint
    through each other (the owner's agent, 2026-10-11: cap faces over 70 % of the atlas, a refine painted the back
    views onto the front). Rasterised at size², one face's bounding box at a time (ponytail: a Python loop over
    the faces, ~1 s per 100k)."""
    count = np.zeros((size, size), np.int32)
    tris = np.asarray(uv, np.float64)[np.asarray(faces, np.int64)] * size
    for t in tris:
        lo = np.floor(t.min(0)).astype(int).clip(0, size - 1)
        hi = np.ceil(t.max(0)).astype(int).clip(0, size - 1)
        d = t[1:] - t[0]
        det = d[0, 0] * d[1, 1] - d[1, 0] * d[0, 1]
        if abs(det) < 1e-9:
            continue
        xs, ys = np.meshgrid(np.arange(lo[0], hi[0] + 1), np.arange(lo[1], hi[1] + 1))
        q = np.stack([xs + 0.5, ys + 0.5], -1).reshape(-1, 2) - t[0]
        a = (q[:, 0] * d[1, 1] - q[:, 1] * d[1, 0]) / det
        b = (d[0, 0] * q[:, 1] - d[0, 1] * q[:, 0]) / det
        inside = (a > 0) & (b > 0) & (a + b < 1)  # strict: a texel on a shared edge is nobody's
        count[ys.reshape(-1)[inside], xs.reshape(-1)[inside]] += 1
    return float((count > 1).sum() / max((count > 0).sum(), 1))


def uv_report(uv, faces, vertices) -> dict:
    """Islands, bounds, overlap and texel-density spread (3D area per UV area, relative to the median)."""
    uv, f, v = (
        np.asarray(uv, np.float64),
        np.asarray(faces, np.int64),
        np.asarray(vertices, np.float64),
    )
    a3 = 0.5 * np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1)
    e1, e2 = uv[f[:, 1]] - uv[f[:, 0]], uv[f[:, 2]] - uv[f[:, 0]]
    a2 = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
    ok = (a3 > 1e-12) & (a2 > 1e-14)
    density = np.sqrt(a2[ok] / a3[ok])  # texture-space length per metre
    med = float(np.median(density)) if ok.any() else 0.0
    return {
        "islands": len(np.unique(components(len(uv), f)[np.unique(f)])),
        "in_0_1": bool(((uv >= 0) & (uv <= 1)).all()),
        "degenerate_uv_faces": int((~ok).sum()),
        "texel_density_median": round(med, 5),
        "texel_density_p10_p90": [
            round(float(x) / med, 3) for x in np.percentile(density, [10, 90])
        ]
        if med
        else None,
        "uv_area_used": round(float(a2.sum()), 4),
        "overlap": round(uv_overlap(uv, f), 4),
    }


def detaper(v: np.ndarray, bands: int = 12) -> tuple[np.ndarray, dict | None]:
    """A flat prop generated from a picture in perspective keeps the taper: one end shorter, the foot rising,
    the far boards narrower (the owner's agent, 2026-10-11: a fence 1.50 m at one end, 1.07 m at the other).
    Along the long side (X or Z): the bottom and top lines fitted through the bands' 2nd and 98th percentiles
    of height; every column stretched to the tall end's height about its bottom line, the length un-foreshortened
    by the same factor (the integral of H / h, a log for a linear h). The depth stays as the reconstruction made it:
    even (the owner's agent, 2026-10-13: scaled with the height, a 0.17 m hoarding came out 0.24 m thick at its short
    end, a wedge). The tall end keeps its size. (v, None) when it is not a flat panel along X or Z (depth over 35 % of the
    length: not one, or still turned; straighten it first), the taper is under 3 % or there is too little to
    measure. ponytail: a linear taper, the picture's; a curved one stays."""
    axis = 0 if np.ptp(v[:, 0]) >= np.ptp(v[:, 2]) else 2
    x, y, z = v[:, axis], v[:, 1], v[:, 2 - axis]
    if np.ptp(z) > 0.35 * np.ptp(x):
        return v, None
    lo, hi = x.min(), x.max()
    edges = np.linspace(lo, hi, bands + 1)
    xs, bot, top = [], [], []
    for a, b in itertools.pairwise(edges):
        sel = (x >= a) & (x <= b)
        if sel.sum() >= 50:
            xs.append((a + b) / 2)
            bot.append(np.percentile(y[sel], 2))
            top.append(np.percentile(y[sel], 98))
    if len(xs) < 4:
        return v, None
    (b1, b0), (t1, t0) = np.polyfit(xs, bot, 1), np.polyfit(xs, top, 1)
    s = t1 - b1  # the height's slope along x

    def h(u):
        return (t0 - b0) + s * u

    if min(h(lo), h(hi)) <= 0:
        return v, None
    big = max(h(lo), h(hi))
    taper = 1 - min(h(lo), h(hi)) / big
    if taper < 0.03:
        return v, None
    x0 = hi if h(hi) >= h(lo) else lo  # the tall end keeps its size
    k = big / h(x)
    xn = x0 + big / s * np.log(h(x) / h(x0)) if abs(s) > 1e-9 else x
    yn = (y - (b0 + b1 * x)) * k + (b0 + b1 * x0)
    out = v.copy()
    out[:, axis], out[:, 1] = xn, yn
    info = {
        "taper_pct": round(float(100 * taper), 1),
        "length_gain_pct": round(float(100 * (np.ptp(xn) / np.ptp(x) - 1)), 1),
    }
    return out, info


def read_glb(path):
    """(vertices, faces, glTF uvs or None) of a GLB's meshes, merged; positions in its own frame."""
    import trimesh

    m = trimesh.load(path, force="mesh", process=False)
    uv = getattr(m.visual, "uv", None)
    if uv is not None:
        uv = np.asarray(uv, np.float64).copy()
        uv[:, 1] = 1 - uv[:, 1]  # trimesh keeps OpenGL UVs; ours are glTF's
    return np.asarray(m.vertices), np.asarray(m.faces), uv


def weld(vertices, faces):
    """One vertex per position: glTF splits vertices at UV seams, which topology (and CuMesh, which pulls
    the seams apart into cracks when it simplifies) would otherwise see as holes."""
    v, inv = np.unique(np.asarray(vertices), axis=0, return_inverse=True)
    return v, inv.reshape(-1)[np.asarray(faces)]


def textured(glb) -> bool:
    """The GLB's JSON chunk (right after the 20-byte header) lists images: it carries textures."""
    import json

    with open(glb, "rb") as f:
        n = int.from_bytes(f.read(20)[12:16], "little")
        return bool(json.loads(f.read(n)).get("images"))


def smooth_normals(vertices, faces) -> np.ndarray:
    """Smooth vertex normals [V,3]: area-weighted face normals summed per position, so the copies of a vertex
    split at a UV seam share one (no shading crease there); a vertex of no face gets +Y."""
    v, f = np.asarray(vertices, np.float64), np.asarray(faces)
    fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])  # length = 2 × area
    _, at = np.unique(np.round(v, 9), axis=0, return_inverse=True)
    at = at.ravel()
    acc = np.zeros((at.max() + 1, 3))
    for k in range(3):
        np.add.at(acc, at[f[:, k]], fn)
    n = acc[at]
    length = np.linalg.norm(n, axis=1, keepdims=True)
    return np.where(length > 1e-20, n / length.clip(1e-20), [[0.0, 1.0, 0.0]])


def write_glb(path, vertices, faces, uv=None) -> None:
    """Untextured GLB (geometry, optional glTF UVs, smooth normals: without NORMAL a viewer shades flat);
    textures come from bake."""
    import trimesh

    m = trimesh.Trimesh(
        np.asarray(vertices),
        np.asarray(faces),
        vertex_normals=smooth_normals(vertices, faces),
        process=False,
    )
    if uv is not None:
        gl = np.asarray(uv, np.float64).copy()
        gl[:, 1] = 1 - gl[:, 1]
        m.visual = trimesh.visual.TextureVisuals(
            uv=gl,
            material=trimesh.visual.material.PBRMaterial(metallicFactor=0.0, roughnessFactor=0.9),
        )
    m.export(path, file_type="glb")


def fill_holes(m, max_hole_perimeter: float) -> None:
    """CuMesh `fill_holes`, then every added hole-centre vertex set to the mean of its ring (what upstream
    computes). Our sm_120 Windows build returned a few centres with z ≈ 1e17 on a 2.9M-vertex TRELLIS
    mesh (small repros were fine), which collapsed the whole frame. ponytail: workaround here, not in
    the wheel; drop it when a CuMesh rebuild passes the same mesh."""
    import torch

    n = m.num_vertices
    m.fill_holes(max_hole_perimeter=max_hole_perimeter)
    v, f = m.read()
    if v.shape[0] == n:
        return
    f = f.long()
    fan = f[(f >= n).any(1)]  # each fan face: two ring vertices + one new centre
    old = (fan < n).float()
    centre = fan.max(1).values - n
    ring = (v[fan.clamp(max=n - 1)] * old[..., None]).sum(1)
    total = torch.zeros_like(v[n:]).index_add_(0, centre, ring)
    count = torch.zeros(v.shape[0] - n, device=v.device).index_add_(0, centre, old.sum(1))
    v = v.clone()
    v[n:] = total / count.clamp(min=1)[:, None]
    m.init(v, f.int())


def spread_votes(faces, votes, rounds: int = 200):
    """votes per face (+1 keep, -1 flip, 0 undecided) → final decision, undecided faces following their
    decided neighbours: across an edge both faces wind consistently (they keep or flip together) or not
    (one flips). Repeated until nothing changes; faces no decision reaches stay 0 (keep)."""
    from scipy.sparse import coo_matrix

    f = np.asarray(faces, np.int64)
    n_faces, base = len(f), int(f.max()) + 1
    e = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    owner = np.tile(np.arange(n_faces), 3)
    undirected = np.minimum(e[:, 0], e[:, 1]) * base + np.maximum(e[:, 0], e[:, 1])
    order = np.argsort(undirected, kind="stable")
    u, o, d = undirected[order], owner[order], e[order, 0]
    pair = np.flatnonzero(u[1:] == u[:-1])  # neighbours sharing an edge (consecutive after sorting)
    a, b = o[pair], o[pair + 1]
    sign = np.where(d[pair] == d[pair + 1], -1.0, 1.0)  # same direction = opposite winding
    adj = coo_matrix(
        (np.r_[sign, sign], (np.r_[a, b], np.r_[b, a])), shape=(n_faces, n_faces)
    ).tocsr()
    s = np.sign(np.asarray(votes, float))
    for _ in range(rounds):
        open_ = s == 0
        pull = adj @ s
        take = open_ & (pull != 0)
        if not take.any():
            break
        s[take] = np.sign(pull[take])
    return s


def inward_faces(v, f, bvh=None):
    """Bool [F] (GPU): the faces that clearly face inward: a ray against the face's normal escapes the
    mesh and one along it doesn't. Undecided faces (both or neither ray escapes, e.g. the anvil's waist
    between horn and base) follow their decided neighbours (spread_votes). v, f: torch tensors."""
    import cumesh
    import torch

    f = f.long()
    tri = v[f]
    c = tri.mean(1)
    n = torch.nn.functional.normalize(
        torch.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0], dim=1), dim=1
    )
    n = torch.nan_to_num(n)  # a degenerate face has no normal: its rays go nowhere, its vote is 0
    bvh = bvh or cumesh.cuBVH(v, f)
    eps = 2e-4  # a fifth of a 1024³ voxel: starts past the face itself
    escapes_front = bvh.ray_trace(c + n * eps, n)[1] < 0
    escapes_back = bvh.ray_trace(c - n * eps, -n)[1] < 0
    votes = (escapes_front.int() - escapes_back.int()).cpu().numpy()  # +1 out, -1 in, 0 undecided
    return torch.from_numpy(spread_votes(f.cpu().numpy(), votes) < 0).to(f.device)


def face_outward(m) -> None:
    """Turn every face that clearly faces inward (inward_faces). m: a CuMesh (GPU). CuMesh's
    unify_face_orientations picks sides arbitrarily, and TRELLIS meshes mix thin sheets with double
    shells, so one consistently wound surface can face out here and in there (the bench barrel: 13 %
    inward); per face, not per patch."""
    v, f = m.read()
    f = f.long()
    inward = inward_faces(v, f)
    if inward.any():
        f[inward] = f[inward].flip(1)
        m.init(v, f.int())


def orient(m, trust: bool) -> None:
    """Faces outward. trust: the winding is consistent (a dual-contouring rebuild): it's kept and only a
    part that's inside out as a whole (negative volume) is flipped; face_outward's ray votes flipped
    correct faces on a rebuilt robot's thin plates (holes in the render). Else (raw TRELLIS: patches
    wound at random) face_outward."""
    import torch

    if not trust:
        return face_outward(m)
    v, f = m.read()
    vn, fn = v.cpu().numpy(), f.cpu().numpy().astype(np.int64)
    label = components(len(vn), fn)[fn[:, 0]]
    t = vn[fn]
    vol = np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2]))  # 6x signed volume per face
    flip = torch.from_numpy(np.bincount(label, weights=vol, minlength=len(vn))[label] < 0).to(
        f.device
    )
    if flip.any():
        f = f.clone()
        f[flip] = f[flip].flip(1)
        m.init(v, f)


def drop_small_parts(m, min_area: float) -> None:
    """Remove connected parts smaller than min_area from a CuMesh, parts joined by shared vertices.
    CuMesh's remove_small_connected_components only follows manifold edges: on a noisy surface (Pixal3D's
    bark) it saw thousands of "parts" and deleted 91 % of the faces, i.e. the holes in the Faithful
    bench meshes."""
    import torch

    v, f = m.read()
    vn, fn = v.cpu().numpy(), f.cpu().numpy().astype(np.int64)
    tri = vn[fn]
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    label = components(len(vn), fn)[fn[:, 0]]
    keep = np.bincount(label, weights=area, minlength=len(vn))[label] >= min_area
    if keep.all():
        return
    m.init(v, f[torch.from_numpy(keep).to(f.device)])


def fill_loops(v: np.ndarray, f: np.ndarray, max_perimeter: float) -> tuple[np.ndarray, np.ndarray]:
    """Fill the holes CuMesh's fill_holes leaves, the pinched ones (two holes sharing a vertex: a
    figure 8): the boundary half-edges are walked into simple cycles (each vertex hands its outgoing
    edges out one by one), and every cycle up to max_perimeter gets a fan around a new middle vertex,
    wound like the faces around it. v, f welded."""
    f = np.asarray(f, np.int64)
    d = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    key = np.sort(d, 1)
    _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    bnd = d[cnt[inv.reshape(-1)] == 1][:, ::-1]  # a hole runs against the faces next to it
    out: dict[int, list[int]] = {}
    for a, b in bnd:
        out.setdefault(int(a), []).append(int(b))
    new_v, new_f = [], []
    while out:
        start = next(iter(out))
        cyc, x = [start], start
        while True:
            y = out[x].pop()
            if not out[x]:
                del out[x]
            if y == start:
                break
            if y in cyc:  # a pinch: close the inner cycle, keep walking the outer one
                i = cyc.index(y)
                inner, cyc = cyc[i:], cyc[: i + 1]
                _fan(v, inner, max_perimeter, len(v) + len(new_v), new_v, new_f)
            else:
                cyc.append(y)
            x = y
            if x not in out:
                break
        _fan(v, cyc, max_perimeter, len(v) + len(new_v), new_v, new_f)
    if not new_v:
        return v, f
    return np.r_[v, np.array(new_v)], np.r_[f, np.array(new_f, np.int64)]


def _fan(v, cyc, max_perimeter, centre, new_v, new_f) -> None:
    if len(cyc) < 3:
        return
    p = v[cyc]
    if np.linalg.norm(p - np.roll(p, -1, 0), axis=1).sum() > max_perimeter:
        return
    new_v.append(p.mean(0))
    new_f.extend([[cyc[i], cyc[(i + 1) % len(cyc)], centre] for i in range(len(cyc))])


def _winding(v, f, pts) -> np.ndarray:
    """steps._rig.winding in torch, on the GPU when there is one, a block of points at a time: the numpy
    one takes a point at a time, and drop_enclosed_parts asks for every part's probes against the rest
    (the street brawler, 2026-10-14: 25 000 probes over 190 000 faces, 32 ms each, 13 minutes of clean)."""
    import torch

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    tri = torch.as_tensor(np.asarray(v)[np.asarray(f)], dtype=torch.float64, device=dev)
    # points × faces per block: ≈ 0.4 GB of temporaries
    step = max(1, 2_000_000 // max(1, len(tri)))
    out = [np.empty(0)]
    for s in range(0, len(pts), step):
        p = torch.as_tensor(pts[s : s + step], dtype=torch.float64, device=dev)[:, None]
        a, b, c = (tri[None, :, k] - p for k in range(3))
        la, lb, lc = (x.norm(dim=2) for x in (a, b, c))
        num = (a * torch.linalg.cross(b, c)).sum(2)
        den = la * lb * lc + (a * b).sum(2) * lc + (b * c).sum(2) * la + (c * a).sum(2) * lb
        out.append((torch.atan2(num, den).sum(1) / (2 * torch.pi)).cpu().numpy())
    return np.concatenate(out)


def drop_enclosed_parts(m, near: float = 0.0) -> int:
    """Remove connected parts that sit inside another part (every vertex of the part, up to 2 000 of them
    taken evenly plus its six extremes, has a winding number > 0.5 for the rest of the mesh): bubbles a
    dual-contouring rebuild leaves in the body, invisible but counted as separate parts and triangles.
    Returns how many went. The mean vertex alone was the test before, and a ring around another part (a
    torus around a sphere, a bracelet around an arm) has its mean inside that part: it lost every face
    (code review 2026-10-05); the six extremes alone next, and a sphere whose extremes sit inside a tilted
    torus while its sides poke out through the ring's holes lost every face (audit 2026-10-10, PIPE-001).
    A part that crosses another is kept: uncertain geometry stays. `near` (clean's rebuild voxel): a probe
    outside but within `near` of the rest's surface counts as inside, since the rebuild's inner copy of a
    solid pokes through the outer one by a hair here and there (the bollard, agent 2026-10-13: 2 of 2 004
    probes, 0.15 mm at a 2 mm voxel) and was kept whole. Smallest area first, so of two such shells the
    inner one goes."""
    import torch

    v, f = m.read()
    vn, fn = v.cpu().numpy().astype(np.float64), f.cpu().numpy().astype(np.int64)
    label = components(len(vn), fn)[fn[:, 0]]
    parts = np.unique(label)
    if len(parts) < 2:
        return 0
    tri = vn[fn]
    area = np.bincount(
        label, np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    )
    drop = np.zeros(len(fn), bool)
    for p in parts[np.argsort(area[parts])]:
        mine = label == p
        pv = vn[np.unique(fn[mine])]
        extremes = np.unique(np.r_[pv.argmin(0), pv.argmax(0)])
        sample = np.unique(np.linspace(0, len(pv) - 1, min(len(pv), 2000)).astype(int))
        probes = pv[np.union1d(extremes, sample)]
        rest = fn[~mine & ~drop]
        out = np.abs(_winding(vn, rest, probes)) <= 0.5
        if near > 0 and out.any() and len(rest) > 8:  # cuBVH wants more than 8 triangles
            import cumesh

            d = cumesh.cuBVH(vn.astype(np.float32), rest.astype(np.int32)).unsigned_distance(
                torch.from_numpy(probes[out])
            )[0]
            out[out] = d.cpu().numpy() > near
        if not out.any():
            drop |= mine
    if drop.any():
        m.init(v, f[torch.from_numpy(~drop).to(f.device)])
    return len(np.unique(label[drop]))


def make_manifold(m, rounds: int = 4, close: float = 0.0) -> None:
    """No edge shared by more than two faces, as the welded mesh sees it: CuMesh's repair_non_manifold_edges
    splits vertices, and every later step re-welds by position (UV seams), which brought 5-85 such edges
    back (M4 acceptance: 0). Instead: weld, drop the extra faces on such edges, repeat (a few faces per
    10k-tri mesh, >= 99.9 % of the area kept). close > 0: the holes that left (perimeter up to `close`)
    are filled afterwards and checked once more (the robot's 12k mesh: 418 open edges)."""
    import torch

    for _ in range(rounds):
        v, f = m.read()
        wv, wf = weld(v.cpu().numpy(), f.cpu().numpy())
        tri = wv[wf]
        area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
        distinct = (wf[:, 0] != wf[:, 1]) & (wf[:, 1] != wf[:, 2]) & (wf[:, 0] != wf[:, 2])
        wf = wf[(area > 1e-12) & distinct]  # welding can collapse a face: zero area, no normal
        done = mesh_report(wv, wf)["non_manifold_edges"] == 0
        m.init(
            torch.tensor(wv, dtype=torch.float32, device=v.device),
            torch.tensor(wf, dtype=torch.int32, device=f.device),
        )
        if done:
            break
        m.remove_non_manifold_faces()
    if close > 0:  # fill, re-check; a fill can make a few edges non-manifold again: up to 3 rounds
        import torch

        for _ in range(3):
            fill_holes(m, close)
            v, f = m.read()
            wv, wf = fill_loops(*weld(v.cpu().numpy(), f.cpu().numpy()), close)  # the pinched ones
            m.init(
                torch.tensor(wv, dtype=torch.float32, device=v.device),
                torch.tensor(wf, dtype=torch.int32, device=f.device),
            )
            make_manifold(m, rounds)
            v, f = m.read()
            if mesh_report(*weld(v.cpu().numpy(), f.cpu().numpy()))["boundary_edges"] == 0:
                break


def _by_size(v: np.ndarray, f: np.ndarray, limit: int) -> list[tuple[np.ndarray, bool]]:
    """(faces, closed) per connected component, largest first, at most `limit`; closed = no boundary edge."""
    lab = components(len(v), f)[f[:, 0]]
    out = []
    for c in np.argsort(-np.bincount(lab))[:limit]:
        fc = f[lab == c]
        if not len(fc):
            break
        e = np.sort(np.concatenate([fc[:, [0, 1]], fc[:, [1, 2]], fc[:, [2, 0]]]), axis=1)
        _, n = np.unique(e, axis=0, return_counts=True)
        out.append((fc, bool((n != 1).all())))
    return out


def _crossings(tri: np.ndarray, pts: np.ndarray, axis: int, sign: int, tol: float) -> np.ndarray:
    """Per point: how many triangles the ray from it along `sign` × `axis` crosses more than `tol` ahead
    (the projected barycentric test of steps/_rig.hits, many points at once). ponytail: O(points × faces)
    in chunks of 16 points."""
    a, b = [i for i in range(3) if i != axis]
    p0, p1, p2 = (tri[:, k][:, [a, b]] for k in range(3))
    d = (p1[:, 1] - p2[:, 1]) * (p0[:, 0] - p2[:, 0]) + (p2[:, 0] - p1[:, 0]) * (
        p0[:, 1] - p2[:, 1]
    )
    ok = np.abs(d) > 1e-18
    p0, p1, p2, tri, d = p0[ok], p1[ok], p2[ok], tri[ok], d[ok]
    lo, hi = tri[:, :, [a, b]].min(1), tri[:, :, [a, b]].max(1)
    out = np.zeros(len(pts), int)
    for s in range(0, len(pts), 16):
        q = pts[s : s + 16]
        near = ((lo <= q[:, [a, b]].max(0)) & (hi >= q[:, [a, b]].min(0))).all(1)  # the chunk's box
        t0, t1, t2, tt, dd = p0[near], p1[near], p2[near], tri[near], d[near]
        qx, qy = q[:, a, None], q[:, b, None]
        w0 = (
            (t1[:, 1] - t2[:, 1]) * (qx - t2[:, 0]) + (t2[:, 0] - t1[:, 0]) * (qy - t2[:, 1])
        ) / dd
        w1 = (
            (t2[:, 1] - t0[:, 1]) * (qx - t2[:, 0]) + (t0[:, 0] - t2[:, 0]) * (qy - t2[:, 1])
        ) / dd
        w2 = 1 - w0 - w1
        z = w0 * tt[:, 0, axis] + w1 * tt[:, 1, axis] + w2 * tt[:, 2, axis]
        hit = (w0 >= 0) & (w1 >= 0) & (w2 >= 0) & (sign * (z - q[:, axis, None]) > tol)
        for i in range(len(q)):  # a ray through a shared edge hits both triangles: one crossing
            zs = np.sort(z[i][hit[i]])
            out[s + i] = len(zs) - int((np.diff(zs) < 1e-9).sum()) if len(zs) else 0
    return out


def self_intersection(v, f, comps=None, samples: int = 2000) -> dict | None:
    """Ray parity on points a hair outside the surface (after img2threejs's self_intersection gate,
    Apache-2.0): such a point that counts an odd number of its own component's triangles along the axis
    its face faces most is inside that component, so the surface folded through itself there (an eye
    socket pushed out through the back of the skull: manifold, watertight and the tunnel count all pass
    it). Per closed component, "outside" by the component's signed volume (a piece wound inward, or a
    cavity, is not a fold), on an even spread of face centroids, the samples shared by size; None when
    no component is closed. `comps`: `_by_size(v, f, 24)` if the caller has it. ponytail: faces × samples
    capped at 5e7, about 3 s (a 1M-face mesh gets 50 samples)."""
    v, f = np.asarray(v, np.float64), np.asarray(f, np.int64)
    comps = [fc for fc, closed in (comps or _by_size(v, f, 8))[:8] if closed]
    if not comps:
        return None
    eps = 1e-4 * float(np.linalg.norm(np.ptp(v, 0)))
    k = max(50, min(samples, int(5e7 // max(len(f), 1))))
    inside = total = 0
    for fc in comps:
        tri = v[fc]
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        if np.einsum("ij,ij->", tri[:, 0], n) < 0:  # 6 × the signed volume: wound inward, so flip
            n = -n
        ln = np.linalg.norm(n, axis=1)
        ok = np.flatnonzero(ln > 1e-18)
        want = max(20, k * len(fc) // len(f))
        pick = ok[np.unique(np.linspace(0, len(ok) - 1, min(want, len(ok))).astype(int))]
        nn = n[pick] / ln[pick, None]
        pts = tri[pick].mean(1) + eps * nn
        axis = np.abs(nn).argmax(1)
        sign = np.sign(nn[np.arange(len(pick)), axis]).astype(int)
        for ax in range(3):
            for sg in (-1, 1):
                sel = (axis == ax) & (sign == sg)
                if sel.any():
                    inside += int((_crossings(tri, pts[sel], ax, sg, eps / 2) % 2 == 1).sum())
        total += len(pick)
    return {
        "pct": round(100 * inside / max(total, 1), 2),
        "samples": total,
        "components": len(comps),
    }


def parts_inside(v, f, comps=None, samples: int = 100) -> dict | None:
    """The largest share of one component's vertices inside another closed component (after img2threejs's
    pairwise_penetration gate): 100 % is a shell inside a shell (the generator's inner copy of a torso), a
    few tens of % a helmet sunk into a head. Inside = odd crossings along X and along Y both, the hits
    nearer than a hair ignored, so a point on the other part's surface (a hat flush on a head) is not
    inside. The 24 largest components against the 8 largest closed ones whose boxes they overlap, only the
    points inside that box; None with one component. ponytail: faces × points capped at 5e7 over all pairs."""
    v, f = np.asarray(v, np.float64), np.asarray(f, np.int64)
    comps = (comps or _by_size(v, f, 24))[:24]
    if len(comps) < 2:
        return None
    eps = 1e-4 * float(np.linalg.norm(np.ptp(v, 0)))
    box = [(v[np.unique(fc)].min(0), v[np.unique(fc)].max(0)) for fc, _ in comps]
    pairs = [
        (a, b)
        for a in range(len(comps))
        for b, (fb, closed) in enumerate(comps[:8])
        if a != b and closed and (box[a][0] <= box[b][1]).all() and (box[a][1] >= box[b][0]).all()
    ]
    if not pairs:
        return {"pct": 0.0}
    per = max(10, min(samples, int(5e7 // sum(len(comps[b][0]) for _, b in pairs))))
    worst = 0.0
    for a, b in pairs:
        every = v[np.unique(comps[a][0])]
        inbox = every[
            ((every > box[b][0] + eps) & (every < box[b][1] - eps)).all(1)
        ]  # else outside
        if not len(inbox):
            continue
        pts = inbox[np.unique(np.linspace(0, len(inbox) - 1, min(per, len(inbox))).astype(int))]
        tri = v[comps[b][0]]
        odd = (_crossings(tri, pts, 0, 1, eps) % 2 == 1) & (
            _crossings(tri, pts, 1, 1, eps) % 2 == 1
        )
        worst = max(
            worst, 100 * odd.mean() * len(inbox) / len(every)
        )  # the sample's share, of the part
    return {"pct": round(float(worst), 1)}


def denoise_surface(
    vertices, faces, rounds: int = 10, moves: int = 10, sin2: float = 0.2
) -> np.ndarray:
    """Edge-aware denoising of a surface (bilateral normal filtering, then the vertices moved to fit; Zheng et al.
    2011, Sun et al. 2007): the generator's faceting, spikes and small bumps go, creases stay. Each round a face's
    normal becomes the area-weighted mean of its own and its edge neighbours', a neighbour weighted
    exp(-2(1 - cos θ) / sin2) by the angle θ between them (sin2 0.2: 15° 0.71, 30° 0.26, 60° 0.007); then `moves`
    times each vertex moves along its faces' filtered normals onto their planes. Winding need not agree (a
    neighbour's normal is turned to the face's side first). Returns the new vertices [V,3]; faces unchanged."""
    import torch

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    v = torch.tensor(np.asarray(vertices), dtype=torch.float64, device=dev)
    f = torch.tensor(np.asarray(faces), dtype=torch.long, device=dev)
    nf = len(f)
    # edge neighbours: faces sharing a sorted edge key
    e = torch.cat([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]).sort(dim=1).values
    key = e[:, 0] * len(v) + e[:, 1]
    owner = torch.arange(nf, device=dev).repeat(3)
    key, order = key.sort()
    owner = owner[order]
    same = key[1:] == key[:-1]
    fa, fb = owner[:-1][same], owner[1:][same]

    def normals():
        n = torch.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]], dim=1)
        area = n.norm(dim=1)
        return n / area.clamp_min(1e-30)[:, None], area

    n, area = normals()
    for _ in range(rounds):
        a, b = n[fa], n[fb]
        dot = (a * b).sum(1)
        w = torch.exp(-2 * (1 - dot.abs()) / sin2)
        sgn = torch.where(dot < 0, -1.0, 1.0).to(n.dtype)[:, None]
        acc = n * area[:, None]
        acc.index_add_(0, fa, (w * area[fb])[:, None] * b * sgn)
        acc.index_add_(0, fb, (w * area[fa])[:, None] * a * sgn)
        n = torch.nn.functional.normalize(acc, dim=1)
    count = torch.zeros(len(v), device=dev, dtype=v.dtype).index_add_(
        0, f.reshape(-1), torch.ones(nf * 3, device=dev, dtype=v.dtype)
    )
    for _ in range(moves):
        c = v[f].mean(1)
        step = torch.zeros_like(v)
        for k in range(3):
            d = ((c - v[f[:, k]]) * n).sum(1, keepdim=True) * n
            step.index_add_(0, f[:, k], d)
        v = v + step / count.clamp_min(1)[:, None]
    return v.cpu().numpy()
